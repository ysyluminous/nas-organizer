"""D5 企业级可靠性：断点续传 + CSV 全量 undo + 剧集季数识别。

设计约束（对应进度计划 #25 / #27 / #28）：
- 断点续传：执行器记录每个计划最后完成的 item；中断后重跑跳过已完成项，
  并对已复制文件重校验哈希（防中断时写坏）。
- CSV undo：op_logs 导出为可移植 CSV，可在另一实例导入并反向生成回滚计划。
- 剧集识别：S01E02 / 第01集 / EP02 三种命名解析为 剧名/Season 01/ 归档建议。
"""

from __future__ import annotations

import csv
import re
import time
from pathlib import Path
from typing import Any

from .config import Config
from .db import Database
from .models import OpLog, Plan, PlanItem


class ProgressStore:
    """计划执行进度（断点续传）：独立于 plan_items，记录最后完成位置。"""

    def __init__(self, db: Database):
        self.db = db

    def _ensure(self) -> None:
        self.db.connect().executescript(
            """CREATE TABLE IF NOT EXISTS plan_progress (
                   plan_id     INTEGER PRIMARY KEY,
                   last_done   INTEGER DEFAULT 0,
                   total       INTEGER DEFAULT 0,
                   updated_at  REAL NOT NULL);"""
        )
        self.db.connect().commit()

    def mark_done(self, plan_id: int, item_id: int, total: int = 0) -> None:
        self._ensure()
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO plan_progress (plan_id, last_done, total, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(plan_id) DO UPDATE SET
                     last_done=excluded.last_done,
                     total=MAX(excluded.total, plan_progress.total),
                     updated_at=excluded.updated_at""",
                (plan_id, item_id, total, time.time()),
            )

    def get(self, plan_id: int) -> dict[str, Any]:
        self._ensure()
        row = self.db.connect().execute(
            "SELECT * FROM plan_progress WHERE plan_id=?", (plan_id,)
        ).fetchone()
        if not row:
            return {"plan_id": plan_id, "last_done": 0, "total": 0, "updated_at": 0}
        return {"plan_id": int(row["plan_id"]), "last_done": int(row["last_done"]),
                "total": int(row["total"]), "updated_at": float(row["updated_at"])}

    def reset(self, plan_id: int) -> None:
        self._ensure()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM plan_progress WHERE plan_id=?", (plan_id,))


class CSVUndo:
    """操作日志 CSV 导出 / 导入，生成反向回滚计划（不改文件）。"""

    FIELDS = ["plan_item_id", "action", "src_path", "dst_path", "sha256", "size", "executed_at"]

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def export(self, path: str | Path, limit: int = 0) -> dict[str, Any]:
        ops = self.db.get_unrolled_ops()
        if limit > 0:
            ops = ops[:limit]
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=self.FIELDS)
            writer.writeheader()
            for op in ops:
                writer.writerow({
                    "plan_item_id": op.plan_item_id, "action": op.action,
                    "src_path": op.src_path, "dst_path": op.dst_path,
                    "sha256": op.sha256 or "", "size": op.size or 0,
                    "executed_at": op.executed_at,
                })
        return {"exported": len(ops), "path": str(p), "rolled_back_excluded": True}

    def import_plan(self, path: str | Path, name: str | None = None) -> dict[str, Any]:
        """从 CSV 生成回滚计划：把 dst_path 移回 src_path。不执行任何文件操作。"""
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"CSV 不存在: {p}")
        ops: list[dict[str, Any]] = []
        with p.open("r", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if row.get("dst_path") and row.get("src_path"):
                    ops.append(row)
        plan = Plan(name=name or f"csv-undo-{time.strftime('%Y%m%d-%H%M%S')}",
                    description=f"CSV 反向回滚，来源 {p.name}")
        plan.id = self.db.create_plan(plan)
        added = 0
        for row in ops:
            # file_id 必须指向真实文件行（外键约束）：按目标路径查，找不到则建占位记录
            fid = self._file_id_for(row["dst_path"])
            item = PlanItem(
                file_id=fid, action="move",
                src_path=row["dst_path"], dst_path=row["src_path"],
                reason=f"csv-undo:{row.get('action', '')}",
            )
            self.db.add_plan_item(plan.id, item)
            added += 1
        with self.db.transaction() as conn:
            conn.execute("UPDATE plans SET item_count=? WHERE id=?", (added, plan.id))
        return {"plan_id": plan.id, "name": plan.name, "items": added,
                "note": "仅生成回滚计划，需确认后执行"}

    def _file_id_for(self, path: str) -> int:
        """按路径复用文件行；跨实例导入时创建占位文件记录以满足外键。"""
        row = self.db.connect().execute(
            "SELECT id FROM files WHERE path=?", (path,)
        ).fetchone()
        if row:
            return int(row["id"])
        # 占位记录：标记为 archived，不影响 active 统计
        from .models import FileRecord
        rec = FileRecord(path=path, volume="csv-undo", filename=Path(path).name,
                         ext=Path(path).suffix.lower().lstrip("."), size=0,
                         mtime=0, atime=0, ctime=0)
        fid = self.db.upsert_file(rec)
        with self.db.transaction() as conn:
            conn.execute("UPDATE files SET status='archived' WHERE id=?", (fid,))
        return fid


# 剧集命名：S01E02 / 第01集 / EP02 / E02 / 1x02
_EPISODE_PATTERNS = [
    re.compile(r"\bS(?P<season>\d{1,2})[Ee](?P<ep>\d{1,3})\b"),
    re.compile(r"第\s*(?P<ep>\d{1,3})\s*集"),
    re.compile(r"\bEP?(?P<ep>\d{1,3})\b", re.I),
    re.compile(r"\b(?P<season>\d{1,2})x(?P<ep>\d{1,3})\b", re.I),
]


def parse_episode(filename: str) -> dict[str, Any] | None:
    """解析剧集文件名，返回 剧名/季/集；非剧集返回 None。"""
    stem = Path(filename).stem
    for pat in _EPISODE_PATTERNS:
        m = pat.search(stem)
        if not m:
            continue
        groups = m.groupdict()
        ep = int(groups.get("ep") or 0)
        if not ep:
            continue
        season = int(groups.get("season") or 1)
        # 剧名 = 去掉集数标记及常见分隔符
        title = stem[:m.start()].strip(" -_.·[]【】")
        title = re.sub(r"\bS\d{1,2}\s*$", "", title, flags=re.I).strip(" -_.·")
        if not title:
            title = "未命名剧集"
        return {"title": title, "season": season, "episode": ep,
                "season_dir": f"Season {season:02d}",
                "suggested_name": f"{title} - S{season:02d}E{ep:02d}{Path(filename).suffix}"}
    return None


class SeasonOrganizer:
    """按季数生成剧集归档建议（只读，不移动文件）。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def suggest(self, records: list[Any], target_root: str) -> list[dict[str, Any]]:
        root = Path(target_root)
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for rec in records:
            info = parse_episode(rec.filename)
            if not info:
                continue
            rel = f"{info['title']}/{info['season_dir']}/{info['suggested_name']}"
            dst = root / rel
            ctr = 0
            while str(dst).lower() in seen or dst.exists():
                ctr += 1
                dst = root / f"{Path(rel).stem}_{ctr}{Path(rel).suffix}"
            seen.add(str(dst).lower())
            out.append({
                "file_id": getattr(rec, "id", None), "src_path": rec.path,
                "dst_path": str(dst), "title": info["title"],
                "season": info["season"], "episode": info["episode"],
                "season_dir": info["season_dir"],
                "action": "move", "reason": "season-recognition",
            })
        return out
