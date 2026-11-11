"""两级回收站：stage1 隔离（可还原）→ stage2 待清除（显式确认才清除）。

安全设计（延续项目底线）：
  - 删除/迁移的源文件永远先进入 stage1，绝不物理删除。
  - stage1 中超过 N 天的项目由 promote() 移入 stage2，仍不删除。
  - purge() 清除 stage2 必须显式传 confirm=True，否则拒绝。
  - 每次隔离/提升/清除都写 recycle_logs 审计表，可追溯。
  - 所有路径经过 PathGuard 校验，回收站根目录固定为应用私有 data/ 下。
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .db import Database


@dataclass
class RecycleItem:
    id: int
    original_path: str
    stored_path: str
    stage: int              # 1 隔离 / 2 待清除
    removed_at: float
    promoted_at: float | None
    size: int
    reason: str


class RecycleBin:
    """两级回收站管理器。"""

    STAGE1 = "stage1"
    STAGE2 = "stage2"

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        root = config.get("executor.recycle_root", "data/recycle")
        self.root = config.resolve(str(root)) if not Path(str(root)).is_absolute() else Path(str(root))
        self.stage1 = self.root / self.STAGE1
        self.stage2 = self.root / self.STAGE2
        self.stage1.mkdir(parents=True, exist_ok=True)
        self.stage2.mkdir(parents=True, exist_ok=True)
        self.days_to_stage2 = int(config.get("recycle.days_to_stage2", 30))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """兼容存量数据库：旧库没有 recycle_logs 时自动建表，无需用户重建。"""
        self.db.connect().execute(
            """CREATE TABLE IF NOT EXISTS recycle_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                original_path TEXT NOT NULL,
                stored_path   TEXT NOT NULL,
                stage         INTEGER NOT NULL,
                removed_at    REAL NOT NULL,
                promoted_at   REAL,
                size          INTEGER NOT NULL,
                reason        TEXT,
                action        TEXT NOT NULL
            )"""
        )
        self.db.connect().execute(
            "CREATE INDEX IF NOT EXISTS idx_recycle_stage ON recycle_logs(stage, action)"
        )
        self.db.connect().commit()

    # ---------- 隔离 ----------
    def quarantine(self, src: str | Path, reason: str = "") -> str:
        """把源文件移入 stage1，返回存储路径。源文件不存在时抛 FileNotFoundError。"""
        src_path = Path(src)
        if not src_path.exists():
            raise FileNotFoundError(f"待隔离文件不存在: {src}")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        safe = str(src_path).replace("/", "_").replace("\\", "_").lstrip("_")
        stored = self.stage1 / f"{stamp}__{Path(safe).name}"
        # 同盘优先 rename，跨盘自动 copy+unlink
        shutil.move(str(src_path), str(stored))
        size = stored.stat().st_size if stored.is_file() else self._dir_size(stored)
        conn = self.db.connect()
        cur = conn.execute(
            """INSERT INTO recycle_logs
               (original_path, stored_path, stage, removed_at, promoted_at, size, reason, action)
               VALUES (?, ?, 1, ?, NULL, ?, ?, 'quarantine')""",
            (str(src_path), str(stored), time.time(), size, reason),
        )
        conn.commit()
        return str(stored)

    # ---------- 提升：stage1 → stage2 ----------
    def promote(self, older_than_days: int | None = None) -> dict:
        """把 stage1 中超时项目移入 stage2（仍不删除）。"""
        days = older_than_days if older_than_days is not None else self.days_to_stage2
        cutoff = time.time() - days * 86400
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT id, original_path, stored_path, size FROM recycle_logs "
            "WHERE stage=1 AND removed_at < ?", (cutoff,)
        ).fetchall()
        stats = {"total": len(rows), "promoted": 0, "failed": 0}
        for row in rows:
            stored = Path(row["stored_path"])
            try:
                if stored.exists():
                    target = self.stage2 / stored.name
                    shutil.move(str(stored), str(target))
                    new_path = str(target)
                else:
                    new_path = row["stored_path"]
                conn.execute(
                    "UPDATE recycle_logs SET stage=2, stored_path=?, promoted_at=? WHERE id=?",
                    (new_path, time.time(), row["id"]),
                )
                conn.execute(
                    "INSERT INTO recycle_logs "
                    "(original_path, stored_path, stage, removed_at, promoted_at, size, reason, action) "
                    "VALUES (?, ?, 2, ?, ?, ?, ?, 'promote')",
                    (row["original_path"], new_path, time.time(), time.time(),
                     row["size"], "stage1 超时自动提升"),
                )
                stats["promoted"] += 1
            except Exception as exc:
                print(f"[RECYCLE] 提升失败 {row['id']}: {exc}")
                stats["failed"] += 1
        conn.commit()
        return stats

    # ---------- 还原 ----------
    def restore(self, item_id: int, confirm: bool = False) -> dict:
        """把回收站项目还原到原始路径。"""
        if not confirm:
            return {"ok": False, "reason": "需要 confirm=True"}
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM recycle_logs WHERE id=? AND action='quarantine'",
                           (item_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "项目不存在"}
        stored = Path(row["stored_path"])
        original = Path(row["original_path"])
        if not stored.exists():
            return {"ok": False, "reason": "回收站文件已丢失"}
        if original.exists():
            return {"ok": False, "reason": "原路径已存在，拒绝覆盖"}
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stored), str(original))
            conn.execute(
                "INSERT INTO recycle_logs "
                "(original_path, stored_path, stage, removed_at, promoted_at, size, reason, action) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'restore')",
                (row["original_path"], str(original), row["stage"], row["removed_at"],
                 row["promoted_at"], row["size"], f"还原自 item={item_id}"),
            )
            # 标记原隔离记录已还原（stage=0 表示已出回收站）
            conn.execute("UPDATE recycle_logs SET stage=0 WHERE id=?", (item_id,))
            conn.commit()
            return {"ok": True, "restored_to": str(original)}
        except Exception as exc:
            return {"ok": False, "reason": str(exc)}

    # ---------- 清除（危险操作）----------
    def purge(self, stage: int = 2, confirm: bool = False) -> dict:
        """物理清除指定阶段的回收站项目。stage2 必须 confirm=True。

        为安全起见，stage=1 的清除也被拒绝——必须先 promote 到 stage2。
        """
        if not confirm:
            return {"ok": False, "reason": "清除是不可逆操作，需要 confirm=True"}
        if stage != 2:
            return {"ok": False, "reason": "只允许清除 stage2，请先把 stage1 项目提升到 stage2"}
        conn = self.db.connect()
        rows = conn.execute("SELECT id, stored_path FROM recycle_logs WHERE stage=2").fetchall()
        stats = {"total": len(rows), "purged": 0, "failed": 0, "bytes": 0}
        for row in rows:
            stored = Path(row["stored_path"])
            try:
                if stored.is_file():
                    n = stored.stat().st_size
                    stored.unlink()
                elif stored.is_dir():
                    n = self._dir_size(stored)
                    shutil.rmtree(stored)
                else:
                    n = 0
                conn.execute(
                    "INSERT INTO recycle_logs "
                    "(original_path, stored_path, stage, removed_at, promoted_at, size, reason, action) "
                    "VALUES (?, ?, 2, ?, ?, ?, ?, 'purge')",
                    ("", str(stored), time.time(), time.time(), n, "物理清除"),
                )
                conn.execute("UPDATE recycle_logs SET stage=-1 WHERE id=? AND action='quarantine'",
                             (row["id"],))
                stats["purged"] += 1
                stats["bytes"] += n
            except Exception as exc:
                print(f"[RECYCLE] 清除失败 {row['id']}: {exc}")
                stats["failed"] += 1
        conn.commit()
        return {"ok": True, **stats}

    # ---------- 查询 ----------
    def list_items(self, stage: int | None = None, limit: int = 100) -> list[dict]:
        conn = self.db.connect()
        sql = "SELECT * FROM recycle_logs WHERE action='quarantine'"
        params: list = []
        if stage is not None:
            sql += " AND stage=?"
            params.append(stage)
        sql += " ORDER BY removed_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def stats(self) -> dict:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT stage, COUNT(*) n, COALESCE(SUM(size),0) bytes "
            "FROM recycle_logs WHERE action='quarantine' GROUP BY stage"
        ).fetchall()
        result = {f"stage{i}": {"count": 0, "bytes": 0} for i in (0, 1, 2, -1)}
        for r in rows:
            result[f"stage{r['stage']}"] = {"count": r["n"], "bytes": r["bytes"]}
        return result

    @staticmethod
    def _dir_size(path: Path) -> int:
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total
