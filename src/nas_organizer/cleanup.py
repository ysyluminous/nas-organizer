"""12 类清理发现器：只读扫描产出候选清单，绝不自动删除。

工作流：discover() 输出候选 → 用户确认 → 生成 plan → 走现有 Executor 安全执行。
每个发现器是纯函数式扫描，可被单测独立验证；默认只发现不清理。

12 类：
  1  empty_file       空文件（0 字节）
  2  empty_dir        空目录
  3  temp_file        .tmp/.temp/~$*/.DS_Store/Thumbs.db
  4  partial_download .part/.crdownload/.!ut/.aria2
  5  torrent          .torrent 种子
  6  installer        .exe/.msi/.dmg/.apk 安装包
  7  old_archive      超过 N 天的压缩包
  8  thumbnail_cache  @eaDir/.thumbnails/.cache 缩略图
  9  old_log          .log 且超过 N 天
  10 backup_version   xxx (1).ext / xxx-副本.ext 版本副本
  11 duplicate_name   同目录内 "xxx (1).ext" 疑似重复命名
  12 stale_download   下载目录中超过 N 天未访问的大文件
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .db import Database
from .security import PathGuard, PathViolation

CLEANUP_KINDS = [
    "empty_file", "empty_dir", "temp_file", "partial_download", "torrent",
    "installer", "old_archive", "thumbnail_cache", "old_log",
    "backup_version", "duplicate_name", "stale_download",
]

TEMP_PATTERNS = (".tmp", ".temp", ".swp", ".bak")
THUMB_DIRS = {"@eaDir", ".thumbnails", ".cache", "__thumbnails__", ".@__thumb"}
PARTIAL_SUFFIXES = (".part", ".crdownload", ".!ut", ".aria2", ".download")
INSTALLER_SUFFIXES = (".exe", ".msi", ".dmg", ".apk", ".deb", ".rpm", ".pkg")
ARCHIVE_SUFFIXES = (".zip", ".rar", ".7z", ".tar", ".gz", ".xz", ".bz2")
DUPLICATE_RE = re.compile(r"^(.*?)\s*[\(（](\d+)[\)）]\s*$")
COPY_RE = re.compile(r"^(.*?)\s*[-–]\s*(副本|copy|备份)\s*$", re.I)


@dataclass
class CleanupCandidate:
    kind: str
    path: str
    size: int
    mtime: float
    reason: str


@dataclass
class CleanupReport:
    scanned_dirs: int = 0
    candidates: list[CleanupCandidate] = field(default_factory=list)

    def by_kind(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for c in self.candidates:
            counts[c.kind] = counts.get(c.kind, 0) + 1
        return counts

    def total_bytes(self) -> int:
        return sum(c.size for c in self.candidates)


class CleanupFinder:
    """只读发现器：扫描目录产出清理候选，不写任何文件。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.guard = PathGuard(config)
        self.old_days = int(config.get("cleanup.old_days", 90))
        self.stale_days = int(config.get("cleanup.stale_days", 180))
        self.download_dirs = [Path(p) for p in config.get("cleanup.download_dirs", [])]
        self.exclude_dirs = set(config.get("scanner.exclude_dirs", [])) | {".git"}

    def discover(self, roots: list[str | Path], kinds: list[str] | None = None) -> CleanupReport:
        """扫描给定根目录，返回候选报告。kinds=None 表示全部 12 类。"""
        active = set(kinds) if kinds else set(CLEANUP_KINDS)
        unknown = active - set(CLEANUP_KINDS)
        if unknown:
            raise ValueError(f"未知清理类型: {sorted(unknown)}")
        report = CleanupReport()
        for root in roots:
            try:
                safe_root = self.guard.check(str(root), source="cleanup", action="discover")
            except PathViolation:
                continue
            if not safe_root.exists():
                continue
            report.scanned_dirs += 1
            self._scan_root(safe_root, active, report)
        return report

    def _scan_root(self, root: Path, active: set[str], report: CleanupReport) -> None:
        now = time.time()
        for dirpath, dirnames, filenames in __import__("os").walk(root):
            dirs_to_skip = []
            for d in dirnames:
                if d in self.exclude_dirs:
                    dirs_to_skip.append(d)
                    continue
                full = Path(dirpath) / d
                if d in THUMB_DIRS and "thumbnail_cache" in active:
                    size = self._dir_size(full)
                    report.candidates.append(CleanupCandidate(
                        "thumbnail_cache", str(full), size, full.stat().st_mtime,
                        "缩略图缓存目录"))
                    dirs_to_skip.append(d)
                    continue
                if "empty_dir" in active and not any(full.iterdir()):
                    report.candidates.append(CleanupCandidate(
                        "empty_dir", str(full), 0, full.stat().st_mtime, "空目录"))
            dirnames[:] = [d for d in dirnames if d not in dirs_to_skip]

            for fn in filenames:
                full = Path(dirpath) / fn
                try:
                    st = full.stat()
                except OSError:
                    continue
                age_days = (now - st.st_mtime) / 86400
                low = fn.lower()

                if "empty_file" in active and st.st_size == 0:
                    report.candidates.append(CleanupCandidate(
                        "empty_file", str(full), 0, st.st_mtime, "0 字节空文件"))
                if "temp_file" in active and (
                    low.endswith(TEMP_PATTERNS) or low.startswith("~$")
                    or low in ("thumbs.db", ".ds_store")
                ):
                    report.candidates.append(CleanupCandidate(
                        "temp_file", str(full), st.st_size, st.st_mtime, "临时/系统文件"))
                if "partial_download" in active and low.endswith(PARTIAL_SUFFIXES):
                    report.candidates.append(CleanupCandidate(
                        "partial_download", str(full), st.st_size, st.st_mtime, "未完成下载"))
                if "torrent" in active and low.endswith(".torrent"):
                    report.candidates.append(CleanupCandidate(
                        "torrent", str(full), st.st_size, st.st_mtime, "种子文件"))
                if "installer" in active and low.endswith(INSTALLER_SUFFIXES):
                    report.candidates.append(CleanupCandidate(
                        "installer", str(full), st.st_size, st.st_mtime, "安装包"))
                if "old_archive" in active and low.endswith(ARCHIVE_SUFFIXES) and age_days >= self.old_days:
                    report.candidates.append(CleanupCandidate(
                        "old_archive", str(full), st.st_size, st.st_mtime,
                        f"压缩包超过 {self.old_days} 天"))
                if "old_log" in active and low.endswith(".log") and age_days >= self.old_days:
                    report.candidates.append(CleanupCandidate(
                        "old_log", str(full), st.st_size, st.st_mtime,
                        f"日志超过 {self.old_days} 天"))
                if "stale_download" in active and self._is_download(full) and age_days >= self.stale_days:
                    report.candidates.append(CleanupCandidate(
                        "stale_download", str(full), st.st_size, st.st_mtime,
                        f"下载目录文件超过 {self.stale_days} 天"))
                if "duplicate_name" in active and DUPLICATE_RE.match(Path(fn).stem):
                    report.candidates.append(CleanupCandidate(
                        "duplicate_name", str(full), st.st_size, st.st_mtime,
                        "疑似重复命名（如 xxx (1).ext）"))
                if "backup_version" in active and COPY_RE.match(Path(fn).stem):
                    report.candidates.append(CleanupCandidate(
                        "backup_version", str(full), st.st_size, st.st_mtime,
                        "疑似版本副本（如 xxx-副本.ext）"))

    def _is_download(self, path: Path) -> bool:
        for d in self.download_dirs:
            try:
                if d in path.parents:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        try:
            for p in path.rglob("*"):
                if p.is_file():
                    total += p.stat().st_size
        except OSError:
            pass
        return total
