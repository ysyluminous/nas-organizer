"""D6 单元测试：复核台 / FFmpeg 视频降级 / rclone 只读索引 / 自然语言意图映射。"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.feedback import FeedbackLearner
from nas_organizer.mcp_server import MCPServer
from nas_organizer.models import FileRecord
from nas_organizer.remote import RcloneIndexer, VideoKeyframeFinder, capability
from nas_organizer.review import ReviewQueue


def make_db() -> Database:
    db = Database(":memory:")
    db.init_schema()
    return db


def add_low_conf(db, path: str, confidence: float) -> int:
    ts = time.time()
    rec = FileRecord(path=path, volume="v1", filename=Path(path).name, ext=".pdf",
                     size=100, mtime=ts, atime=ts, ctime=ts,
                     ftype="文档", domain="资源", confidence=confidence, classified_by="ext")
    fid = db.upsert_file(rec)
    db.update_classification(fid, "文档", "资源", "热", confidence, "ext")
    return fid


def test_review_queue_and_decision():
    db = make_db()
    hi = add_low_conf(db, "/nas/财务报表2024.pdf", 0.95)  # 高置信度，不进队列
    low = add_low_conf(db, "/nas/云账户账单明细2025.pdf", 0.3)
    rq = ReviewQueue(Config({}), db)
    q = rq.queue()
    assert all(it["id"] != hi for it in q["low_confidence"])
    assert any(it["id"] == low for it in q["low_confidence"])
    # 裁决：修正 domain + 写反馈
    result = rq.decide(low, "domain", "个人", note="财务账单")
    assert result["ok"] and result["keywords_learned"] > 0
    rec = db.get_file(low)
    assert rec.domain == "个人" and rec.confidence == 1.0 and rec.classified_by == "manual"
    assert db.get_feedback("domain")
    # 同类文件应被反馈命中
    from nas_organizer.classifier import Classifier
    c = Classifier(Config({}), db)
    r2 = c.classify(FileRecord(path="/nas/inbox/云账户账单明细2026.pdf", volume="v1",
                               filename="云账户账单明细2026.pdf", ext=".pdf", size=1,
                               mtime=time.time(), atime=0, ctime=0))
    assert r2[1] == "个人" and r2[4] == "feedback", r2
    print("[OK] 复核台：队列识别低置信度、裁决修正+写反馈、同类自动归类")


def test_review_history_and_stats():
    db = make_db()
    fid = add_low_conf(db, "/nas/合同.pdf", 0.5)
    rq = ReviewQueue(Config({}), db)
    rq.decide(fid, "ftype", "文档")
    hist = rq.history()
    assert len(hist) == 1 and hist[0]["old_value"] == "文档"  # 原本 ftype 就是文档
    stats = rq.stats()
    assert stats["decisions_made"] == 1 and stats["threshold"] == 0.8
    print("[OK] 复核审计：历史与统计正确")


def test_decide_validation():
    db = make_db()
    rq = ReviewQueue(Config({}), db)
    try:
        rq.decide(999, "domain", "工作")
        raise AssertionError("不存在文件应拒绝")
    except ValueError:
        pass
    try:
        fid = add_low_conf(db, "/nas/a.pdf", 0.3)
        rq.decide(fid, "bad_field", "x")
        raise AssertionError("非法字段应拒绝")
    except ValueError:
        pass
    print("[OK] 裁决校验：不存在文件/非法字段均拒绝")


def test_external_capability_degrades():
    caps = capability()
    assert set(caps) == {"ffmpeg", "rclone"}
    # 当前环境两者都未安装 → 全部 false，且以下调用都不抛异常
    finder = VideoKeyframeFinder(Config({}), make_db())
    result = finder.find_similar()
    assert result["groups"] == []
    if not caps["ffmpeg"]:
        assert result.get("available") is False
        assert "FFmpeg" in result.get("note", "")
    idx = RcloneIndexer(Config({}), make_db())
    if not caps["rclone"]:
        assert idx.lsjson("remote:path") == []
        assert idx.to_records("remote:path") == []
    print("[OK] 外部工具降级：无 ffmpeg/rclone 时优雅返回空，不中断")


def test_remote_validates_remote_string():
    idx = RcloneIndexer(Config({}), make_db())
    assert idx._valid_remote("nas:Media/电影")
    assert not idx._valid_remote("/local/path")  # 必须是 name:path
    assert not idx._valid_remote("name:path\nrm -rf /")  # 防注入
    assert not idx._valid_remote(":path")
    print("[OK] rclone 安全：remote 格式校验与参数注入防护")


def test_intent_schema_and_mcp_tools():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        (base / "allowed").mkdir()
        cfg = Config({"database": {"path": "data/t.db"},
                      "security": {"allowed_paths": [str(base / "allowed")], "strict": True,
                                   "audit_log": "data/a.jsonl"}}, base)
        db = Database(base / "data" / "t.db")
        s = MCPServer(cfg, db)
        names = {t["name"] for t in s.list_tools()}
        for n in ("review_queue", "review_stats", "review_history", "review_decide",
                  "intent_schema", "external_capability", "video_similar", "remote_lsjson"):
            assert n in names, n
        schema = s.call_tool("intent_schema", {})
        assert schema["isError"] is False
        try:
            payload = __import__("json").loads(schema["content"][0]["text"])
            assert "服务端只接受结构化参数" in payload["principle"]
            assert all("tools" in i and "phrase_examples" in i for i in payload["intents"])
            # 未授权裁决应被拒绝
            dec = s.call_tool("review_decide", {"file_id": 1, "field": "domain",
                                                 "value": "工作", "confirm": True})
            assert dec["isError"] is True and "授权" in dec["content"][0]["text"]
        finally:
            db.close()
    print("[OK] MCP：D6 工具注册、意图映射、裁决需授权")


def test_review_decide_requires_confirm_in_mcp():
    # confirm 强制：即便授权也不能少 confirm
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        (base / "allowed").mkdir()
        cfg = Config({"database": {"path": "data/t.db"},
                      "security": {"allowed_paths": [str(base / "allowed")], "strict": True,
                                   "audit_log": "data/a.jsonl"}}, base)
        db = Database(base / "data" / "t.db")
        s = MCPServer(cfg, db)
        try:
            s.authorized = True
            r = s.call_tool("review_decide", {"file_id": 1, "field": "domain", "value": "工作"})
            assert r["isError"] is True and "confirm=true" in r["content"][0]["text"]
        finally:
            db.close()
    print("[OK] MCP 裁决：缺 confirm=true 被拒绝")


if __name__ == "__main__":
    test_review_queue_and_decision()
    test_review_history_and_stats()
    test_decide_validation()
    test_external_capability_degrades()
    test_remote_validates_remote_string()
    test_intent_schema_and_mcp_tools()
    test_review_decide_requires_confirm_in_mcp()
    print("D6 测试全部通过")
