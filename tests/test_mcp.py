"""MCP 服务测试：工具清单、认证边界、confirm 强制、JSON-RPC 协议。"""

import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.mcp_server import MCPServer


@contextmanager
def build_server(base: Path, token: str | None = None):
    cfg = Config({
        "database": {"path": "data/test.db"},
        "security": {"allowed_paths": [str(base / "allowed")], "strict": True,
                     "audit_log": "data/security_audit.jsonl"},
        "auth": {"token_ttl_seconds": 28800},
        "volumes": [{"name": "v1", "path": str(base / "allowed")}],
    }, base)
    (base / "allowed").mkdir(parents=True, exist_ok=True)
    db = Database(base / "data" / "test.db")
    try:
        yield MCPServer(cfg, db, token=token)
    finally:
        db.close()


def test_tool_count_and_schema():
    with tempfile.TemporaryDirectory() as d:
        with build_server(Path(d)) as server:
            tools = server.list_tools()
            assert len(tools) >= 26, f"工具数不足: {len(tools)}"
            names = {t["name"] for t in tools}
            for required in ("get_stats", "list_files", "plan_execute", "rollback_last", "scan_start"):
                assert required in names, f"缺少工具 {required}"
            for tool in tools:
                assert tool["inputSchema"]["type"] == "object"
                assert isinstance(tool["description"], str) and tool["description"]
        print(f"[OK] 工具清单：{len(tools)} 个工具，均含描述与 schema")


def test_json_rpc_handshake():
    with tempfile.TemporaryDirectory() as d:
        with build_server(Path(d)) as server:
            init = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            assert init["result"]["serverInfo"]["name"] == "nas-organizer"
            assert init["result"]["capabilities"]["tools"]["listChanged"] is False
            listing = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            assert len(listing["result"]["tools"]) >= 26
            assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
            unknown = server.handle({"jsonrpc": "2.0", "id": 3, "method": "nope"})
            assert unknown["error"]["code"] == -32601
        print("[OK] JSON-RPC：initialize / tools/list / 通知 / 未知方法")


def test_unauthorized_write_blocked():
    with tempfile.TemporaryDirectory() as d:
        with build_server(Path(d)) as server:
            assert server.authorized is False
            result = server.call_tool("scan_start", {})
            assert result["isError"] is True
            assert "需要授权" in result["content"][0]["text"]
            read = server.call_tool("get_stats", {})
            assert read["isError"] is False
        print("[OK] 认证边界：只读工具匿名可用，写工具被拒绝")


def test_invalid_token_rejected():
    with tempfile.TemporaryDirectory() as d:
        db = Database(Path(d) / "data" / "test.db")
        try:
            cfg = Config({"database": {"path": "data/test.db"}}, Path(d))
            try:
                MCPServer(cfg, db, token="forged.token")
                raise AssertionError("伪造 Token 应导致启动失败")
            except Exception as exc:
                assert "token" in str(exc).lower()
        finally:
            db.close()
        print("[OK] 伪造 Token：服务启动被拒绝")


def test_confirm_required():
    with tempfile.TemporaryDirectory() as d:
        with build_server(Path(d)) as server:
            server.authorized = True
            result = server.call_tool("plan_execute", {"plan_id": 1, "confirm": False})
            assert result["isError"] is True
            assert "confirm=true" in result["content"][0]["text"]
            result2 = server.call_tool("rollback_last", {"confirm": False})
            assert result2["isError"] is True
        print("[OK] 危险操作：无 confirm=true 一律拒绝")


def test_authorized_tools_execute():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        with build_server(base) as server:
            server.authorized = True
            stats = server.call_tool("get_stats", {})
            assert stats["isError"] is False
            payload = json.loads(stats["content"][0]["text"])
            assert payload["total"] == 0
            check = server.call_tool("security_check_path", {"path": str(base / "allowed" / "x.mp4")})
            assert json.loads(check["content"][0]["text"])["allowed"] is True
            violation = server.call_tool("security_check_path", {"path": str(base / "outside" / "x.mp4")})
            assert violation["isError"] is True
        print("[OK] 工具执行：统计、白名单内路径放行、越界路径拒绝")


if __name__ == "__main__":
    test_tool_count_and_schema()
    test_json_rpc_handshake()
    test_unauthorized_write_blocked()
    test_invalid_token_rejected()
    test_confirm_required()
    test_authorized_tools_execute()
    print("MCP 测试全部通过")
