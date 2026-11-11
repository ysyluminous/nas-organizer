"""分类器单元测试（使用通用容器路径，不绑定任何 NAS 品牌）."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.classifier import Classifier
from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.models import FileRecord


def make_record(path: str, ext: str = "", size: int = 1000, volume: str = "media1",
                mtime: float | None = None) -> FileRecord:
    p = Path(path)
    ts = mtime if mtime is not None else time.time()
    return FileRecord(
        path=path, volume=volume, filename=p.name,
        ext=ext or p.suffix.lower().lstrip("."), size=size,
        mtime=ts, atime=ts, ctime=ts,
    )


def make_classifier(config: Config | None = None) -> Classifier:
    db = Database(":memory:")
    db.init_schema()
    return Classifier(config or Config.load(), db)


def test_path_rule():
    c = make_classifier()
    ftype, domain, temp, conf, by = c.classify(
        make_record("/scan/media1/电影/疯狂动物城.mp4", "mp4")
    )
    assert domain == "生活" and ftype == "视频" and by == "path", (ftype, domain, by)


def test_extension():
    c = make_classifier()
    ftype, domain, temp, conf, by = c.classify(
        make_record("/scan/media1/misc/周杰伦-晴天.flac", "flac")
    )
    assert ftype == "音乐", (ftype, domain, by)


def test_keyword():
    c = make_classifier()
    ftype, domain, temp, conf, by = c.classify(
        make_record("/scan/media1/inbox/2024年度财务报表.xlsx", "xlsx")
    )
    assert domain == "工作" and ftype == "文档" and by == "keyword", (ftype, domain, by)


def test_temp_by_age():
    c = make_classifier()
    recent = c.classify(make_record("/scan/media1/notes.txt", mtime=time.time()))[2]
    old = c.classify(make_record("/scan/media1/notes2.txt", mtime=time.time() - 800 * 86400))[2]
    assert recent == "热", recent
    assert old == "冷", old


def test_temp_by_configured_hot_volume():
    # 热卷由 config 中 role=hot 决定，不硬编码任何盘符
    config = Config({"volumes": [{"name": "fast-ssd", "role": "hot"}]})
    c = make_classifier(config)
    temp = c.classify(make_record("/scan/fast-ssd/project/readme.txt",
                                  volume="fast-ssd", mtime=time.time() - 800 * 86400))[2]
    assert temp == "热", temp


if __name__ == "__main__":
    test_path_rule()
    test_extension()
    test_keyword()
    test_temp_by_age()
    test_temp_by_configured_hot_volume()
    print("所有测试通过")
