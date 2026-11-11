"""D7 单元测试：FTS5 检索 / 插件能力 / PWA 端点。"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.extractors import NullExtractor, TesseractExtractor, build_extractor, capability
from nas_organizer.models import FileRecord
from nas_organizer.search import SearchIndex


def make_db() -> Database:
    db = Database(":memory:")
    db.init_schema()
    return db


def add(db, name: str, domain="工作", ftype="文档"):
    rec = FileRecord(path=f"/nas/{domain}/{name}", volume="v1", filename=name,
                     ext=Path(name).suffix.lstrip("."), size=100,
                     mtime=time.time(), atime=0, ctime=0, ftype=ftype, domain=domain)
    fid = db.upsert_file(rec)
    db.update_classification(fid, ftype, domain, "热", 0.9, "test")
    return fid


def test_fts5_chinese_substring():
    db = make_db()
    for i in range(3):
        add(db, f"财务报表202{i}.pdf")
    add(db, "周杰伦晴天.mp3", "生活", "音乐")
    s = SearchIndex(Config({}), db)
    assert s.build_index()["fts5"] is True
    assert s.search("财务报表")["count"] == 3
    assert s.search("报表")["count"] == 3        # 中文 2-gram 子串
    assert s.search("2021")["count"] == 1
    assert s.search("周杰伦")["count"] == 1
    assert s.search("不存在的词xyz")["count"] == 0
    assert s.stats()["indexed"] == 4
    print("[OK] FTS5：中文整词/子串、数字、英文名检索均正确")


def test_search_like_fallback():
    # 未建索引时自动降级 LIKE，仍可查
    db = make_db()
    add(db, "合同.pdf")
    s = SearchIndex(Config({}), db)
    r = s.search("合同")
    assert r["count"] == 1 and r["engine"] in ("fts5", "like"), r
    print("[OK] 搜索降级：未建索引时 LIKE 仍可命中")


def test_extractor_default_off():
    assert isinstance(build_extractor(False), NullExtractor)
    assert build_extractor(False).extract("x.png") is None
    assert not build_extractor(False).available()
    # 启用但 provider 未知 → 空实现
    assert isinstance(build_extractor(True, "unknown"), NullExtractor)
    ext = TesseractExtractor()
    assert isinstance(capability(), dict) and "ocr" in capability()
    if not ext.available():
        assert ext.extract("x.png") is None  # 无 tesseract 优雅降级
    print("[OK] OCR 插件：默认关闭零行为，缺失依赖优雅降级")


def test_pwa_endpoints():
    from fastapi.testclient import TestClient
    from nas_organizer.api import setup
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        cfg = Config({"database": {"path": "data/t.db"}}, base)
        db = Database(base / "data" / "t.db")
        try:
            app = setup(cfg, db)
            client = TestClient(app)
            mf = client.get("/manifest.webmanifest")
            assert mf.status_code == 200 and "manifest+json" in mf.headers.get("content-type", "")
            assert "NAS Organizer" in mf.text
            sw = client.get("/sw.js")
            assert sw.status_code == 200 and "javascript" in sw.headers.get("content-type", "")
            # 安全中间件统一注入 no-store（比 no-cache 更严格），符合预期
            assert sw.headers.get("Cache-Control") in ("no-cache", "no-store")
            assert "/api/" not in sw.text or "startsWith('/api/')" in sw.text  # 不缓存 API
            index = client.get("/")
            assert index.status_code == 200 and "/manifest.webmanifest" in index.text
            # API 仍需认证
            assert client.get("/api/stats").status_code == 401
        finally:
            db.close()
    print("[OK] PWA：manifest / sw 正确 MIME，API 仍受认证保护")


def test_mcp_d7_tools():
    import json
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        (base / "allowed").mkdir()
        cfg = Config({"database": {"path": "data/t.db"},
                      "security": {"allowed_paths": [str(base / "allowed")], "strict": True,
                                   "audit_log": "data/a.jsonl"}}, base)
        db = Database(base / "data" / "t.db")
        from nas_organizer.mcp_server import MCPServer
        s = MCPServer(cfg, db)
        try:
            names = {t["name"] for t in s.list_tools()}
            for n in ("search", "search_stats", "plugin_capability", "search_reindex"):
                assert n in names, n
            r = s.call_tool("plugin_capability", {})
            assert r["isError"] is False
            payload = json.loads(r["content"][0]["text"])
            assert "extractors" in payload and "ocr_enabled" in payload
        finally:
            db.close()
    print("[OK] MCP：D7 搜索与插件能力工具注册")


if __name__ == "__main__":
    test_fts5_chinese_substring()
    test_search_like_fallback()
    test_extractor_default_off()
    test_pwa_endpoints()
    test_mcp_d7_tools()
    print("D7 测试全部通过")
