"""分类器单元测试."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.classifier import Classifier
from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.models import FileRecord


def make_record(path: str, ext: str = "", size: int = 1000) -> FileRecord:
    p = Path(path)
    return FileRecord(
        path=path, volume="volume5", filename=p.name,
        ext=ext or p.suffix.lower().lstrip("."), size=size,
        mtime=1000, atime=1000, ctime=1000,
    )


def test_path_rule():
    db = Database(":memory:")
    db.init_schema()
    c = Classifier(Config.load(), db)
    ftype, domain, temp, conf, by = c.classify(
        make_record("/volume5/16t2/分享内容/电影/疯狂动物城.mp4", "mp4")
    )
    assert domain == "生活" and ftype == "视频" and by == "path", (ftype, domain, by)


def test_extension():
    db = Database(":memory:")
    db.init_schema()
    c = Classifier(Config.load(), db)
    ftype, domain, temp, conf, by = c.classify(
        make_record("/volume5/16t2/随机/周杰伦-晴天.flac", "flac")
    )
    assert ftype == "音乐", (ftype, domain, by)


def test_keyword():
    db = Database(":memory:")
    db.init_schema()
    c = Classifier(Config.load(), db)
    ftype, domain, temp, conf, by = c.classify(
        make_record("/volume2/16t1/待整理/2024年度财务报表.xlsx", "xlsx")
    )
    assert domain == "工作" and ftype == "文档", (ftype, domain, by)


def test_temp():
    db = Database(":memory:")
    db.init_schema()
    c = Classifier(Config.load(), db)
    ftype, domain, temp, conf, by = c.classify(
        make_record("/volume4/1T-ssd/项目/a.txt", "txt")
    )
    assert temp == "热", temp


if __name__ == "__main__":
    test_path_rule()
    test_extension()
    test_keyword()
    test_temp()
    print("所有测试通过")
