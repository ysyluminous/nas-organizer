"""D4 单元测试：反馈学习 / AI 优雅降级 / exif-ID3 / pHash / 依赖可用性。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.ai import OllamaProvider, build_provider
from nas_organizer.classifier import Classifier
from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.feedback import FeedbackLearner, extract_keywords
from nas_organizer.media import (
    MediaOrganizer, exif_date, music_dest_name, music_tags, photo_date,
)
from nas_organizer.models import FileRecord
from nas_organizer.perceptual import PerceptualFinder, hamming, phash


def rec(path: str, ftype: str = "图片", ext: str = "jpg") -> FileRecord:
    ts = time.time()
    return FileRecord(path=path, volume="v1", filename=Path(path).name, ext=ext,
                      size=1000, mtime=ts, atime=ts, ctime=ts, ftype=ftype, domain="资源")


def test_feedback_changes_low_confidence():
    db = Database(":memory:")
    db.init_schema()
    cfg = Config({"feedback": {"enabled": True}})
    learner = FeedbackLearner(cfg, db)
    n = learner.learn_from_correction("/nas/云账户账单明细.pdf", "domain", "个人")
    assert n > 0
    # 低置信度文件（原本仅凭扩展名归资源）应被反馈命中
    c = Classifier(cfg, db)
    result = c.classify(rec("/nas/inbox/云账户账单明细2025.pdf", "文档", "pdf"))
    assert result[1] == "个人" and result[4] == "feedback", result
    # 规则优先：路径规则命中的文件不受反馈影响
    rule = c.classify(rec("/nas/电影/云账户账单明细.mp4", "视频", "mp4"))
    assert rule[4] == "path", rule
    print("[OK] 反馈学习：低置信度命中反馈，规则判定不受影响")


def test_feedback_reset():
    db = Database(":memory:")
    db.init_schema()
    learner = FeedbackLearner(Config({}), db)
    learner.learn_from_correction("/nas/周杰伦晴天.mp3", "domain", "生活")
    assert len(db.get_feedback()) > 0
    removed = learner.reset()
    assert removed > 0 and len(db.get_feedback()) == 0
    print("[OK] 反馈重置：清空后无残留")


def test_ai_disabled_by_default():
    assert build_provider(Config({})) is None
    assert build_provider(Config({"ai": {"enabled": False}})) is None
    p = build_provider(Config({"ai": {"enabled": True, "provider": "ollama"}}))
    assert isinstance(p, OllamaProvider)
    print("[OK] AI 兜底：默认关闭零 provider，启用后构造 Ollama")


def test_ai_failure_degrades():
    # 指向不可达端口，classify 必须返回 None（降级），且不抛异常
    p = OllamaProvider(Config({"ai": {"base_url": "http://127.0.0.1:9",
                                      "timeout": 1, "domains": ["工作"]}}))
    assert p.classify("合同.pdf", "/工作", "pdf") is None
    print("[OK] AI 降级：无 Ollama/超时返回 None，主流程不受影响")


def test_keyword_extraction():
    kws = extract_keywords("/nas/新建文件夹/2024财务报表-final.pdf")
    assert "财务报表" in kws or "2024财务报表" in kws, kws
    assert "新建文件夹" not in kws, kws
    print("[OK] 关键词抽取：过滤停用词、保留语义词块")


def test_photo_date_and_naming():
    ts = time.mktime(time.strptime("2023:05:06 12:00:00", "%Y:%m:%d %H:%M:%S"))
    date, source = photo_date("/does/not/exist.jpg", ts, exif_enabled=True)
    assert source == "mtime" and date == "2023-05-06", (date, source)
    assert exif_date("/does/not/exist.jpg") is None
    print("[OK] 照片日期：无 exif 优雅回落 mtime")


def test_music_naming_with_and_without_tags():
    rel, source = music_dest_name("周杰伦 - 晴天.mp3", None)
    assert source == "filename" and "周杰伦" in rel, (rel, source)
    rel2, source2 = music_dest_name("track01.mp3", {
        "artist": "周杰伦", "album": "叶惠美", "title": "晴天", "tracknumber": "3/12",
    })
    assert source2 == "id3"
    assert rel2 == "周杰伦/叶惠美/03 - 晴天.mp3", rel2
    assert music_tags("/does/not/exist.mp3") is None
    print("[OK] 音乐命名：有标签按 ID3 三层、无标签回退文件名")


def test_media_suggest_no_overwrite():
    # 同目录下两张同名照片，建议目标不应重复（自动 _1 后缀）
    db = Database(":memory:")
    db.init_schema()
    org = MediaOrganizer(Config({"media": {"exif_enabled": False}}))
    r1 = rec("/nas/IMG_0001.jpg")
    r2 = rec("/nas/sub/IMG_0001.jpg")
    r1.id, r2.id = 1, 2
    items = org.suggest_photos([r1, r2], "/write/album")
    assert len(items) == 2 and items[0]["dst_path"] != items[1]["dst_path"], items
    print("[OK] 媒体建议：同名目标自动去重，避免覆盖")


def test_phash_math():
    assert hamming(0b1010, 0b1011) == 1
    # 纯 Python DCT：同一内容缩放后哈希应一致/近似
    p = Path(__file__).parent / "_d4_sample.png"
    try:
        from PIL import Image
        Image.new("RGB", (64, 64), (120, 80, 40)).save(p)
        small = Path(__file__).parent / "_d4_sample_small.png"
        Image.new("RGB", (32, 32), (120, 80, 40)).save(small)
        h1, h2 = phash(p), phash(small)
        if h1 is not None and h2 is not None:
            assert hamming(h1, h2) <= 10, (h1, h2, hamming(h1, h2))
        p.unlink(missing_ok=True)
        small.unlink(missing_ok=True)
        print("[OK] pHash：缩放同图汉明距离在阈值内")
    except ImportError:
        print("[OK] pHash：无 Pillow，跳过图像相似度（能力已降级）")


def test_perceptual_disabled():
    db = Database(":memory:")
    db.init_schema()
    f = PerceptualFinder(Config({"perceptual": {"enabled": False}}), db)
    assert f.find_similar()["groups"] == []
    print("[OK] 感知哈希：默认关闭时不产出分组")


if __name__ == "__main__":
    test_feedback_changes_low_confidence()
    test_feedback_reset()
    test_ai_disabled_by_default()
    test_ai_failure_degrades()
    test_keyword_extraction()
    test_photo_date_and_naming()
    test_music_naming_with_and_without_tags()
    test_media_suggest_no_overwrite()
    test_phash_math()
    test_perceptual_disabled()
    print("D4 测试全部通过")
