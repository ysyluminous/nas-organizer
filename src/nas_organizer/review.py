"""D6 Reviewer 复核台：集中低置信度分类与冲突计划，人工裁决写回反馈。

设计约束（对应进度计划 #30）：
- 只读产出待复核队列；裁决是显式写操作，改的是索引库分类与 feedback 表，
  绝不移动/删除用户文件。
- 裁决同时做两件事：① 立即修正当前文件分类；② 写 feedback 让同类文件以后自动归类。
- 有完整裁决历史（谁把什么改成什么），可审计、可追溯。
"""

from __future__ import annotations

import time
from typing import Any

from .config import Config
from .db import Database
from .feedback import FeedbackLearner


class ReviewQueue:
    """待复核队列 + 人工裁决。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.threshold = float(config.get("review.threshold", 0.8))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.db.connect().executescript(
            """CREATE TABLE IF NOT EXISTS review_decisions (
                   id           INTEGER PRIMARY KEY AUTOINCREMENT,
                   file_id      INTEGER NOT NULL,
                   path         TEXT NOT NULL,
                   field        TEXT NOT NULL,
                   old_value    TEXT,
                   new_value    TEXT NOT NULL,
                   note         TEXT,
                   decided_at   REAL NOT NULL);"""
        )
        self.db.connect().commit()

    # ---------- 只读队列 ----------

    def low_confidence(self, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """SELECT id, path, filename, ext, ftype, domain, temp, confidence, classified_by
               FROM files WHERE status='active' AND confidence < ?
               ORDER BY confidence ASC, id ASC LIMIT ? OFFSET ?""",
            (self.threshold, limit, offset),
        ).fetchall()
        return [
            {"id": int(r["id"]), "path": r["path"], "filename": r["filename"],
             "ext": r["ext"], "ftype": r["ftype"], "domain": r["domain"],
             "temp": r["temp"], "confidence": float(r["confidence"] or 0),
             "classified_by": r["classified_by"], "reason": "low_confidence"}
            for r in rows
        ]

    def plan_conflicts(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            """SELECT pi.id, pi.file_id, pi.src_path, pi.dst_path, pi.reason, pi.error,
                      f.path, f.ftype, f.domain, f.confidence
               FROM plan_items pi LEFT JOIN files f ON f.id=pi.file_id
               WHERE pi.status='failed' OR pi.error IS NOT NULL
               ORDER BY pi.id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [
            {"plan_item_id": int(r["id"]), "file_id": r["file_id"],
             "path": r["path"] or r["src_path"], "src_path": r["src_path"],
             "dst_path": r["dst_path"], "error": r["error"],
             "ftype": r["ftype"], "domain": r["domain"],
             "confidence": float(r["confidence"] or 0) if r["confidence"] is not None else 0,
             "reason": "plan_conflict"}
            for r in rows
        ]

    def queue(self, limit: int = 100, offset: int = 0,
              kind: str = "all") -> dict[str, Any]:
        """统一待复核队列：低置信度分类 + 冲突计划。"""
        low = self.low_confidence(limit, offset) if kind in ("all", "low_confidence") else []
        conflicts = self.plan_conflicts(limit) if kind in ("all", "plan_conflict") else []
        return {"threshold": self.threshold, "low_confidence": low,
                "plan_conflicts": conflicts,
                "total": len(low) + len(conflicts), "kind": kind}

    # ---------- 写：人工裁决 ----------

    def decide(self, file_id: int, field: str, value: str,
               note: str | None = None, learn: bool = True) -> dict[str, Any]:
        """裁决一个文件的 domain 或 ftype；立即修正 + 写反馈 + 记审计。

        field ∈ {domain, ftype}；learn=False 时只改当前文件、不写 feedback。
        """
        if field not in ("domain", "ftype"):
            raise ValueError("field 必须是 domain 或 ftype")
        rec = self.db.get_file(file_id)
        if not rec:
            raise ValueError(f"文件 {file_id} 不存在")
        old = getattr(rec, field, None)

        # 1. 立即修正当前文件分类
        ftype = value if field == "ftype" else (rec.ftype or "其他")
        domain = value if field == "domain" else (rec.domain or "资源")
        self.db.update_classification(file_id, ftype, domain, rec.temp, 1.0, "manual")

        # 2. 写 feedback，让同类文件以后自动归类（规则仍优先）
        keywords = 0
        if learn:
            keywords = FeedbackLearner(self.config, self.db).learn_from_correction(
                rec.path, field, value)

        # 3. 裁决审计
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO review_decisions
                   (file_id, path, field, old_value, new_value, note, decided_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (file_id, rec.path, field, old, value, note, time.time()),
            )
        return {"ok": True, "file_id": file_id, "field": field,
                "old_value": old, "new_value": value,
                "keywords_learned": keywords, "note": "规则优先，反馈只影响低置信度"}

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.connect().execute(
            "SELECT * FROM review_decisions ORDER BY decided_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {"id": int(r["id"]), "file_id": int(r["file_id"]), "path": r["path"],
             "field": r["field"], "old_value": r["old_value"],
             "new_value": r["new_value"], "note": r["note"],
             "decided_at": float(r["decided_at"])}
            for r in rows
        ]

    def stats(self) -> dict[str, Any]:
        conn = self.db.connect()
        low = conn.execute(
            "SELECT COUNT(*) AS n FROM files WHERE status='active' AND confidence < ?",
            (self.threshold,),
        ).fetchone()["n"]
        conflicts = conn.execute(
            "SELECT COUNT(*) AS n FROM plan_items WHERE status='failed' OR error IS NOT NULL"
        ).fetchone()["n"]
        decisions = conn.execute("SELECT COUNT(*) AS n FROM review_decisions").fetchone()["n"]
        return {"low_confidence": int(low), "plan_conflicts": int(conflicts),
                "decisions_made": int(decisions), "threshold": self.threshold}
