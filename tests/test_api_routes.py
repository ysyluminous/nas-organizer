"""API 全路由集成测试（Round C）。

覆盖 D1-Round B 全部 REST 端点：
- 公开接口 vs 受保护接口的认证边界
- 危险写操作的 confirm=true 强制校验
- 分页/数量参数上限（防止一次拉爆索引）
- PWA 与静态资源可访问性
- 安全响应头在所有响应上一致

直接运行：python tests/test_api_routes.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from nas_organizer import api  # noqa: E402
from nas_organizer.config import Config  # noqa: E402
from nas_organizer.db import Database  # noqa: E402


# 只读端点：未授权必须 401，授权后必须 200
READ_ENDPOINTS = [
    "/api/auth/me", "/api/stats", "/api/files", "/api/duplicates",
    "/api/plans", "/api/review/queue", "/api/review/stats", "/api/review/history",
    "/api/tasks", "/api/views", "/api/views/stats",
    "/api/search?q=test", "/api/search/stats", "/api/cleanup", "/api/cleanup/kinds",
    "/api/recycle", "/api/recycle/stats", "/api/audit/backup",
    "/api/media/capability", "/api/plugins/capability",
    "/api/phash/similar", "/api/video/similar", "/api/feedback", "/api/schedule",
    "/api/audit/operations", "/api/mounts",
    "/api/scan/progress",
    "/api/tree/children", "/api/tree/ancestors",
]

# 会写索引但不碰用户文件的端点：无 confirm 必须 400
CONFIRM_ENDPOINTS = ["/api/search/reindex", "/api/views/auto-tag"]


def build_client(base: Path):
    db_path = base / "data" / "routes.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config({"database": {"path": str(db_path)}})
    db = Database(db_path)
    client = TestClient(api.setup(config, db))
    pf = base / "data" / "INITIAL_PASSWORD.txt"
    password = [line.split("：", 1)[1].strip() for line in pf.read_text(encoding="utf-8").splitlines()
                if line.startswith("密码：")][0]
    return client, password, db


def check_all():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        client, password, db = build_client(Path(d))
        try:
            # 1. 公开接口（含新增健康检查，供 Docker healthcheck 使用）
            assert client.get("/api/auth/status").status_code == 200
            health = client.get("/api/health")
            assert health.status_code == 200 and health.json()["status"] == "ok", health.text
            assert "version" in health.json()
            login = client.post("/api/auth/login", json={"password": password})
            assert login.status_code == 200, login.text
            headers = {"Authorization": "Bearer " + login.json()["token"]}

            # 2. 所有只读端点：认证边界 + 200
            for path in READ_ENDPOINTS:
                assert client.get(path).status_code == 401, f"{path} 未拦截未授权访问"
                r = client.get(path, headers=headers)
                assert r.status_code == 200, f"{path} 返回 {r.status_code}: {r.text[:120]}"

            # 3. 分页参数上限：超大 limit 被夹紧，不报错
            for path in ["/api/files?limit=999999&offset=0", "/api/duplicates?limit=999999",
                         "/api/recycle?limit=999999", "/api/cleanup?limit=999999",
                         "/api/phash/similar?limit=999999"]:
                r = client.get(path, headers=headers)
                assert r.status_code == 200, f"{path} 参数上限处理失败: {r.text[:120]}"

            # 4. 危险端点：未授权 401，授权但无 confirm 400
            for path in CONFIRM_ENDPOINTS:
                assert client.post(path, json={"confirm": True}).status_code == 401, path
                r = client.post(path, headers=headers, json={})
                assert r.status_code == 400 and "confirm" in r.text, f"{path} 未强制确认"

            # 5. 静态资源 / PWA
            for path in ["/", "/static/app.js", "/static/style.css", "/static/icon.svg",
                         "/manifest.webmanifest", "/sw.js"]:
                r = client.get(path)
                assert r.status_code == 200, f"{path} 不可访问"
            assert "application/manifest+json" in client.get("/manifest.webmanifest").headers.get("content-type", "")
            assert client.get("/sw.js").headers.get("Service-Worker-Allowed") == "/"

            # 6. 安全响应头跨端点一致（公开 + 受保护 + 404 之外的静态）
            for r in [client.get("/api/auth/status"), client.get("/api/stats"), client.get("/")]:
                assert r.headers.get("X-Frame-Options") == "DENY"
                assert r.headers.get("X-Content-Type-Options") == "nosniff"
                assert r.headers.get("Referrer-Policy") == "no-referrer"
                assert r.headers.get("Cache-Control") == "no-store"

            # 7. 未知清理类型返回 400（输入校验）
            r = client.get("/api/cleanup?kinds=__not_a_kind__", headers=headers)
            assert r.status_code == 400 and "未知清理类型" in r.text

            # 8. 挂载绑定/解绑：confirm 强制校验 + 越界路径拒绝
            assert client.post("/api/mounts", json={"name": "x", "path": "/etc"}).status_code == 401
            r = client.post("/api/mounts", headers=headers, json={"name": "x", "path": "/etc"})
            assert r.status_code == 400 and "confirm" in r.text, r.text
            r = client.post("/api/mounts", headers=headers,
                            json={"name": "x", "path": "/etc", "confirm": True})
            assert r.status_code == 400, "越界路径未被拒绝"
            r = client.delete("/api/mounts/nope", headers=headers)
            assert r.status_code == 400 and "confirm" in r.text, r.text
            r = client.delete("/api/mounts/nope?confirm=true", headers=headers)
            assert r.status_code == 400 and "未找到" in r.text, r.text

            print(f"API 全路由集成测试通过：{len(READ_ENDPOINTS)} 只读端点 + "
                  f"{len(CONFIRM_ENDPOINTS)} 确认端点 + 静态/PWA/安全头")
        finally:
            db.close()


if __name__ == "__main__":
    check_all()
