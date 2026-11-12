"""每日重复文件定时检测 + 报告运行时.

设计要点（对应用户诉求「重复文件应该默认自动执行，每天出一个报告」）：
- 默认开启：auto_daily=True，无需用户配置 schedule.yaml 即可每天自动查重。
- 每天一份报告：报告写入 SQLite 表 dedupe_reports，以 report_date 为唯一键，
  同一天重复执行只覆盖当天报告，历史按天保留，可查趋势。
- 安全：查重是只读操作（Deduper 只更新索引库的 hash/is_duplicate 标记，
  绝不改动用户文件），因此可以放心默认自动执行。
- 设置持久化到 data/dedupe_settings.json（不碰只读 config.yaml）。
- 进程内单例 + 后台守护线程，Web 与调度共享同一运行态（线程安全）。
- 启动时若「已过执行时间且今天还没报告」，会补跑一次，确保部署当天即有报告。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import Config
from .db import Database

SETTINGS_REL = "data/dedupe_settings.json"
DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_daily": True,      # 默认自动执行
    "run_hour": 3,           # 每天 03:00 后首次检查触发（低峰时段）
    "retention_days": 30,    # 报告保留天数
    "top_n": 50,             # 报告内保留的重复组 Top N
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS dedupe_reports (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date   TEXT    NOT NULL UNIQUE,
    generated_at  REAL    NOT NULL,
    status        TEXT    NOT NULL,
    scanned_files INTEGER DEFAULT 0,
    groups        INTEGER DEFAULT 0,
    dup_files     INTEGER DEFAULT 0,
    wasted_bytes  INTEGER DEFAULT 0,
    duration_sec  REAL    DEFAULT 0,
    top_groups    TEXT,
    error         TEXT
);
CREATE INDEX IF NOT EXISTS idx_dedupe_reports_date ON dedupe_reports(report_date);
"""


def _settings_path(config: Config) -> Path:
    return config.resolve(SETTINGS_REL)


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def load_settings(config: Config) -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    p = _settings_path(config)
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in DEFAULT_SETTINGS:
                    if key in loaded:
                        data[key] = loaded[key]
        except (OSError, ValueError):
            pass
    data["auto_daily"] = bool(data["auto_daily"])
    data["run_hour"] = _clamp_int(data["run_hour"], 0, 23, 3)
    data["retention_days"] = _clamp_int(data["retention_days"], 1, 365, 30)
    data["top_n"] = _clamp_int(data["top_n"], 1, 500, 50)
    return data


def save_settings(config: Config, settings: dict[str, Any]) -> dict[str, Any]:
    clean = load_settings(config)
    if "auto_daily" in settings:
        clean["auto_daily"] = bool(settings["auto_daily"])
    if "run_hour" in settings:
        clean["run_hour"] = _clamp_int(settings["run_hour"], 0, 23, 3)
    if "retention_days" in settings:
        clean["retention_days"] = _clamp_int(settings["retention_days"], 1, 365, 30)
    if "top_n" in settings:
        clean["top_n"] = _clamp_int(settings["top_n"], 1, 500, 50)
    p = _settings_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".dedupe-settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, str(p))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return clean


class DedupeRuntime:
    """线程安全的每日查重运行时单例。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict[str, Any] = self._empty_state()
        self._thread: threading.Thread | None = None
        self._config: Config | None = None
        self._db: Database | None = None
        self._sched_stop = threading.Event()
        self._sched_thread: threading.Thread | None = None
        self._triggered_date: str = ""   # 本进程已触发过的日期，避免重复

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "status": "idle",            # idle | running | done | error
            "stage": "",                 # size | headtail | full
            "count": 0,
            "total": 0,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "last_report_date": None,
        }

    # ---------- schema ----------

    def ensure_schema(self, db: Database) -> None:
        conn = db.connect()
        conn.executescript(SCHEMA)
        conn.commit()

    # ---------- 查重控制 ----------

    def start(self, config: Config, db: Database, manual: bool = False) -> dict[str, Any]:
        """后台启动一次查重并生成当日报告，立即返回；进度用 progress() 轮询。"""
        with self._lock:
            if self._state["status"] == "running":
                return {"started": False, "reason": "已有查重正在运行"}
            self.ensure_schema(db)
            self._state = self._empty_state()
            self._state.update({"status": "running", "started_at": time.time()})
            self._config, self._db = config, db
            self._thread = threading.Thread(
                target=self._run, args=(config, db, manual),
                daemon=True, name="nas-dedupe",
            )
            self._thread.start()
            return {"started": True}

    def _run(self, config: Config, db: Database, manual: bool) -> None:
        started = time.time()
        report_date = datetime.now().strftime("%Y-%m-%d")
        try:
            from .dedupe import Deduper

            def progress_cb(stage: str, count: int, total: int) -> None:
                with self._lock:
                    self._state["stage"] = stage
                    self._state["count"] = int(count)
                    self._state["total"] = int(total)

            deduper = Deduper(config, db)
            deduper.on_progress(progress_cb)
            stats = deduper.find_duplicates()

            # 聚合重复组 Top N 明细（当日快照，写入报告）
            settings = load_settings(config)
            top_groups = self._top_groups(db, settings["top_n"])
            scanned = db.connect().execute(
                "SELECT COUNT(*) AS n FROM files WHERE status='active'"
            ).fetchone()["n"]

            self._save_report(db, {
                "report_date": report_date,
                "generated_at": time.time(),
                "status": "ok",
                "scanned_files": int(scanned),
                "groups": int(stats.get("groups", 0)),
                "dup_files": int(stats.get("marked", 0)),
                "wasted_bytes": int(stats.get("wasted_bytes", 0)),
                "duration_sec": round(time.time() - started, 1),
                "top_groups": json.dumps(top_groups, ensure_ascii=False),
                "error": None,
            })
            self._prune(db, settings["retention_days"])
            with self._lock:
                self._state["status"] = "done"
                self._state["stage"] = "full"
                self._state["last_report_date"] = report_date
                self._state["finished_at"] = time.time()
        except Exception as exc:  # 后台线程必须捕获一切
            err = str(exc)[:500]
            try:
                self._save_report(db, {
                    "report_date": report_date,
                    "generated_at": time.time(),
                    "status": "failed",
                    "duration_sec": round(time.time() - started, 1),
                    "top_groups": "[]",
                    "error": err,
                })
            except Exception:
                pass
            with self._lock:
                self._state["status"] = "error"
                self._state["error"] = err
                self._state["finished_at"] = time.time()
            print(f"[ERROR] 每日查重失败: {exc}")

    @staticmethod
    def _top_groups(db: Database, top_n: int) -> list[dict[str, Any]]:
        """从索引库聚合重复组 Top N，作为当日报告快照。"""
        conn = db.connect()
        rows = conn.execute(
            """SELECT full_hash, COUNT(*) AS n, SUM(size) AS wasted
               FROM files WHERE full_hash IS NOT NULL AND status='active'
               GROUP BY full_hash HAVING n > 1 ORDER BY wasted DESC LIMIT ?""",
            (top_n,),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            members = conn.execute(
                """SELECT path, size, mtime FROM files
                   WHERE full_hash=? AND status='active'
                   ORDER BY size DESC LIMIT 10""",
                (row["full_hash"],),
            ).fetchall()
            result.append({
                "count": int(row["n"]),
                "wasted_bytes": int(row["wasted"] or 0),
                "files": [
                    {"path": m["path"], "size": int(m["size"] or 0),
                     "mtime": float(m["mtime"] or 0)}
                    for m in members
                ],
            })
        return result

    @staticmethod
    def _save_report(db: Database, report: dict[str, Any]) -> None:
        """写入当日报告；同一天重复执行则覆盖（ON CONFLICT）。"""
        conn = db.connect()
        conn.execute(
            """INSERT INTO dedupe_reports
               (report_date, generated_at, status, scanned_files, groups, dup_files,
                wasted_bytes, duration_sec, top_groups, error)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(report_date) DO UPDATE SET
                 generated_at=excluded.generated_at, status=excluded.status,
                 scanned_files=excluded.scanned_files, groups=excluded.groups,
                 dup_files=excluded.dup_files, wasted_bytes=excluded.wasted_bytes,
                 duration_sec=excluded.duration_sec, top_groups=excluded.top_groups,
                 error=excluded.error""",
            (
                report["report_date"], report["generated_at"], report["status"],
                report.get("scanned_files", 0), report.get("groups", 0),
                report.get("dup_files", 0), report.get("wasted_bytes", 0),
                report.get("duration_sec", 0), report.get("top_groups", "[]"),
                report.get("error"),
            ),
        )
        conn.commit()

    @staticmethod
    def _prune(db: Database, retention_days: int) -> None:
        """清理超过保留天数的报告。"""
        cutoff = datetime.now().timestamp() - retention_days * 86400
        conn = db.connect()
        conn.execute("DELETE FROM dedupe_reports WHERE generated_at < ?", (cutoff,))
        conn.commit()

    def progress(self) -> dict[str, Any]:
        with self._lock:
            s = dict(self._state)
        if s["status"] == "running" and s["started_at"]:
            s["elapsed_sec"] = round(time.time() - float(s["started_at"]), 1)
        else:
            s["elapsed_sec"] = (
                round(float(s["finished_at"]) - float(s["started_at"]), 1)
                if s.get("finished_at") and s.get("started_at") else 0
            )
        return s

    # ---------- 报告查询 ----------

    @staticmethod
    def reports(db: Database, limit: int = 30) -> list[dict[str, Any]]:
        conn = db.connect()
        rows = conn.execute(
            """SELECT id, report_date, generated_at, status, scanned_files, groups,
                      dup_files, wasted_bytes, duration_sec, error
               FROM dedupe_reports ORDER BY report_date DESC LIMIT ?""",
            (max(1, min(365, int(limit))),),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def report_detail(db: Database, report_id: int) -> dict[str, Any] | None:
        conn = db.connect()
        row = conn.execute(
            "SELECT * FROM dedupe_reports WHERE id=?", (report_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["top_groups"] = json.loads(d.get("top_groups") or "[]")
        except (ValueError, TypeError):
            d["top_groups"] = []
        return d

    @staticmethod
    def latest_report(db: Database) -> dict[str, Any] | None:
        conn = db.connect()
        row = conn.execute(
            "SELECT id, report_date, generated_at, status, groups, dup_files, "
            "wasted_bytes FROM dedupe_reports ORDER BY report_date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ---------- 每日自动调度 ----------

    def ensure_scheduler(self, config: Config, db: Database) -> None:
        """由 api.setup 调用；幂等。守护线程每分钟检查是否到点触发。"""
        with self._lock:
            if self._sched_thread and self._sched_thread.is_alive():
                return
            self.ensure_schema(db)
            self._config, self._db = config, db
            self._sched_stop.clear()
            self._sched_thread = threading.Thread(
                target=self._sched_loop, daemon=True, name="nas-dedupe-daily",
            )
            self._sched_thread.start()

    def _sched_loop(self) -> None:
        # 启动后稍等再首次检查，避免与 Web 启动争用
        if self._sched_stop.wait(20.0):
            return
        while not self._sched_stop.wait(60.0):
            cfg, db = self._config, self._db
            if cfg is None or db is None:
                continue
            settings = load_settings(cfg)
            if not settings["auto_daily"]:
                continue
            now = datetime.now()
            if now.hour < settings["run_hour"]:
                continue
            date_key = now.strftime("%Y-%m-%d")
            # 本进程今天已触发过 → 跳过
            if self._triggered_date == date_key:
                continue
            # 今天已有成功报告 → 跳过（例如手动已生成）
            try:
                has_today = db.connect().execute(
                    "SELECT 1 FROM dedupe_reports WHERE report_date=? AND status='ok' LIMIT 1",
                    (date_key,),
                ).fetchone()
            except Exception:
                has_today = None
            if has_today:
                self._triggered_date = date_key
                continue
            with self._lock:
                if self._state["status"] == "running":
                    continue
                self._triggered_date = date_key
                self.start(cfg, db, manual=False)
            print(f"[dedupe-daily] 触发每日查重 {date_key}")

    def stop_scheduler(self) -> None:
        self._sched_stop.set()


# 全局单例：Web 进程内所有路由共享同一运行态
runtime = DedupeRuntime()
