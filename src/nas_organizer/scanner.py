"""增量文件扫描器.

- 全量扫描：遍历所有文件并 upsert 索引。
- 增量扫描：仅处理 mtime 晚于该卷上次扫描时间的新增/变更文件（新增文件自动入库）。
- 进度回调：on_progress((done, total, volume)) 兼容旧的两参数回调。
"""

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
        self._progress: Callable | None = None
        self._volume_cb: Callable[[str, str], None] | None = None
        self._phase: str = "scanning"

    def on_progress(self, callback: Callable) -> None:
        """兼容旧的两参数回调 (done, total)，也支持三/四参数 (done, total, volume, phase)。"""
        self._progress = callback

    def on_volume(self, callback: Callable[[str, str], None]) -> None:
        """卷阶段回调：callback(volume, 'start'|'done')。"""
        self._volume_cb = callback

    def _emit(self, done: int, total: int, volume: str) -> None:
        if not self._progress:
            return
        try:
            self._progress(done, total, volume, self._phase)  # 新式四参数
        except TypeError:
            try:
                self._progress(done, total, volume)  # 三参数
            except TypeError:
                self._progress(done, total)  # 旧式两参数兼容

    @staticmethod
    def _last_scan_at(conn, volume: str) -> float:
        row = conn.execute(
            "SELECT last_scan_at FROM scan_state WHERE volume=?", (volume,)
        ).fetchone()
        return float(row["last_scan_at"]) if row else 0.0

    def scan_all(
        self,
        incremental: bool = False,
        progress_cb: Callable | None = None,
        volume_cb: Callable[[str, str], None] | None = None,
    ) -> dict[str, int]:
        """扫描所有当前生效的卷（含 Web 动态绑定），返回各卷扫描文件数.

        incremental=True 时，只处理 mtime > 上次扫描时间的新增/变更文件。
        """
        from .mounts import effective_volumes
        if progress_cb:
            self._progress = progress_cb
        if volume_cb:
            self._volume_cb = volume_cb
        self.db.init_schema()
        results: dict[str, int] = {}
        for vol in effective_volumes(self.config):
            name = vol["name"]
            try:
                root = self.guard.check(
                    vol["path"], source="scanner", action="scan"
                )
            except PathViolation as exc:
                print(f"[安全] 跳过越界扫描源: {exc}", flush=True)
                continue
            if not root.exists():
                print(f"[WARN] 卷 {name} 路径不存在: {root}，跳过", flush=True)
                continue
            if self._volume_cb:
                self._volume_cb(name, "start")
            count = self.scan_volume(name, root, incremental=incremental)
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
            if self._volume_cb:
                self._volume_cb(name, "done")
        return results

    def scan_volume(self, volume: str, root: Path, incremental: bool = False) -> int:
        """扫描单个卷。增量模式仅处理新增/变更文件。"""
        since = 0.0
        if incremental:
            conn = self.db.connect()
            since = self._last_scan_at(conn, volume)
        mode = f"增量(自 {time.strftime('%H:%M:%S', time.localtime(since))})" if since else ("增量(首次，等同全量)" if incremental else "全量")
        print(f"[SCAN] 开始扫描 {volume}: {root} [{mode}]", flush=True)

        # 阶段一：遍历目录树。大盘遍历可能很慢，按目录数上报进度，避免页面显示 0/0 卡死。
        self._phase = "discovering"
        paths: list[Path] = []
        walked_dirs = 0
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in self.exclude_dirs]
            for fn in filenames:
                paths.append(Path(dirpath) / fn)
            walked_dirs += 1
            if walked_dirs % 200 == 0:
                self._emit(len(paths), 0, volume)

        total = len(paths)
        print(f"[SCAN] {volume} 发现 {total} 个文件（遍历 {walked_dirs} 个目录）", flush=True)
        # 阶段二：逐文件 stat + 入库
        self._phase = "scanning"
        scanned = 0
        written = 0
        batch: list[FileRecord] = []

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(self._stat_file, p, volume): p for p in paths}
            for future in as_completed(futures):
                try:
                    record = future.result()
                    if record is not None:
                        # 增量过滤：mtime 未变化且之前已入库的文件跳过写入
                        if incremental and record.mtime <= since:
                            scanned += 1
                            self._emit(scanned, total, volume)
                            continue
                        batch.append(record)
                        written += 1
                except Exception as exc:
                    print(f"[WARN] 读取失败 {futures[future]}: {exc}", flush=True)
                scanned += 1
                self._emit(scanned, total, volume)
                if len(batch) >= 500:
                    self._flush(batch)
                    batch.clear()

        if batch:
            self._flush(batch)

        print(
            f"[SCAN] {volume} 完成，扫描 {scanned}，入库 {written} 个文件", flush=True
        )
        return written if incremental else scanned

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
