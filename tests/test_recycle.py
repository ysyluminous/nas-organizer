"""两级回收站测试：隔离 / 提升 / 还原 / 清除确认 / 审计."""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.recycle import RecycleBin


def make_bin(base: Path, days: int = 30) -> tuple[RecycleBin, Database]:
    db = Database(base / "data/x.db")
    db.init_schema()
    cfg = Config({
        "executor": {"recycle_root": str(base / "data/recycle")},
        "recycle": {"days_to_stage2": days},
        "security": {"allowed_paths": [str(base)], "strict": True},
    })
    return RecycleBin(cfg, db), db


def test_quarantine_and_restore():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        src = b / "work" / "report.txt"
        src.parent.mkdir(parents=True)
        src.write_text("hello", encoding="utf-8")
        rb, db = make_bin(b)
        try:
            stored = rb.quarantine(src, reason="test")
            assert not src.exists() and Path(stored).exists()
            items = rb.list_items()
            assert len(items) == 1 and items[0]["stage"] == 1
            # 无 confirm 拒绝还原
            assert rb.restore(items[0]["id"], confirm=False)["ok"] is False
            result = rb.restore(items[0]["id"], confirm=True)
            assert result["ok"] is True and src.read_text() == "hello"
            assert rb.stats()["stage0"]["count"] == 1
        finally:
            db.close()


def test_promote_and_purge_requires_confirm():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        src = b / "junk.tmp"
        src.write_text("junk", encoding="utf-8")
        rb, db = make_bin(b, days=0)
        try:
            rb.quarantine(src)
            # days=0 → cutoff=now，removed_at < now 成立，提升
            stats = rb.promote(older_than_days=0)
            assert stats["promoted"] == 1, stats
            assert rb.stats()["stage2"]["count"] >= 1
            # 无 confirm 拒绝清除
            assert rb.purge(stage=2, confirm=False)["ok"] is False
            # stage1 清除也被拒绝（即使 confirm）
            assert rb.purge(stage=1, confirm=True)["ok"] is False
            # 正式清除
            result = rb.purge(stage=2, confirm=True)
            assert result["ok"] is True and result["purged"] >= 1
        finally:
            db.close()


def test_no_overwrite_on_restore():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        src = b / "a.txt"
        src.write_text("old", encoding="utf-8")
        rb, db = make_bin(b)
        try:
            rb.quarantine(src)
            src.write_text("new", encoding="utf-8")  # 原路径被新文件占用
            item_id = rb.list_items()[0]["id"]
            result = rb.restore(item_id, confirm=True)
            assert result["ok"] is False and "已存在" in result["reason"]
            assert src.read_text() == "new"  # 未覆盖
        finally:
            db.close()


def test_audit_trail():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        src = b / "x.log"
        src.write_text("x", encoding="utf-8")
        rb, db = make_bin(b, days=0)
        try:
            rb.quarantine(src)
            rb.promote(0)
            rb.purge(2, confirm=True)
            actions = [r["action"] for r in db.connect().execute(
                "SELECT action FROM recycle_logs ORDER BY id")]
            assert "quarantine" in actions and "promote" in actions and "purge" in actions
        finally:
            db.close()


if __name__ == "__main__":
    test_quarantine_and_restore()
    test_promote_and_purge_requires_confirm()
    test_no_overwrite_on_restore()
    test_audit_trail()
    print("回收站测试全部通过")
