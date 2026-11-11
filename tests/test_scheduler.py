"""定时调度器测试：cron 解析、默认关闭、危险命令拦截、触发与审计."""

import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.scheduler import Scheduler, cron_matches, parse_cron


def test_cron_parse():
    r = parse_cron("*/5 0-6 1,15 * 1")
    assert 0 in r["minute"] and 5 in r["minute"] and 59 not in r["minute"]
    assert set(r["hour"]) == set(range(7))
    assert r["day"] == {1, 15}
    assert 1 in r["weekday"]
    assert len(r["month"]) == 12


def test_cron_matches():
    assert cron_matches("0 3 * * *", datetime(2026, 11, 11, 3, 0))
    assert not cron_matches("0 3 * * *", datetime(2026, 11, 11, 3, 1))
    assert cron_matches("30 2 1 * *", datetime(2026, 12, 1, 2, 30))
    assert cron_matches("0 4 * * 1", datetime(2026, 11, 9, 4, 0))  # 周一
    assert not cron_matches("0 4 * * 1", datetime(2026, 11, 10, 4, 0))  # 周二


def test_cron_rejects_extended():
    for bad in ["* * * *", "L * * * *", "*/0 * * * *", "* * * * * *"]:
        try:
            parse_cron(bad)
        except ValueError:
            continue
        raise AssertionError(f"应拒绝: {bad}")


def test_default_disabled():
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "data/x.db")
        try:
            db.init_schema()
            s = Scheduler(Config.load(), db)
            assert s.jobs == []
            assert s.start() is False, "默认不应启动任何任务"
        finally:
            db.close()


def test_dangerous_command_rejected():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "config").mkdir()
        (b / "config" / "schedule.yaml").write_text(
            "enabled: true\njobs:\n"
            "  - name: evil\n    command: execute\n    cron: '* * * * *'\n    enabled: true\n"
            "  - name: ok\n    command: scan\n    cron: '0 3 * * *'\n    enabled: true\n",
            encoding="utf-8",
        )
        cfg = Config({"schedule": {"enabled": True,
                                    "config": str(b / "config" / "schedule.yaml")}})
        db = Database(b / "data/x.db")
        try:
            db.init_schema()
            s = Scheduler(cfg, db)
            names = [j["name"] for j in s.jobs]
            assert names == ["ok"], names
        finally:
            db.close()


def test_job_triggers_and_audits():
    """注入 runner，手动触发一次任务，验证审计落库。"""
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "config").mkdir()
        (b / "config" / "schedule.yaml").write_text(
            "enabled: true\njobs:\n"
            "  - name: scan-test\n    command: scan\n    cron: '* * * * *'\n    enabled: true\n",
            encoding="utf-8",
        )
        cfg = Config({"schedule": {"enabled": True,
                                    "config": str(b / "config" / "schedule.yaml")}})
        db = Database(b / "data/x.db")
        try:
            db.init_schema()
            calls = []
            s = Scheduler(cfg, db, runner=lambda cmd: calls.append(cmd))
            assert len(s.jobs) == 1
            s._run_job(s.jobs[0])
            time.sleep(0.1)
            assert calls == ["scan"], calls
            row = db.connect().execute(
                "SELECT job_name, command, status FROM scheduler_logs"
            ).fetchone()
            assert row["job_name"] == "scan-test" and row["status"] == "ok", dict(row)
        finally:
            db.close()


if __name__ == "__main__":
    test_cron_parse()
    test_cron_matches()
    test_cron_rejects_extended()
    test_default_disabled()
    test_dangerous_command_rejected()
    test_job_triggers_and_audits()
    print("调度器测试全部通过")
