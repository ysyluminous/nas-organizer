"""认证模块测试：覆盖密码哈希、Token 签名、限流、改密轮换。

直接运行：PYTHONPATH=src python tests/test_auth.py
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.auth import (  # noqa: E402
    AuthError,
    AuthManager,
    generate_strong_password,
    hash_password,
    validate_password_strength,
    verify_password,
)


class TestPasswordHashing(unittest.TestCase):
    def test_hash_format_and_verify(self):
        stored = hash_password("MyPassword123!")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))
        self.assertEqual(len(stored.split("$")), 4)
        self.assertTrue(verify_password("MyPassword123!", stored))
        self.assertFalse(verify_password("wrong", stored))

    def test_salt_is_random(self):
        self.assertNotEqual(hash_password("same"), hash_password("same"))

    def test_malformed_hash_safe(self):
        for bad in ("", "abc", "pbkdf2_sha256$bad$xx$yy", "md5$1$$"):
            self.assertFalse(verify_password("anything", bad))

    def test_password_strength(self):
        self.assertIsNone(validate_password_strength("StrongPass123"))
        self.assertIsNotNone(validate_password_strength("short"))
        self.assertIsNotNone(validate_password_strength(" leadingspace"))

    def test_generated_password_has_all_groups(self):
        pwd = generate_strong_password()
        self.assertGreaterEqual(len(pwd), 16)
        self.assertTrue(any(c.islower() for c in pwd))
        self.assertTrue(any(c.isupper() for c in pwd))
        self.assertTrue(any(c.isdigit() for c in pwd))
        self.assertTrue(any(not c.isalnum() for c in pwd))


class TestAuthManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.saved_env = os.environ.pop("NAS_ORGANIZER_ADMIN_PASSWORD", None)
        self.auth = AuthManager(self.dir, {"max_failed_attempts": 3})

    def tearDown(self):
        self.tmp.cleanup()
        if self.saved_env is not None:
            os.environ["NAS_ORGANIZER_ADMIN_PASSWORD"] = self.saved_env

    def _initial_password(self):
        # 从生成的文件中解析初始密码
        text = (self.dir / "INITIAL_PASSWORD.txt").read_text(encoding="utf-8")
        return [line.split("：", 1)[1].strip() for line in text.splitlines() if line.startswith("密码：")][0]

    def test_initial_password_is_generated_and_saved(self):
        pwd = self._initial_password()
        self.assertGreaterEqual(len(pwd), 16)
        self.assertTrue(verify_password(pwd, self.auth._password_hash))
        self.assertTrue((self.dir / "auth.yaml").exists())
        self.assertTrue((self.dir / ".auth_secret").exists())

    def test_login_success_and_token(self):
        token = self.auth.verify_login(self._initial_password(), "1.2.3.4")
        self.assertEqual(self.auth.verify_token(token), "admin")
        self.assertEqual(self.auth.verify_token("garbage.token"), None)
        self.assertEqual(self.auth.verify_token(None), None)

    def test_tampered_token_rejected(self):
        token = self.auth.verify_login(self._initial_password(), "1.2.3.4")
        body, sig = token.split(".", 1)
        tampered = body[:-2] + "xx" + "." + sig
        self.assertIsNone(self.auth.verify_token(tampered))
        self.assertIsNone(self.auth.verify_token(body + ".forged_signature"))

    def test_wrong_password_raises(self):
        with self.assertRaises(AuthError):
            self.auth.verify_login("definitely-wrong", "9.9.9.9")

    def test_lockout_after_failures(self):
        for _ in range(3):
            try:
                self.auth.verify_login("wrong", "8.8.8.8")
            except AuthError:
                pass
        with self.assertRaises(AuthError) as ctx:
            self.auth.verify_login("wrong", "8.8.8.8")
        self.assertEqual(ctx.exception.status, 429)
        self.assertIsNotNone(ctx.exception.retry_after)

    def test_correct_password_not_counted_as_failure(self):
        self.auth.verify_login(self._initial_password(), "7.7.7.7")
        for _ in range(2):
            try:
                self.auth.verify_login("wrong", "7.7.7.7")
            except AuthError:
                pass
        # 未达上限，仍可正常登录
        self.assertTrue(self.auth.verify_token(self.auth.verify_login(self._initial_password(), "7.7.7.7")))

    def test_change_password_revokes_old_token(self):
        old_pwd = self._initial_password()
        old_token = self.auth.verify_login(old_pwd, "5.5.5.5")
        new_token = self.auth.change_password(old_pwd, "BrandNewPass456!", "5.5.5.5")
        self.assertIsNone(self.auth.verify_token(old_token))
        self.assertEqual(self.auth.verify_token(new_token), "admin")
        self.assertFalse((self.dir / "INITIAL_PASSWORD.txt").exists())
        # 旧密码失效，新密码生效
        with self.assertRaises(AuthError):
            self.auth.verify_login(old_pwd, "5.5.5.5")
        self.assertTrue(self.auth.verify_token(self.auth.verify_login("BrandNewPass456!", "5.5.5.5")))

    def test_password_change_persists_across_restart(self):
        new_pwd = "PersistedPass789!"
        self.auth.change_password(self._initial_password(), new_pwd, "6.6.6.6")
        restarted = AuthManager(self.dir, {})
        self.assertTrue(restarted.verify_token(restarted.verify_login(new_pwd, "6.6.6.6")))

    def test_env_password_only_on_first_start(self):
        tmp2 = tempfile.TemporaryDirectory()
        try:
            os.environ["NAS_ORGANIZER_ADMIN_PASSWORD"] = "EnvFirstPass123"
            a1 = AuthManager(Path(tmp2.name), {})
            self.assertTrue(a1.verify_token(a1.verify_login("EnvFirstPass123", "1.1.1.1")))
            # 模拟改密
            a1.change_password("EnvFirstPass123", "ChangedAfterEnv999", "1.1.1.1")
            a2 = AuthManager(Path(tmp2.name), {})
            # 环境变量仍在，但不能覆盖已持久化的改密结果
            with self.assertRaises(AuthError):
                a2.verify_login("EnvFirstPass123", "1.1.1.1")
            self.assertTrue(a2.verify_token(a2.verify_login("ChangedAfterEnv999", "1.1.1.1")))
        finally:
            tmp2.cleanup()
            os.environ.pop("NAS_ORGANIZER_ADMIN_PASSWORD", None)

    def test_logout_revokes_token(self):
        token = self.auth.verify_login(self._initial_password(), "4.4.4.4")
        self.auth.revoke_token(token)
        self.assertIsNone(self.auth.verify_token(token))


if __name__ == "__main__":
    unittest.main(verbosity=2)
