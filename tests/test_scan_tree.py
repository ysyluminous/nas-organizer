"""扫描进度 / 增量扫描 / 文件树 单元测试。

直接运行：python tests/test_scan_tree.py
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config  # noqa: E402
from nas_organizer.db import Database  # noqa: E402
from nas_organizer.filetree import FileTree, _parent  # noqa: E402
from nas_organizer.scanner import Scanner  # noqa: E402
from nas_organizer.scan_runtime import load_settings, save_settings  # noqa: E402


def build(base: Path):
    data = base / "data"
    data.mkdir(parents=True, exist_ok=True)
    # 扫描根：用真实存在的目录，PathGuard 白名单由此卷推导
    scan_root = base / "scan" / "media1"
    (scan_root / "电影").mkdir(parents=True, exist_ok=True)
    (scan_root / "音乐").mkdir(parents=True, exist_ok=True)
    (scan_root / "电影" / "a.mp4").write_bytes(b"x" * 1024)
    (scan_root / "电影" / "b.mkv").write_bytes(b"y" * 2048)
    (scan_root / "音乐" / "c.mp3").write_bytes(b"z" * 512)
    (scan_root / "root.txt").write_bytes(b"r" * 100)

    cfg = Config({
        "database": {"path": str(data / "db.sqlite")},
        "volumes": [{"name": "media1", "path": str(scan_root), "role": "cold"}],
        "scanner": {"workers": 2},
    }, base_dir=base)
    db = Database(cfg.resolve(cfg.get("database.path")))
    db.init_schema()
    return cfg, db, scan_root


def check_all():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        base = Path(d)
        cfg, db, root = build(base)
        try:
            # 1. 全量扫描 + 进度回调
            scanner = Scanner(cfg, db)
            progress = []
            scanner.on_progress(lambda done, total: progress.append((done, total)))
            volumes = []
            scanner.on_volume(lambda name, phase: volumes.append((name, phase)))
            counts = scanner.scan_all()
            assert counts["media1"] == 4, counts
            assert progress and progress[-1][0] == 4, progress[-1]
            assert ("media1", "start") in volumes and ("media1", "done") in volumes
            assert db.connect().execute(
                "SELECT COUNT(*) n FROM files WHERE status='active'"
            ).fetchone()["n"] == 4
            print("  全量扫描 + 进度回调 PASS")

            # 2. 增量扫描：无变化时入库 0，但状态仍更新
            counts2 = scanner.scan_all(incremental=True)
            assert counts2["media1"] == 0, counts2
            print("  增量扫描（无变化）PASS")

            # 3. 新增文件自动入库
            (root / "电影" / "new.mp4").write_bytes(b"n" * 256)
            counts3 = scanner.scan_all(incremental=True)
            assert counts3["media1"] == 1, counts3
            assert db.connect().execute(
                "SELECT COUNT(*) n FROM files WHERE filename='new.mp4'"
            ).fetchone()["n"] == 1
            print("  增量扫描（新增文件自动入库）PASS")

            # 4. 文件树：roots
            tree = FileTree(cfg, db)
            roots = tree.roots_listing()
            assert len(roots["items"]) == 1 and roots["items"][0]["name"] == "media1"
            assert roots["items"][0]["file_count"] == 5
            print("  文件树 roots PASS")

            # 5. 目录 children：根卷下应有 电影/音乐 两个目录 + root.txt
            listing = tree.children(str(root))
            kinds = [i["kind"] for i in listing["items"]]
            assert "dir" in kinds and "file" in kinds, kinds
            names = [i["name"] for i in listing["items"]]
            assert "电影" in names and "音乐" in names and "root.txt" in names, names
            assert listing["dir_count"] == 2 and listing["file_count"] == 1, listing
            print("  文件树 children（目录+文件）PASS")

            # 6. 进入一级目录：电影下 3 个文件
            from nas_organizer.filetree import _norm
            sub = tree.children(str(root / "电影"))
            assert sub["file_count"] == 3, sub
            assert sub["parent"] == _norm(str(root)), sub
            print("  文件树 进入一级目录 PASS")

            # 7. 越界路径拒绝
            denied = tree.children("/etc")
            assert denied.get("denied") is True, denied
            print("  文件树 越界拒绝 PASS")

            # 8. 面包屑
            anc = tree.ancestors("a/b/c")
            assert [x["name"] for x in anc] == ["a", "b", "c"], anc
            assert _parent("/scan/media1/x") == "/scan/media1"
            print("  面包屑 PASS")

            # 9. 设置持久化与夹紧
            s = save_settings(cfg, {"auto_incremental": True, "interval_minutes": 99999})
            assert s["auto_incremental"] is True and s["interval_minutes"] == 1440, s
            assert load_settings(cfg) == s
            print("  扫描设置持久化/夹紧 PASS")

            print("扫描进度/增量/文件树 测试全部通过")
        finally:
            db.close()


if __name__ == "__main__":
    check_all()
