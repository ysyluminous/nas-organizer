"""12 类清理发现器测试：每类一个样本，默认只读不删除."""

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.cleanup import CLEANUP_KINDS, CleanupFinder
from nas_organizer.config import Config
from nas_organizer.db import Database


@contextmanager
def build_finder(base: Path):
    db = Database(base / "data/x.db")
    db.init_schema()
    cfg = Config({
        "security": {"allowed_paths": [str(base)], "strict": True},
        "cleanup": {"old_days": 30, "stale_days": 60,
                    "download_dirs": [str(base / "下载")]},
    })
    try:
        yield CleanupFinder(cfg, db)
    finally:
        db.close()


def set_old(path: Path, days: int = 100):
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


def test_all_12_kinds():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "empty.txt").touch()                       # 1
        (b / "empty_dir").mkdir()                       # 2
        (b / "temp.tmp").write_text("x")                # 3
        (b / "movie.mp4.part").write_text("x")          # 4
        (b / "seed.torrent").write_text("x")            # 5
        (b / "setup.exe").write_text("x")               # 6
        p = b / "old.zip"; p.write_text("x"); set_old(p)  # 7
        thumb = b / "@eaDir"; thumb.mkdir(); (thumb / "t.jpg").write_text("x")  # 8
        p = b / "app.log"; p.write_text("x"); set_old(p)  # 9
        (b / "合同-副本.docx").write_text("x")          # 10
        (b / "photo (1).jpg").write_text("x")           # 11
        dl = b / "下载"; dl.mkdir()
        p = dl / "big.iso"; p.write_text("x"); set_old(p)  # 12
        (b / "normal.mp4").write_text("x")              # 正常文件

        with build_finder(b) as finder:
            report = finder.discover([b])
        counts = report.by_kind()
        for kind in CLEANUP_KINDS:
            assert counts.get(kind, 0) >= 1, f"{kind} 未发现: {counts}"
        assert all("normal.mp4" not in c.path for c in report.candidates), "误伤正常文件"
        assert report.scanned_dirs == 1


def test_readonly_no_deletion():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        target = b / "junk.tmp"
        target.write_text("keep me")
        with build_finder(b) as finder:
            report = finder.discover([b], kinds=["temp_file"])
        assert len(report.candidates) == 1
        assert target.exists() and target.read_text() == "keep me"


def test_kind_filter():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        (b / "a.tmp").write_text("x")
        (b / "b.exe").write_text("x")
        with build_finder(b) as finder:
            report = finder.discover([b], kinds=["temp_file"])
        kinds = {c.kind for c in report.candidates}
        assert kinds == {"temp_file"}, kinds


def test_exclude_project_dirs():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d)
        git_dir = b / ".git"; git_dir.mkdir(); (git_dir / "config.tmp").write_text("x")
        with build_finder(b) as finder:
            report = finder.discover([b], kinds=["temp_file"])
        assert len(report.candidates) == 0, report.candidates


if __name__ == "__main__":
    test_all_12_kinds()
    test_readonly_no_deletion()
    test_kind_filter()
    test_exclude_project_dirs()
    print("12 类清理发现器测试全部通过")
