"""批量归类测试：≥1000 项、进度回调、只读不移动."""

import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.models import FileRecord
from nas_organizer.planner import Planner


@contextmanager
def build(base: Path):
    db = Database(base / "data/x.db")
    db.init_schema()
    cfg = Config({
        "backup": {"hot": {"source": str(base / "out")}},
        "security": {"allowed_paths": [str(base)], "strict": True},
    })
    try:
        yield Planner(cfg, db), db
    finally:
        db.close()


def test_batch_1000_files():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "out").mkdir()
        with build(b) as (planner, db):
            now = time.time()
            for i in range(1200):
                p = b / "in" / f"f{i}.txt"
                rec = FileRecord(path=str(p), volume="v1", filename=p.name,
                                 ext=".txt", size=100, mtime=now, atime=now, ctime=now)
                fid = db.upsert_file(rec)
                db.update_classification(fid, "文档", "工作" if i % 2 else "生活",
                                         "温", 0.9, "test")
            progress_calls = []
            results = planner.build_batch_plans(
                group_by="domain", limit=1200,
                progress=lambda done, total, label: progress_calls.append((done, total, label)),
            )
            assert len(results) == 2, results  # 工作 / 生活
            assert sum(r["items"] for r in results) == 1200, results
            assert progress_calls and progress_calls[-1][0] == 1200
            assert any(done == 1000 for done, _, _ in progress_calls)
            # 只读：批量归类只生成计划，不创建目标文件
            assert not any((b / "out").rglob("*.txt")), "批量归类不应执行文件移动"


def test_batch_group_by_ftype():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "out").mkdir()
        with build(b) as (planner, db):
            now = time.time()
            for i, (domain, ftype) in enumerate([
                ("工作", "文档"), ("生活", "视频"), ("资源", "图书")] * 10):
                p = b / "in" / f"x{i}.dat"
                rec = FileRecord(path=str(p), volume="v1", filename=p.name,
                                 ext=".dat", size=10, mtime=now, atime=now, ctime=now)
                fid = db.upsert_file(rec)
                db.update_classification(fid, ftype, domain, "冷", 0.8, "test")
            results = planner.build_batch_plans(group_by="domain_ftype")
            keys = {r["group"] for r in results}
            assert keys == {"工作/文档", "生活/视频", "资源/图书"}, keys


def test_invalid_group_by():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        with build(b) as (planner, db):
            try:
                planner.build_batch_plans(group_by="bogus")
            except ValueError:
                pass
            else:
                raise AssertionError("应拒绝非法 group_by")


if __name__ == "__main__":
    test_batch_1000_files()
    test_batch_group_by_ftype()
    test_invalid_group_by()
    print("批量归类测试全部通过")
