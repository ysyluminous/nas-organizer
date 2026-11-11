"""文件夹别名测试：抽样命中 + 不误伤项目目录 + 归一化."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.classifier import Classifier
from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.models import FileRecord


def make_record(path: str, ext: str = "mp4") -> FileRecord:
    return FileRecord(path=path, volume="v1", filename=Path(path).name,
                      ext=ext, size=1000, mtime=1.0, atime=1.0, ctime=1.0)


def build_classifier() -> Classifier:
    db = Database(":memory:")
    db.init_schema()
    return Classifier(Config.load(), db)


def test_alias_count():
    c = build_classifier()
    assert len(c.folder_aliases) >= 135, len(c.folder_aliases)


def test_50_alias_samples():
    """抽样 50 个别名：作为目录段时应被 alias 或同语义 path 规则命中。"""
    c = build_classifier()
    samples = list(c.folder_aliases.keys())[:50]
    for alias in samples:
        rec = make_record(f"/scan/v1/{alias}/media.mp4")
        ftype, domain, temp, conf, by = c.classify(rec)
        assert by in ("alias", "path"), f"别名 {alias!r} 未命中: {by} {ftype}/{domain}"


def test_no_false_positive_on_project_dirs():
    """项目类目录名含媒体关键词时不应被误判。"""
    c = build_classifier()
    for path in [
        "/scan/v1/电影项目资料/说明.mp4",
        "/scan/v1/music_app_source/main.py",
        "/scan/v1/照片管理系统/readme.txt",
        "/scan/v1/books_order_system/config.json",
    ]:
        _, _, _, _, by = c.classify(make_record(path, ext=Path(path).suffix.lstrip(".")))
        assert by != "alias", f"误伤项目目录: {path}"


def test_normalization():
    c = build_classifier()
    for alias in ["TV Shows", "tv_shows", "tv-shows", "TV  SHOWS"]:
        rec = make_record(f"/scan/v1/{alias}/ep01.mp4")
        assert c.classify(rec)[4] == "alias", f"归一化失败: {alias}"


def test_ftype_from_alias_when_ext_unknown():
    c = build_classifier()
    rec = make_record("/scan/v1/电影/xxx.unknown", ext="unknown")
    ftype, domain, _, _, by = c.classify(rec)
    assert by in ("alias", "path") and ftype == "视频" and domain == "生活", (ftype, domain, by)


if __name__ == "__main__":
    test_alias_count()
    test_50_alias_samples()
    test_no_false_positive_on_project_dirs()
    test_normalization()
    test_ftype_from_alias_when_ext_unknown()
    print("别名测试全部通过")
