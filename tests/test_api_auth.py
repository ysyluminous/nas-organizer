"""API 认证集成测试：验证未授权拦截、登录、危险操作确认与安全响应头。

直接运行：python tests/test_api_auth.py
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from nas_organizer import api  # noqa: E402
from nas_organizer.config import Config  # noqa: E402
from nas_organizer.db import Database  # noqa: E402


def build_client(tmp: Path):
    db_path = tmp / "data" / "test.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = Config({"database": {"path": str(db_path)}, "auth": {"max_failed_attempts": 3}})
    db = Database(db_path)
    app = api.setup(config, db)
    client = TestClient(app)
    password_file = tmp / "data" / "INITIAL_PASSWORD.txt"
    password = [line.split("：", 1)[1].strip() for line in password_file.read_text(encoding="utf-8").splitlines()
                if line.startswith("密码：")][0]
    return client, password, db


def check_all():
    with tempfile.TemporaryDirectory() as d:
        client, password, db = build_client(Path(d))

        # 1. 状态接口公开
        r = client.get("/api/auth/status")
        assert r.status_code == 200 and r.json()["auth_required"] is True, r.text

        # 2. 未登录访问业务接口被拦截
        r = client.get("/api/stats")
        assert r.status_code == 401, r.text
        assert r.headers.get("WWW-Authenticate") == "Bearer"

        # 3. 安全响应头
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("Cache-Control") == "no-store"

        # 4. 错误密码被拒绝
        r = client.post("/api/auth/login", json={"password": "wrong-password"})
        assert r.status_code == 401, r.text

        # 5. 正确密码登录
        r = client.post("/api/auth/login", json={"password": password})
        assert r.status_code == 200, r.text
        token = r.json()["token"]
        headers = {"Authorization": "Bearer " + token}

        # 6. 带 Token 可访问业务接口
        r = client.get("/api/stats", headers=headers)
        assert r.status_code == 200, r.text
        assert "total" in r.json()

        # 7. 伪造 Token 被拒绝
        r = client.get("/api/stats", headers={"Authorization": "Bearer forged.token"})
        assert r.status_code == 401, r.text

        # 8. 危险操作未带 confirm=true 时拒绝
        r = client.post("/api/plans/1/execute", headers=headers, json={})
        assert r.status_code == 400 and "confirm" in r.text, r.text
        r = client.post("/api/rollback", headers=headers, json={})
        assert r.status_code == 400 and "confirm" in r.text, r.text

        # 9. 改密后旧 Token 失效
        r = client.post("/api/auth/password", headers=headers,
                        json={"old_password": password, "new_password": "NewSecurePass123!"})
        assert r.status_code == 200, r.text
        new_token = r.json()["token"]
        assert client.get("/api/stats", headers=headers).status_code == 401
        assert client.get("/api/stats", headers={"Authorization": "Bearer " + new_token}).status_code == 200

        # 10. 登出后 Token 失效
        client.post("/api/auth/logout", headers={"Authorization": "Bearer " + new_token})
        assert client.get("/api/stats", headers={"Authorization": "Bearer " + new_token}).status_code == 401

        db.close()

    print("API 认证集成测试全部通过")


if __name__ == "__main__":
    check_all()
