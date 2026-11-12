"""每日查重定时 + 报告 单元测试.

直接运行：python tests/test_dedupe_daily.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config  # noqa: E402
from nas_organizer.db import Database  # noqa: E402
from nas_organizer.dedupe_runtime import (  # noqa: E402
    DEFAULT_SETTINGS, DedupeRuntime, load_settings, save_settings,
)
from nas_organizer.models import FileRecord  # noqa: E402


def build(base: Path):
    data = base / "data"
    data.mkdir(parents=True, exist_ok=True)
    cfg = Config({
        "database": {"path": str(data / "db.sqlite")},
        "volumes": [],
    }, base_dir=base)
    db = Database(cfg.resolve(cfg.get("database.path")))
    db.init_schema()
    return cfg, db


def add_file(db, path, size=100, full_hash="h"):
    fid = db.upsert_file(FileRecord(
        path=path, volume="v", filename=Path(path).name, ext=".txt",
        size=size, mtime=time.time(), atime=time.time(), ctime=time.time(),
    ))
    db.update_hashes(fid, full=full_hash)
    return fid


def check_all():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        base = Path(d)
        cfg, db = build(base)

        # 1. 默认配置：默认开启、每天 3 点、保留 30 天
        s = load_settings(cfg)
        assert s["auto_daily"] is True, "每日查重应默认开启"
        assert s["run_hour"] == 3 and s["retention_days"] == 30
        print("1. 默认开启每日查重 OK")

        # 2. 设置读写 + 边界钳制
        save_settings(cfg, {"auto_daily": False, "run_hour": 99, "retention_days": 0, "top_n": 5})
        s2 = load_settings(cfg)
        assert s2["auto_daily"] is False
        assert s2["run_hour"] == 23, "run_hour 应钳制到 23"
        assert s2["retention_days"] == 1, "retention_days 应钳制到 1"
        assert s2["top_n"] == 5
        save_settings(cfg, {"auto_daily": True, "run_hour": 3})
        print("2. 设置读写与边界钳制 OK")

        # 3. schema 建表
        rt = DedupeRuntime()
        rt.ensure_schema(db)
        cols = [r["name"] for r in db.connect().execute("PRAGMA table_info(dedupe_reports)").fetchall()]
        for c in ("report_date", "status", "groups", "wasted_bytes", "top_groups"):
            assert c in cols, f"缺列 {c}"
        print("3. 报告表结构 OK")

        # 4. 报告写入 + 同日覆盖（一天一份）
        add_file(db, "/a/1.txt", 100, "hash1")
        add_file(db, "/b/1.txt", 100, "hash1")
        add_file(db, "/c/2.txt", 500, "hash2")
        top = DedupeRuntime._top_groups(db, 50)
        assert len(top) == 1 and top[0]["count"] == 2 and len(top[0]["files"]) == 2, top
        report = {
            "report_date": "2026-11-12", "generated_at": time.time(), "status": "ok",
            "scanned_files": 3, "groups": 1, "dup_files": 1, "wasted_bytes": 100,
            "duration_sec": 1.5, "top_groups": __import__("json").dumps(top), "error": None,
        }
        DedupeRuntime._save_report(db, report)
        DedupeRuntime._save_report(db, {**report, "groups": 9, "wasted_bytes": 999})
        reports = rt.reports(db, 10)
        assert len(reports) == 1, "同一天应只有一份报告"
        assert reports[0]["groups"] == 9, "同日重复写入应覆盖"
        detail = rt.report_detail(db, reports[0]["id"])
        assert detail["top_groups"][0]["count"] == 2
        print("4. 报告写入 / 同日覆盖 / 明细查询 OK")

        # 5. 最新报告 + 清理过期
        DedupeRuntime._save_report(db, {**report, "report_date": "2026-11-11", "generated_at": time.time() - 40 * 86400})
        latest = rt.latest_report(db)
        assert latest["report_date"] == "2026-11-12", latest
        DedupeRuntime._prune(db, 30)
        assert len(rt.reports(db, 10)) == 1, "超期报告应被清理"
        print("5. 最新报告 / 过期清理 OK")

        # 6. top_n 限制
        assert len(DedupeRuntime._top_groups(db, 1)) == 1
        print("6. Top N 限制 OK")

        db.close()
        print(f"\n每日查重测试全部通过（默认配置：{DEFAULT_SETTINGS}）")


if __name__ == "__main__":
    check_all()
