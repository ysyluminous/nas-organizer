"""认证与授权模块。

设计目标：
1. 密码绝不明文存储 —— PBKDF2-HMAC-SHA256 + 随机盐，21 万次迭代
2. 无状态签名 Token —— HMAC-SHA256 签名，防伪造、防篡改
3. 防暴力破解 —— 按 IP 计数，失败超限后指数冷却
4. 防时序攻击 —— 所有敏感比较使用 hmac.compare_digest
5. 首次启动自动生成强随机密码，杜绝默认弱口令

本模块只依赖 Python 标准库，不引入额外第三方包。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import string
import time
from pathlib import Path
from typing import Any

import yaml

# ---- 安全参数 ----
PBKDF2_ROUNDS = 210_000          # OWASP 2023 推荐 >= 600000，此处兼顾 NAS 低性能 CPU
MIN_PASSWORD_LEN = 8
TOKEN_TTL_SECONDS = 8 * 3600     # Token 有效期 8 小时
MAX_FAILED_ATTEMPTS = 5          # 连续失败次数上限
LOCK_BASE_SECONDS = 30           # 首次锁定时长，之后指数增长
LOCK_MAX_SECONDS = 3600          # 锁定上限 1 小时
MAX_PASSWORD_LEN = 1024          # 防止超长密码造成 DoS


class AuthError(Exception):
    """认证相关异常，message 为用户可读提示。"""

    def __init__(self, message: str, status: int = 401, retry_after: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retry_after = retry_after


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, rounds: int = PBKDF2_ROUNDS) -> str:
    """生成密码哈希，格式：pbkdf2_sha256$轮数$盐$哈希。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return f"pbkdf2_sha256${rounds}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """常量时间校验密码。任何解析异常都视为失败。"""
    try:
        algo, rounds_s, salt_s, hash_s = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        if rounds < 10_000 or rounds > 10_000_000:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _b64d(salt_s), rounds)
        return hmac.compare_digest(digest, _b64d(hash_s))
    except (ValueError, TypeError, UnicodeError):
        return False


def generate_strong_password(length: int = 20) -> str:
    """生成首次启动用的强随机密码，保证各类字符至少一位。"""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    groups = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*-_=+"),
    ]
    rest = [secrets.choice(alphabet) for _ in range(max(0, length - len(groups)))]
    chars = groups + rest
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def validate_password_strength(password: str) -> str | None:
    """返回 None 表示通过，否则返回错误提示。"""
    if not isinstance(password, str):
        return "密码必须是字符串"
    if len(password) < MIN_PASSWORD_LEN:
        return f"密码长度不能少于 {MIN_PASSWORD_LEN} 位"
    if len(password) > MAX_PASSWORD_LEN:
        return "密码过长"
    if password.strip() != password:
        return "密码首尾不能包含空格"
    return None


class AuthManager:
    """统一管理密码、Token 与登录限流。

    密码来源优先级：
      1. data/auth.yaml 中的 password_hash（持久化，改密后以此为准）
      2. 环境变量 NAS_ORGANIZER_ADMIN_PASSWORD（仅首次启动、尚无 auth.yaml 时引导用）
      3. 以上都没有 → 自动生成随机密码写入 data/INITIAL_PASSWORD.txt（权限 600）

    Token 签名密钥持久化在 data/.auth_secret；改密时轮换密钥，使所有旧 Token 立即失效。
    """

    def __init__(self, data_dir: Path, config: dict[str, Any] | None = None):
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        cfg = config or {}
        self._ttl = int(cfg.get("token_ttl_seconds", TOKEN_TTL_SECONDS))
        self._max_failed = int(cfg.get("max_failed_attempts", MAX_FAILED_ATTEMPTS))
        self._secret_path = self._data_dir / ".auth_secret"
        self._password_file = self._data_dir / "auth.yaml"
        self._initial_password_file = self._data_dir / "INITIAL_PASSWORD.txt"

        self._secret = self._load_or_create_secret()
        self._password_hash = self._load_password_hash()
        self._failed: dict[str, dict[str, int | float]] = {}
        self._revoked: set[str] = set()

    # ---------- 密码管理 ----------

    def _load_or_create_secret(self) -> bytes:
        if self._secret_path.exists():
            try:
                raw = _b64d(self._secret_path.read_text(encoding="ascii").strip())
                if len(raw) >= 32:
                    return raw
            except (ValueError, OSError):
                pass
        secret = secrets.token_bytes(32)
        self._secret_path.write_text(_b64e(secret), encoding="ascii")
        self._harden_file(self._secret_path)
        return secret

    def _load_password_hash(self) -> str:
        # 1. 已持久化的哈希优先：保证改密结果在重启后仍然有效，
        #    不会被遗留的环境变量密码覆盖。
        if self._password_file.exists():
            try:
                data = yaml.safe_load(self._password_file.read_text(encoding="utf-8")) or {}
                stored = str(data.get("password_hash", ""))
                if stored.startswith("pbkdf2_sha256$"):
                    return stored
            except (OSError, yaml.YAMLError):
                pass

        # 2. 首次启动且无持久化哈希时，读取环境变量引导密码并落盘
        env_pwd = os.getenv("NAS_ORGANIZER_ADMIN_PASSWORD", "").strip()
        if env_pwd:
            stored = hash_password(env_pwd)
            self._save_password_hash(stored)
            print("[安全] 已使用环境变量 NAS_ORGANIZER_ADMIN_PASSWORD 初始化密码；"
                  "该环境变量现已不再生效，建议从部署配置中移除。", flush=True)
            return stored

        # 3. 首次启动：生成随机密码，写入受限文件，并打印到日志
        password = generate_strong_password()
        stored = hash_password(password)
        self._save_password_hash(stored)
        self._initial_password_file.write_text(
            "NAS Organizer 初始管理员密码\n"
            "============================\n"
            f"账号：admin\n"
            f"密码：{password}\n\n"
            "该文件权限为 600，请登录后立即修改密码。\n"
            "修改密码后本文件可删除。\n",
            encoding="utf-8",
        )
        self._harden_file(self._initial_password_file)
        # 醒目输出到容器日志（不写入普通日志文件）
        print("\n" + "=" * 60, flush=True)
        print("[安全] 首次启动，已生成随机管理员密码：", password, flush=True)
        print(f"[安全] 同时保存到 {self._initial_password_file}", flush=True)
        print("[安全] 请登录后立即在「安全设置」中修改密码。", flush=True)
        print("=" * 60 + "\n", flush=True)
        return stored

    def _save_password_hash(self, stored: str) -> None:
        self._password_file.write_text(
            "# NAS Organizer 认证配置\n"
            "# 此文件包含密码哈希，请勿提交到版本库或分享给他人。\n"
            f"password_hash: {stored}\n",
            encoding="utf-8",
        )
        self._harden_file(self._password_file)

    @staticmethod
    def _harden_file(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            # Windows 上 chmod 有限，尽力而为
            pass

    def is_password_set(self) -> bool:
        return bool(self._password_hash)

    def verify_login(self, password: str, client_ip: str = "unknown") -> str:
        """校验账号密码，成功返回签名 Token，失败抛 AuthError。"""
        self._check_lock(client_ip)
        if not isinstance(password, str) or len(password) > MAX_PASSWORD_LEN:
            self._record_failure(client_ip)
            raise AuthError("密码格式错误", 400)

        if not verify_password(password, self._password_hash):
            self._record_failure(client_ip)
            raise AuthError("密码错误")

        self._failed.pop(client_ip, None)
        return self.create_token()

    def change_password(self, old_password: str, new_password: str, client_ip: str = "unknown") -> str:
        """修改密码并轮换密钥，返回新 Token。旧 Token 全部失效。"""
        self._check_lock(client_ip)
        if not verify_password(old_password, self._password_hash):
            self._record_failure(client_ip)
            raise AuthError("原密码错误", 403)

        err = validate_password_strength(new_password)
        if err:
            raise AuthError(err, 400)
        if verify_password(new_password, self._password_hash):
            raise AuthError("新密码不能与旧密码相同", 400)

        self._password_hash = hash_password(new_password)
        self._save_password_hash(self._password_hash)
        # 轮换签名密钥 → 所有已签发 Token 立即失效
        self._secret = secrets.token_bytes(32)
        self._secret_path.write_text(_b64e(self._secret), encoding="ascii")
        self._harden_file(self._secret_path)
        self._failed.pop(client_ip, None)

        # 清除初始密码提示文件
        if self._initial_password_file.exists():
            try:
                self._initial_password_file.unlink()
            except OSError:
                pass
        return self.create_token()

    # ---------- 限流 ----------

    def _check_lock(self, client_ip: str) -> None:
        rec = self._failed.get(client_ip)
        if not rec:
            return
        count = int(rec.get("count", 0))
        until = float(rec.get("until", 0))
        if count >= self._max_failed and time.time() < until:
            raise AuthError(
                f"尝试次数过多，请在 {int(until - time.time())} 秒后重试",
                429,
                retry_after=int(until - time.time()) + 1,
            )
        # 仅在「曾经锁定且锁定已过期」时清空记录。
        # 若未锁定（until=0）时也清空，失败计数永远无法累积，限流形同虚设。
        if until and time.time() >= until:
            self._failed.pop(client_ip, None)

    def _record_failure(self, client_ip: str) -> None:
        rec = self._failed.setdefault(client_ip, {"count": 0, "until": 0.0, "last": 0.0})
        count = int(rec["count"]) + 1
        # 仅在连续失败时累加，间隔超过 10 分钟则重置
        if time.time() - float(rec.get("last", 0)) > 600:
            count = 1
        rec["count"] = count
        rec["last"] = time.time()
        if count >= self._max_failed:
            delay = min(LOCK_BASE_SECONDS * (2 ** (count - self._max_failed)), LOCK_MAX_SECONDS)
            rec["until"] = time.time() + delay
        print(f"[安全] 登录失败 IP={client_ip} 连续失败 {count} 次", flush=True)

    # ---------- Token ----------

    def create_token(self, username: str = "admin") -> str:
        now = int(time.time())
        payload = {
            "u": username,
            "iat": now,
            "exp": now + self._ttl,
            "n": secrets.token_hex(8),
        }
        body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        sig = _b64e(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
        return f"{body}.{sig}"

    def verify_token(self, token: str | None) -> str | None:
        """校验 Token 签名与有效期，成功返回用户名，失败返回 None。"""
        if not token or not isinstance(token, str):
            return None
        token = token.strip()
        if token in self._revoked:
            return None
        try:
            body, sig = token.split(".", 1)
            expected = _b64e(hmac.new(self._secret, body.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(sig, expected):
                return None
            payload = json.loads(_b64d(body))
            exp = int(payload.get("exp", 0))
            if exp < time.time():
                return None
            username = payload.get("u")
            return username if isinstance(username, str) else None
        except (ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError):
            return None

    def revoke_token(self, token: str | None) -> None:
        """登出：将 Token 加入内存吊销表（重启后失效，属正常行为）。"""
        if token and isinstance(token, str):
            self._revoked.add(token.strip())

    @property
    def token_ttl(self) -> int:
        return self._ttl
