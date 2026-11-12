"""重复检测：大小分组 → 头尾哈希 → 全量哈希三级漏斗."""

import hashlib
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable

from .config import Config
from .db import Database
from .models import FileRecord


class Deduper:
    """三级漏斗检测重复文件."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.head_size = int(config.get("dedupe.head_size", 65536))
        self.tail_size = int(config.get("dedupe.tail_size", 65536))
        self.workers = int(config.get("dedupe.workers", 2))
        self._progress: Callable[[str, int, int], None] | None = None

    def on_progress(self, callback: Callable[[str, int, int], None]) -> None:
        self._progress = callback

    def find_duplicates(self) -> dict:
        """执行三级检测，返回统计信息."""
        records = [r for r in self.db.get_all_active() if r.size > 0]
        print(f"[DEDUPE] 开始检测 {len(records)} 个文件")

        # 第一级：按大小分组
        by_size: dict[int, list[FileRecord]] = defaultdict(list)
        for r in records:
            by_size[r.size].append(r)
        candidates = [g for g in by_size.values() if len(g) > 1]
        size_candidates = sum(len(g) for g in candidates)
        print(f"[DEDUPE] 一级过滤后剩 {size_candidates} 个候选（{len(candidates)} 组）")
        if self._progress:
            self._progress("size", size_candidates, size_candidates)

        # 第二级：头尾哈希（逐文件读盘较慢，周期内上报细分进度）
        by_headtail: dict[tuple[str, str], list[FileRecord]] = defaultdict(list)
        ht_done = 0
        for group in candidates:
            for rec in group:
                try:
                    head, tail = self._edge_hashes(Path(rec.path))
                    self.db.update_hashes(rec.id, head=head, tail=tail)
                    by_headtail[(head, tail)].append(rec)
                except (OSError, PermissionError) as exc:
                    print(f"[WARN] 读文件失败 {rec.path}: {exc}")
                ht_done += 1
                if self._progress and ht_done % 500 == 0:
                    self._progress("headtail", ht_done, size_candidates)
        candidates2 = [g for g in by_headtail.values() if len(g) > 1]
        ht_candidates = sum(len(g) for g in candidates2)
        print(f"[DEDUPE] 二级过滤后剩 {ht_candidates} 个候选（{len(candidates2)} 组）")
        if self._progress:
            self._progress("headtail", size_candidates, size_candidates)

        # 第三级：全量哈希（周期上报细分进度）
        by_full: dict[str, list[FileRecord]] = defaultdict(list)
        full_total = sum(len(g) for g in candidates2)
        full_done = 0
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {}
            for group in candidates2:
                for rec in group:
                    futures[pool.submit(self._full_hash, Path(rec.path))] = rec
            for future in as_completed(futures):
                rec = futures[future]
                try:
                    digest = future.result()
                    self.db.update_hashes(rec.id, full=digest)
                    by_full[digest].append(rec)
                except (OSError, PermissionError) as exc:
                    print(f"[WARN] 全量哈希失败 {rec.path}: {exc}")
                full_done += 1
                if self._progress and full_done % 200 == 0:
                    self._progress("full", full_done, full_total)
        dup_groups = [g for g in by_full.values() if len(g) > 1]
        print(f"[DEDUPE] 三级过滤后确认 {len(dup_groups)} 组重复")
        if self._progress:
            self._progress("full", full_total, full_total)

        # 标记非主文件
        marked = 0
        with self.db.transaction() as conn:
            for group in dup_groups:
                keeper = self._pick_keeper(group)
                for rec in group:
                    if rec.id != keeper.id:
                        conn.execute(
                            "UPDATE files SET is_duplicate=1, duplicate_of=? WHERE id=?",
                            (keeper.id, rec.id),
                        )
                        marked += 1

        wasted = sum(
            sum(r.size for r in g[1:]) for g in dup_groups
        )
        return {
            "groups": len(dup_groups),
            "marked": marked,
            "wasted_bytes": wasted,
            "size_candidates": size_candidates,
            "headtail_candidates": ht_candidates,
        }

    def _edge_hashes(self, path: Path) -> tuple[str, str]:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = hashlib.sha256(f.read(self.head_size)).hexdigest()
            if size > self.head_size:
                f.seek(-min(self.tail_size, size), 2)
                tail = hashlib.sha256(f.read(self.tail_size)).hexdigest()
            else:
                tail = head
        return head, tail

    def _full_hash(self, path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _pick_keeper(group: list[FileRecord]) -> FileRecord:
        """选主文件：路径短优先 → mtime 新优先 → 文件名排序."""
        return sorted(
            group,
            key=lambda r: (len(r.path), -r.mtime, r.filename),
        )[0]
