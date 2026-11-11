"""备份审计测试：四项检查 + 零写入."""

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.audit import BackupAuditor
from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.models import FileRecord


@contextmanager
def build(base: Path, stale_days: int = 30, min_versions: int = 2):
    db = Database(base / "data/x.db")
    db.init_schema()
    cfg = Config({
        "backup": {"hot": {"source": str(base / "src"), "target": str(base / "bak")}},
        "audit": {"stale_days": stale_days, "min_versions": min_versions},
        "security": {"allowed_paths": [str(base)], "strict": True},
    })
    try:
        yield BackupAuditor(cfg, db), db
    finally:
        db.close()


def add_hot(db: Database, path: Path, size: int = 10, mtime: float | None = None):
    ts = mtime or time.time()
    rec = FileRecord(path=str(path), volume="v1", filename=path.name, ext=path.suffix,
                     size=size, mtime=ts, atime=ts, ctime=ts)
    fid = db.upsert_file(rec)
    db.update_classification(fid, "文档", "工作", "热", 1.0, "test")


def test_all_four_checks_clean():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "src").mkdir(); (b / "bak").mkdir()
        src = b / "src" / "a.txt"; src.write_text("0123456789")
        bak = b / "bak" / "a.txt"; bak.write_text("0123456789")
        for i in range(2):
            (b / "bak" / f"a.v{i}.txt").write_text("v")
        with build(b) as (auditor, db):
            add_hot(db, src)
            report = auditor.audit()
        checks = report.checks
        assert set(checks) == {"existence", "versioning", "staleness", "conflict"}
        assert checks["existence"]["ok"] and checks["existence"]["present"] == 1
        assert checks["conflict"]["ok"]
        assert checks["versioning"]["ok"]
        assert report.ok


def test_missing_and_conflict_detected():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "src").mkdir(); (b / "bak").mkdir()
        s1 = b / "src" / "ok.txt"; s1.write_text("123")
        (b / "bak" / "ok.txt").write_text("123")
        s2 = b / "src" / "missing.txt"; s2.write_text("x")
        s3 = b / "src" / "conflict.txt"; s3.write_text("12345678")
        (b / "bak" / "conflict.txt").write_text("SHORT")
        with build(b, min_versions=0) as (auditor, db):
            add_hot(db, s1); add_hot(db, s2); add_hot(db, s3)
            report = auditor.audit()
        assert report.checks["existence"]["missing"] == 1
        assert report.checks["conflict"]["conflicts"] == 1
        assert report.ok is False


def test_staleness_detected():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "src").mkdir(); (b / "bak").mkdir()
        src = b / "src" / "old.txt"; src.write_text("123")
        bak = b / "bak" / "old.txt"; bak.write_text("123")
        old = time.time() - 100 * 86400
        os.utime(bak, (old, old))
        with build(b, stale_days=30, min_versions=0) as (auditor, db):
            add_hot(db, src)
            report = auditor.audit()
        assert report.checks["staleness"]["stale"] == 1


def test_readonly():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "src").mkdir(); (b / "bak").mkdir()
        src = b / "src" / "a.txt"; src.write_text("abc")
        bak = b / "bak" / "a.txt"; bak.write_text("abc")
        before = bak.read_text()
        with build(b, min_versions=0) as (auditor, db):
            add_hot(db, src)
            auditor.audit()
        assert bak.read_text() == before and src.exists()


if __name__ == "__main__":
    test_all_four_checks_clean()
    test_missing_and_conflict_detected()
    test_staleness_detected()
    test_readonly()
    print("备份审计测试全部通过")
