"""SQLite 数据库管理."""

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .models import FileRecord, OpLog, Plan, PlanItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT    NOT NULL UNIQUE,
    volume          TEXT    NOT NULL,
    filename        TEXT    NOT NULL,
    ext             TEXT,
    size            INTEGER NOT NULL,
    mtime           REAL    NOT NULL,
    atime           REAL    NOT NULL,
    ctime           REAL    NOT NULL,
    head_hash       TEXT,
    tail_hash       TEXT,
    full_hash       TEXT,
    ftype           TEXT,
    domain          TEXT,
    temp            TEXT,
    confidence      REAL DEFAULT 1.0,
    classified_by   TEXT,
    is_duplicate    INTEGER DEFAULT 0,
    duplicate_of    INTEGER,
    status          TEXT DEFAULT 'active',
    scanned_at      REAL    NOT NULL,
    FOREIGN KEY (duplicate_of) REFERENCES files(id)
);
CREATE INDEX IF NOT EXISTS idx_files_path     ON files(path);
CREATE INDEX IF NOT EXISTS idx_files_size     ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_fullhash ON files(full_hash);
CREATE INDEX IF NOT EXISTS idx_files_ftype    ON files(ftype);
CREATE INDEX IF NOT EXISTS idx_files_domain   ON files(domain);
CREATE INDEX IF NOT EXISTS idx_files_temp     ON files(temp);
CREATE INDEX IF NOT EXISTS idx_files_mtime    ON files(mtime);

CREATE TABLE IF NOT EXISTS tags (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    color TEXT
);
CREATE TABLE IF NOT EXISTS file_tags (
    file_id INTEGER NOT NULL,
    tag_id  INTEGER NOT NULL,
    PRIMARY KEY (file_id, tag_id),
    FOREIGN KEY (file_id) REFERENCES files(id),
    FOREIGN KEY (tag_id)  REFERENCES tags(id)
);

CREATE TABLE IF NOT EXISTS plans (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    created_at  REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    item_count  INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS plan_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id     INTEGER NOT NULL,
    file_id     INTEGER NOT NULL,
    action      TEXT NOT NULL,
    src_path    TEXT NOT NULL,
    dst_path    TEXT,
    reason      TEXT,
    status      TEXT DEFAULT 'pending',
    error       TEXT,
    FOREIGN KEY (plan_id) REFERENCES plans(id),
    FOREIGN KEY (file_id) REFERENCES files(id)
);
CREATE INDEX IF NOT EXISTS idx_plan_items_plan   ON plan_items(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_items_status ON plan_items(status);

CREATE TABLE IF NOT EXISTS op_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_item_id INTEGER NOT NULL,
    action       TEXT NOT NULL,
    src_path     TEXT NOT NULL,
    dst_path     TEXT NOT NULL,
    sha256       TEXT,
    size         INTEGER,
    executed_at  REAL NOT NULL,
    rolled_back  INTEGER DEFAULT 0,
    FOREIGN KEY (plan_item_id) REFERENCES plan_items(id)
);

CREATE TABLE IF NOT EXISTS scan_state (
    volume       TEXT PRIMARY KEY,
    last_scan_at REAL NOT NULL,
    file_count   INTEGER NOT NULL,
    status       TEXT NOT NULL
);

-- 定时调度审计（独立于 op_logs：调度任务无 plan_item_id，不污染文件操作日志）
CREATE TABLE IF NOT EXISTS scheduler_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name     TEXT NOT NULL,
    command      TEXT NOT NULL,
    status       TEXT NOT NULL,
    detail       TEXT,
    started_at   REAL NOT NULL,
    duration_sec REAL NOT NULL
);

-- 两级回收站审计：stage 1=隔离(可还原) 2=待清除 0=已还原 -1=已清除
CREATE TABLE IF NOT EXISTS recycle_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    original_path TEXT NOT NULL,
    stored_path   TEXT NOT NULL,
    stage         INTEGER NOT NULL,
    removed_at    REAL NOT NULL,
    promoted_at   REAL,
    size          INTEGER NOT NULL,
    reason        TEXT,
    action        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recycle_stage ON recycle_logs(stage, action);

-- D4 人工反馈学习：关键词 → 归属域/类型的权重，仅调整低置信度分类
CREATE TABLE IF NOT EXISTS feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT NOT NULL,
    field       TEXT NOT NULL,   -- domain / ftype
    value       TEXT NOT NULL,
    hits        INTEGER DEFAULT 1,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    UNIQUE (keyword, field, value)
);
CREATE INDEX IF NOT EXISTS idx_feedback_field ON feedback(field, hits);
"""


class Database:
    """SQLite 数据库封装."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def init_schema(self) -> None:
        conn = self.connect()
        conn.executescript(SCHEMA)
        conn.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ---- files ----

    def upsert_file(self, record: FileRecord) -> int:
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO files
                   (path, volume, filename, ext, size, mtime, atime, ctime, scanned_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                     size=excluded.size, mtime=excluded.mtime, atime=excluded.atime,
                     ctime=excluded.ctime, scanned_at=excluded.scanned_at,
                     status='active'
                """,
                (
                    record.path, record.volume, record.filename, record.ext,
                    record.size, record.mtime, record.atime, record.ctime, time.time(),
                ),
            )
            row = conn.execute("SELECT id FROM files WHERE path=?", (record.path,)).fetchone()
            return int(row["id"])

    def get_file(self, file_id: int) -> FileRecord | None:
        row = self.connect().execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return self._row_to_file(row) if row else None

    def update_classification(
        self, file_id: int, ftype: str, domain: str, temp: str,
        confidence: float, classified_by: str,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE files SET ftype=?, domain=?, temp=?, confidence=?, classified_by=?
                   WHERE id=?""",
                (ftype, domain, temp, confidence, classified_by, file_id),
            )

    def update_hashes(
        self, file_id: int, head: str | None = None,
        tail: str | None = None, full: str | None = None,
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                """UPDATE files SET head_hash=COALESCE(?, head_hash),
                   tail_hash=COALESCE(?, tail_hash), full_hash=COALESCE(?, full_hash)
                   WHERE id=?""",
                (head, tail, full, file_id),
            )

    def get_all_active(self) -> list[FileRecord]:
        rows = self.connect().execute(
            "SELECT * FROM files WHERE status='active'"
        ).fetchall()
        return [self._row_to_file(r) for r in rows]

    def count_files(self) -> int:
        row = self.connect().execute(
            "SELECT COUNT(*) AS n FROM files WHERE status='active'"
        ).fetchone()
        return int(row["n"])

    # ---- plans ----

    def create_plan(self, plan: Plan) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO plans (name, description, created_at, status) VALUES (?, ?, ?, ?)",
                (plan.name, plan.description, time.time(), plan.status),
            )
            return int(cur.lastrowid)

    def add_plan_item(self, plan_id: int, item: PlanItem) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO plan_items
                   (plan_id, file_id, action, src_path, dst_path, reason, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_id, item.file_id, item.action, item.src_path,
                    item.dst_path, item.reason, item.status,
                ),
            )
            return int(cur.lastrowid)

    def get_plan(self, plan_id: int) -> Plan | None:
        conn = self.connect()
        row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if not row:
            return None
        items = conn.execute(
            "SELECT * FROM plan_items WHERE plan_id=?", (plan_id,)
        ).fetchall()
        return Plan(
            id=int(row["id"]),
            name=row["name"],
            description=row["description"] or "",
            status=row["status"],
            item_count=int(row["item_count"] or 0),
            items=[
                PlanItem(
                    id=int(i["id"]), plan_id=int(i["plan_id"]), file_id=int(i["file_id"]),
                    action=i["action"], src_path=i["src_path"], dst_path=i["dst_path"],
                    reason=i["reason"] or "", status=i["status"], error=i["error"],
                )
                for i in items
            ],
        )

    # ---- op logs ----

    def log_op(self, log: OpLog) -> int:
        with self.transaction() as conn:
            cur = conn.execute(
                """INSERT INTO op_logs
                   (plan_item_id, action, src_path, dst_path, sha256, size, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    log.plan_item_id, log.action, log.src_path, log.dst_path,
                    log.sha256, log.size, log.executed_at or time.time(),
                ),
            )
            return int(cur.lastrowid)

    def get_unrolled_ops(self) -> list[OpLog]:
        rows = self.connect().execute(
            "SELECT * FROM op_logs WHERE rolled_back=0 ORDER BY id DESC"
        ).fetchall()
        return [
            OpLog(
                id=int(r["id"]), plan_item_id=int(r["plan_item_id"]),
                action=r["action"], src_path=r["src_path"], dst_path=r["dst_path"],
                sha256=r["sha256"], size=r["size"],
                executed_at=float(r["executed_at"]), rolled_back=bool(r["rolled_back"]),
            )
            for r in rows
        ]

    def mark_rolled_back(self, log_id: int) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE op_logs SET rolled_back=1 WHERE id=?", (log_id,))

    # ---- tags / 虚拟目录（D5 多维归类，不移动源文件）----

    def ensure_tag(self, name: str, color: str | None = None) -> int:
        """取得标签 ID，不存在则创建。返回 tag id。"""
        name = name.strip()
        if not name:
            raise ValueError("标签名不能为空")
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tags (name, color) VALUES (?, ?)",
                (name, color),
            )
            row = conn.execute("SELECT id FROM tags WHERE name=?", (name,)).fetchone()
            return int(row["id"])

    def tag_file(self, file_id: int, tag: str, color: str | None = None) -> None:
        """给文件打标签（幂等）；源文件零改动。"""
        tid = self.ensure_tag(tag, color)
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO file_tags (file_id, tag_id) VALUES (?, ?)",
                (file_id, tid),
            )

    def untag_file(self, file_id: int, tag: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                """DELETE FROM file_tags WHERE file_id=? AND tag_id IN
                   (SELECT id FROM tags WHERE name=?)""",
                (file_id, tag),
            )

    def list_tags(self) -> list[dict]:
        rows = self.connect().execute(
            """SELECT t.id, t.name, t.color, COUNT(ft.file_id) AS file_count
               FROM tags t LEFT JOIN file_tags ft ON ft.tag_id=t.id
               GROUP BY t.id ORDER BY file_count DESC, t.name"""
        ).fetchall()
        return [{"id": int(r["id"]), "name": r["name"], "color": r["color"],
                 "file_count": int(r["file_count"])} for r in rows]

    def get_file_tags(self, file_id: int) -> list[str]:
        rows = self.connect().execute(
            """SELECT t.name FROM tags t JOIN file_tags ft ON ft.tag_id=t.id
               WHERE ft.file_id=? ORDER BY t.name""",
            (file_id,),
        ).fetchall()
        return [r["name"] for r in rows]

    def files_by_tag(self, tag: str, limit: int = 100, offset: int = 0) -> list[dict]:
        """按标签查文件（可多视角：同一文件可属多个标签）。"""
        rows = self.connect().execute(
            """SELECT f.* FROM files f
               JOIN file_tags ft ON ft.file_id=f.id
               JOIN tags t ON t.id=ft.tag_id
               WHERE t.name=? AND f.status='active'
               ORDER BY f.path LIMIT ? OFFSET ?""",
            (tag, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_tag(self, tag: str) -> int:
        """删除标签及其所有关联（不动文件）。返回清理的关联数。"""
        with self.transaction() as conn:
            row = conn.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()
            if not row:
                return 0
            tid = int(row["id"])
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM file_tags WHERE tag_id=?", (tid,)
            ).fetchone()["n"]
            conn.execute("DELETE FROM file_tags WHERE tag_id=?", (tid,))
            conn.execute("DELETE FROM tags WHERE id=?", (tid,))
            return int(count)

    # ---- feedback（D4 反馈学习）----

    def add_feedback(self, keyword: str, field: str, value: str) -> None:
        """记录一次人工纠正；重复关键词命中次数 +1。field ∈ {domain, ftype}."""
        if field not in ("domain", "ftype"):
            raise ValueError("field 必须是 domain 或 ftype")
        now = time.time()
        with self.transaction() as conn:
            conn.execute(
                """INSERT INTO feedback (keyword, field, value, hits, created_at, updated_at)
                   VALUES (?, ?, ?, 1, ?, ?)
                   ON CONFLICT(keyword, field, value) DO UPDATE SET
                     hits = hits + 1, updated_at = excluded.updated_at""",
                (keyword.lower().strip(), field, value, now, now),
            )

    def get_feedback(self, field: str | None = None, limit: int = 200) -> list[dict]:
        conn = self.connect()
        if field:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE field=? ORDER BY hits DESC, updated_at DESC LIMIT ?",
                (field, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM feedback ORDER BY hits DESC, updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": int(r["id"]), "keyword": r["keyword"], "field": r["field"],
                "value": r["value"], "hits": int(r["hits"]),
                "created_at": float(r["created_at"]), "updated_at": float(r["updated_at"]),
            }
            for r in rows
        ]

    def reset_feedback(self) -> int:
        """清空全部反馈（可重置），返回删除条数。"""
        with self.transaction() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"]
            conn.execute("DELETE FROM feedback")
            return int(count)

    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> FileRecord:
        return FileRecord(
            id=int(row["id"]), path=row["path"], volume=row["volume"],
            filename=row["filename"], ext=row["ext"] or "", size=int(row["size"]),
            mtime=float(row["mtime"]), atime=float(row["atime"]), ctime=float(row["ctime"]),
            head_hash=row["head_hash"], tail_hash=row["tail_hash"],
            full_hash=row["full_hash"], ftype=row["ftype"], domain=row["domain"],
            temp=row["temp"], confidence=float(row["confidence"] or 1.0),
            classified_by=row["classified_by"], is_duplicate=bool(row["is_duplicate"]),
            duplicate_of=row["duplicate_of"], status=row["status"],
        )
