"""查重器单元测试."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.dedupe import Deduper


def test_edge_hashes():
    with tempfile.TemporaryDirectory() as td:
        p1 = Path(td) / "a.bin"
        p2 = Path(td) / "b.bin"
        content = b"hello world" * 100
        p1.write_bytes(content)
        p2.write_bytes(content)
        d = Deduper(Config.load(), Database(":memory:"))
        assert d._edge_hashes(p1) == d._edge_hashes(p2)
        assert d._full_hash(p1) == d._full_hash(p2)


if __name__ == "__main__":
    test_edge_hashes()
    print("所有测试通过")
