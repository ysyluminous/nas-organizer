"""增量文件扫描器."""

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .config import Config
from .db import Database
from .models import FileRecord
from .security import PathGuard, PathViolation


class Scanner:
    """遍历挂载目录，采集文件元数据并写入索引."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.exclude_dirs = set(config.get("scanner.exclude_dirs", []))
        self.workers = int(config.get("scanner.workers", 4))
        self.guard = PathGuard(config)
        self._progress: Callable[[int, int], None] | None = None

    def on_progress(self, callback: Callable[[int, int], None]) -> None:
        self._progress = callback

    def scan_all(self) -> dict[str, int]:
        """扫描所有配置的卷，返回各卷扫描文件数."""
        self.db.init_schema()
        results: dict[str, int] = {}
        for vol in self.config.get("volumes", []):
            name = vol["name"]
            try:
                root = self.guard.check(
                    vol["path"], source="scanner", action="scan"
                )
            except PathViolation as exc:
                print(f"[安全] 跳过越界扫描源: {exc}", flush=True)
                continue
            if not root.exists():
                print(f"[WARN] 卷 {name} 路径不存在: {root}，跳过")
                continue
            count = self.scan_volume(name, root)
            results[name] = count
            with self.db.transaction() as conn:
                conn.execute(
                    """INSERT INTO scan_state (volume, last_scan_at, file_count, status)
                       VALUES (?, ?, ?, 'done')
                       ON CONFLICT(volume) DO UPDATE SET
                         last_scan_at=excluded.last_scan_at,
                         file_count=excluded.file_count, status='done'""",
                    (name, time.time(), count),
                )
        return results

    def scan_volume(self, volume: str, root: Path) -> int:
        """扫描单个卷."""
        print(f"[SCAN] 开始扫描 {volume}: {root}")
        paths: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in self.exclude_dirs]
            for fn in filenames:
                paths.append(Path(dirpath) / fn)

        total = len(paths)
        print(f"[SCAN] {volume} 发现 {total} 个文件")
        scanned = 0
        batch: list[FileRecord] = []

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._stat_file, p, volume): p for p in paths}
            for future in as_completed(futures):
                try:
                    record = future.result()
                    if record is not None:
                        batch.append(record)
                except Exception as exc:
                    print(f"[WARN] 读取失败 {futures[future]}: {exc}")
                scanned += 1
                if self._progress:
                    self._progress(scanned, total)
                if len(batch) >= 500:
                    self._flush(batch)
                    batch.clear()

        if batch:
            self._flush(batch)

        print(f"[SCAN] {volume} 完成，入库 {scanned} 个文件")
        return scanned

    def _stat_file(self, path: Path, volume: str) -> FileRecord | None:
        try:
            st = path.stat()
        except (OSError, PermissionError):
            return None
        return FileRecord(
            path=str(path),
            volume=volume,
            filename=path.name,
            ext=path.suffix.lower().lstrip("."),
            size=int(st.st_size),
            mtime=float(st.st_mtime),
            atime=float(st.st_atime),
            ctime=float(st.st_ctime),
        )

    def _flush(self, batch: list[FileRecord]) -> None:
        with self.db.transaction() as conn:
            conn.executemany(
                """INSERT INTO files
                   (path, volume, filename, ext, size, mtime, atime, ctime, scanned_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                     size=excluded.size, mtime=excluded.mtime, atime=excluded.atime,
                     ctime=excluded.ctime, scanned_at=excluded.scanned_at, status='active'""",
                [
                    (
                        r.path, r.volume, r.filename, r.ext, r.size,
                        r.mtime, r.atime, r.ctime, time.time(),
                    )
                    for r in batch
                ],
            )
