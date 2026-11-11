"""D4 媒体元数据感知：照片 exif 日期归档、音乐 ID3 标签整理。

设计约束（对应进度计划 #19 / #20）：
- 只读提取元数据 + 生成「建议目标名」，绝不移动/改写用户文件；
  真正落地仍走 Planner → confirm → Executor 的既有链路。
- 可选依赖（Pillow / mutagen）缺失或标签损坏时优雅降级：
  照片回落 mtime，音乐回落原文件名，主流程绝不报错。
- 不覆盖已有文件：目标存在时自动追加 _1 / _2 … 后缀。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from .config import Config
from .models import FileRecord

# 可选依赖：缺失时置 None，所有调用点都有降级分支
try:
    from PIL import Image, ExifTags  # type: ignore
except ImportError:  # pragma: no cover - 取决于部署镜像
    Image = None
    ExifTags = None

try:
    from mutagen import File as MutagenFile  # type: ignore
except ImportError:  # pragma: no cover
    MutagenFile = None


# exif 中拍摄时间的候选标签（DateTimeOriginal 优先）
_EXIF_DATE_TAGS = ("DateTimeOriginal", "DateTimeDigitized", "DateTime")


def _sanitize(text: str) -> str:
    """清理文件系统不友好字符，避免路径注入与非法命名。"""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(text)).strip()
    return text[:80] or "unknown"


def exif_date(path: str | Path) -> str | None:
    """读取照片 exif 拍摄日期，返回 'YYYY-MM-DD'；无 exif/依赖时返回 None。"""
    if Image is None:
        return None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            # DateTimeOriginal 常位于 ExifIFD 子字典
            ifd = tag_map.get("ExifOffset")
            if ifd and hasattr(ifd, "get"):
                for k, v in ifd.items():
                    tag_map.setdefault(ExifTags.TAGS.get(k, k), v)
            for tag in _EXIF_DATE_TAGS:
                raw = tag_map.get(tag)
                if raw:
                    m = re.search(r"(\d{4}):(\d{2}):(\d{2})", str(raw))
                    if m:
                        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    except Exception:
        # 图片损坏/格式不支持：交给上层回落 mtime
        return None
    return None


def photo_date(path: str | Path, mtime: float, exif_enabled: bool = True) -> tuple[str, str]:
    """返回 (日期 'YYYY-MM-DD', 来源 'exif' | 'mtime')。"""
    if exif_enabled:
        shot = exif_date(path)
        if shot:
            return shot, "exif"
    return time.strftime("%Y-%m-%d", time.localtime(mtime)), "mtime"


def music_tags(path: str | Path) -> dict[str, str] | None:
    """读取音乐 ID3/容器标签；无依赖或无标签时返回 None。"""
    if MutagenFile is None:
        return None
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None or not getattr(audio, "tags", None):
            return None
        tags = audio.tags

        def first(key: str) -> str:
            val = tags.get(key)
            if isinstance(val, (list, tuple)) and val:
                return str(val[0])
            return str(val) if val else ""

        result = {
            "artist": first("artist"),
            "album": first("album"),
            "title": first("title"),
            "tracknumber": first("tracknumber"),
        }
        if not any(result.values()):
            return None
        return result
    except Exception:
        return None


def music_dest_name(filename: str, tags: dict[str, str] | None) -> tuple[str, str]:
    """根据标签生成 'Artist/Album/Track - Title.ext'；无标签回退文件名规则。

    返回 (相对路径, 来源 'id3' | 'filename')。
    """
    ext = Path(filename).suffix
    if not tags:
        stem = Path(filename).stem
        # 常见 '歌手 - 标题' 命名直接拆成两层
        parts = re.split(r"\s+-\s+", stem, maxsplit=1)
        if len(parts) == 2:
            return f"{_sanitize(parts[0])}/{_sanitize(parts[1])}{ext}", "filename"
        return f"{_sanitize(stem)}{ext}", "filename"
    artist = _sanitize(tags.get("artist") or "未知歌手")
    album = _sanitize(tags.get("album") or "未命名专辑")
    title = _sanitize(tags.get("title") or Path(filename).stem)
    track = re.match(r"(\d+)", tags.get("tracknumber", ""))
    prefix = f"{int(track.group(1)):02d} - " if track else ""
    return f"{artist}/{album}/{prefix}{title}{ext}", "id3"


def _unique_path(target_root: Path, rel: str, taken: set[str]) -> Path:
    """目标存在或本批次已占用时追加 _1/_2，避免覆盖。"""
    candidate = target_root / rel
    ctr = 0
    while str(candidate).lower() in taken or candidate.exists():
        ctr += 1
        stem, ext = Path(rel).stem, Path(rel).suffix
        candidate = target_root / f"{stem}_{ctr}{ext}"
    taken.add(str(candidate).lower())
    return candidate


class MediaOrganizer:
    """生成照片/音乐的只读整理建议清单（不移动文件）。"""

    def __init__(self, config: Config):
        self.config = config
        self.exif_enabled = bool(config.get("media.exif_enabled", True))
        self.id3_enabled = bool(config.get("media.id3_enabled", True))

    def suggest_photos(self, records: list[FileRecord], target_root: str) -> list[dict[str, Any]]:
        """按拍摄日期生成 YYYY/MM/YYYY-MM-DD_原名 的建议清单。"""
        root = Path(target_root)
        taken: set[str] = set()
        out: list[dict[str, Any]] = []
        for rec in records:
            if (rec.ftype or "") != "图片":
                continue
            date, source = photo_date(rec.path, rec.mtime, self.exif_enabled)
            year, month = date[:4], date[5:7]
            rel = f"{year}/{month}/{date}_{Path(rec.filename).name}"
            dst = _unique_path(root, rel, taken)
            if str(dst) == rec.path:
                continue
            out.append({
                "file_id": rec.id, "src_path": rec.path, "dst_path": str(dst),
                "date": date, "source": source, "action": "move",
                "reason": f"photo-date:{source}",
            })
        return out

    def suggest_music(self, records: list[FileRecord], target_root: str) -> list[dict[str, Any]]:
        """按 ID3 标签（或文件名）生成 歌手/专辑/曲目 的建议清单。"""
        root = Path(target_root)
        taken: set[str] = set()
        out: list[dict[str, Any]] = []
        for rec in records:
            if (rec.ftype or "") != "音乐":
                continue
            tags = music_tags(rec.path) if self.id3_enabled else None
            rel, source = music_dest_name(rec.filename, tags)
            dst = _unique_path(root, rel, taken)
            if str(dst) == rec.path:
                continue
            out.append({
                "file_id": rec.id, "src_path": rec.path, "dst_path": str(dst),
                "source": source, "action": "move",
                "reason": f"music-tag:{source}",
            })
        return out

    @staticmethod
    def capability() -> dict[str, bool]:
        """暴露可选依赖是否可用，供 health / MCP 查询。"""
        return {"exif": Image is not None, "id3": MutagenFile is not None}
