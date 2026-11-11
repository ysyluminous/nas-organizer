"""D6 可选外部工具集成：FFmpeg 视频关键帧 + rclone 远端索引。

设计约束（对应进度计划 #29 / #32 / #34）：
- 不引入 pip / 系统依赖：ffmpeg、rclone 由用户自行安装，缺失时能力检测返回 false，
  所有调用点优雅降级、主流程零中断。
- 服务端只接受结构化参数：自然语言意图由客户端 Agent 解析（见 MCP intent_schema），
  服务端不接 LLM、不存任何 API 密钥。
- rclone 只读：仅执行 `lsjson`，绝不调用 copy/sync/delete；路径仍受 PathGuard 约束
  （远端路径单独校验为 rclone remote:name 形式，不做本地路径逃逸）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .config import Config
from .db import Database
from .models import FileRecord
from .perceptual import phash, hamming

VIDEO_EXTS = {"mp4", "mkv", "avi", "mov", "rmvb", "wmv", "flv", "webm", "m4v", "ts"}


def capability() -> dict[str, bool]:
    """外部工具是否可用（用于 health / MCP）。"""
    return {
        "ffmpeg": shutil.which("ffmpeg") is not None,
        "rclone": shutil.which("rclone") is not None,
    }


class VideoKeyframeFinder:
    """FFmpeg 抽帧 + pHash 比对相似视频（只读）。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.threshold = int(config.get("perceptual.video_threshold", 12))
        self.frames = int(config.get("perceptual.video_frames", 3))
        self.enabled = bool(config.get("perceptual.video_enabled", True))

    def _frame_hashes(self, path: str) -> list[int] | None:
        """用 ffmpeg 抽取 N 帧缩略图到 stdout（image2pipe），逐帧算 pHash。

        ffmpeg 缺失/执行失败/超时返回 None；单个文件失败不影响整批。
        """
        if shutil.which("ffmpeg") is None:
            return None
        out: list[int] = []
        for idx in range(self.frames):
            cmd = [
                "ffmpeg", "-v", "error", "-ss", str(idx), "-i", path,
                "-frames:v", "1", "-vf", "scale=64:64", "-f", "image2pipe",
                "-vcodec", "png", "-",
            ]
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=15)
                if proc.returncode != 0 or not proc.stdout:
                    continue
                # pHash 接受路径；这里把帧缓存到临时文件
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                    tf.write(proc.stdout)
                    tmp = tf.name
                try:
                    h = phash(tmp)
                    if h is not None:
                        out.append(h)
                finally:
                    Path(tmp).unlink(missing_ok=True)
            except (subprocess.TimeoutExpired, OSError):
                continue
        return out or None

    def find_similar(self, records: list[FileRecord] | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "groups": []}
        if shutil.which("ffmpeg") is None:
            return {"enabled": True, "available": False, "groups": [],
                    "note": "缺少 FFmpeg，视频关键帧比对已跳过（不影响主流程）"}
        records = records if records is not None else self.db.get_all_active()
        videos = [
            r for r in records
            if r.id and (r.ext or "").lower().lstrip(".") in VIDEO_EXTS
            and Path(r.path).exists()
        ]
        hashes: dict[int, list[int]] = {}
        for rec in videos:
            hs = self._frame_hashes(rec.path)
            if hs:
                hashes[rec.id] = hs

        def dist(a: list[int], b: list[int]) -> int:
            n = min(len(a), len(b))
            return sum(hamming(a[i], b[i]) for i in range(n)) // max(n, 1)

        clusters: list[list[FileRecord]] = []
        items = [r for r in videos if r.id in hashes]
        for rec in items:
            placed = False
            for cluster in clusters:
                if any(dist(hashes[rec.id], hashes[c.id]) <= self.threshold for c in cluster):
                    cluster.append(rec)
                    placed = True
                    break
            if not placed:
                clusters.append([rec])
        groups = [
            {"paths": [r.path for r in c], "file_ids": [r.id for r in c],
             "wasted_bytes": sum(r.size for r in c[1:])}
            for c in clusters if len(c) > 1
        ]
        groups.sort(key=lambda g: g["wasted_bytes"], reverse=True)
        return {"enabled": True, "available": True, "videos_scanned": len(videos),
                "hashed": len(hashes), "groups": groups, "similar_groups": len(groups)}


class RcloneIndexer:
    """rclone lsjson 只读远端索引（不复制、不同步、不删除）。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    @staticmethod
    def _valid_remote(remote: str) -> bool:
        """remote 必须是 name:path 形式，禁止任意本地路径/参数注入。"""
        if ":" not in remote:
            return False
        name, _, rest = remote.partition(":")
        if not name or any(ch in name for ch in " \t\r\n/\\"):
            return False
        if any(ch in rest for ch in "\x00\r\n"):
            return False
        return True

    def available(self) -> bool:
        return shutil.which("rclone") is not None

    def lsjson(self, remote: str, timeout: int = 60) -> list[dict[str, Any]]:
        """列出远端文件（只读 lsjson），返回条目列表。失败返回空列表。"""
        if not self.available():
            return []
        if not self._valid_remote(remote):
            raise ValueError("remote 必须是 'name:path' 形式（rclone 配置名）")
        try:
            proc = subprocess.run(
                ["rclone", "lsjson", remote, "--no-modtime"],
                capture_output=True, timeout=timeout,
            )
            if proc.returncode != 0:
                return []
            data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
            return [e for e in data if isinstance(e, dict) and not e.get("IsDir")]
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError, ValueError):
            return []

    def to_records(self, remote: str, volume: str = "remote",
                   limit: int = 0) -> list[FileRecord]:
        """把 lsjson 结果转为 FileRecord（不写库、不下载文件，仅供只读索引/预览）。"""
        entries = self.lsjson(remote)
        records: list[FileRecord] = []
        for e in entries:
            path = str(e.get("Path", ""))
            if not path:
                continue
            size = int(e.get("Size") or 0)
            name = e.get("Name") or Path(path).name
            mt = e.get("ModTime")
            mtime = 0.0
            if mt:
                from datetime import datetime
                try:
                    mtime = datetime.fromisoformat(str(mt).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    mtime = 0.0
            records.append(FileRecord(
                path=f"{remote}/{path}", volume=volume, filename=name,
                ext=Path(name).suffix.lower().lstrip("."), size=size,
                mtime=mtime, atime=mtime, ctime=mtime,
            ))
            if limit and len(records) >= limit:
                break
        return records
