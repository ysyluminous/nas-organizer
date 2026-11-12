"""应用级路径白名单（allowed_paths）。

设计目标：
1. 即使容器挂载了整个共享目录，应用也只允许访问显式声明的路径
2. 防 `..` 穿越、防符号链接逃逸（统一用 Path.resolve() 归一化后比对）
3. 未配置时从 volumes / write_targets / data 目录自动推导默认值，兼容存量部署
4. 所有越界尝试写入独立审计日志（JSONL），不污染业务日志

安全边界说明：这是容器挂载隔离之上的**第二层**防护，不能替代只读挂载。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Iterable

from .config import Config


class PathViolation(ValueError):
    """路径越界异常。message 可直接展示给用户。"""

    def __init__(self, path: str, reason: str = "不在允许的目录范围内"):
        super().__init__(f"路径越界：{path}（{reason}）")
        self.path = path
        self.reason = reason


class PathGuard:
    """路径白名单校验器。"""

    def __init__(self, config: Config):
        self.config = config
        self.strict = bool(config.get("security.strict", True))
        configured = config.get("security.allowed_paths", []) or []
        self.explicit = bool(configured)
        self.allowed: tuple[Path, ...] = self._build_roots(configured)
        self._audit_path = config.resolve(
            str(config.get("security.audit_log", "data/security_audit.jsonl"))
        )

    def _build_roots(self, configured: Iterable[str]) -> tuple[Path, ...]:
        """显式配置优先；否则从现有配置自动推导，保证升级不破坏存量。"""
        raw: list[str] = [str(p) for p in configured if str(p).strip()]
        if not raw:
            # Web 动态绑定的扫描卷也要纳入白名单，否则新绑定目录扫描时会被拦下
            try:
                from .mounts import effective_volumes
                volumes_src = effective_volumes(self.config)
            except Exception:
                volumes_src = self.config.get("volumes", []) or []
            for vol in volumes_src:
                if vol.get("path"):
                    raw.append(str(vol["path"]))
            for target in self.config.get("write_targets", []) or []:
                if target.get("path"):
                    raw.append(str(target["path"]))
            # 应用自身数据目录（数据库、回收站、认证文件）必须可达
            raw.append(str(self.config.resolve("data")))
            raw.append(str(self.config.resolve("logs")))
            if self.config.get("backup.hot.target"):
                raw.append(str(self.config.get("backup.hot.target")))

        roots: list[Path] = []
        for item in raw:
            p = Path(item).expanduser()
            if not p.is_absolute():
                p = self.config.resolve(str(p))
            resolved = p.resolve()
            if resolved not in roots:
                roots.append(resolved)
        return tuple(roots)

    @property
    def roots(self) -> list[str]:
        return [str(p) for p in self.allowed]

    def check(
        self,
        path: str | Path,
        *,
        source: str = "api",
        action: str = "read",
        write: bool = False,
    ) -> Path:
        """校验路径；越界时按 strict 配置抛异常或仅记录审计。返回归一化路径。"""
        p = Path(path).expanduser()
        if not p.is_absolute():
            p = self.config.resolve(str(p))
        normalized = p.resolve()

        if any(self._is_under(normalized, root) for root in self.allowed):
            return normalized

        self._audit(str(p), source, action, write, allowed=False)
        if self.strict:
            raise PathViolation(str(p))
        return normalized

    def check_many(
        self,
        paths: Iterable[str | Path],
        *,
        source: str = "api",
        action: str = "read",
        write: bool = False,
    ) -> list[Path]:
        return [self.check(p, source=source, action=action, write=write) for p in paths]

    def is_allowed(self, path: str | Path) -> bool:
        try:
            self.check(path)
            return True
        except PathViolation:
            return False

    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def _audit(
        self, path: str, source: str, action: str, write: bool, allowed: bool
    ) -> None:
        entry = {
            "ts": time.time(),
            "source": source,
            "action": action,
            "path": path,
            "write": bool(write),
            "allowed": bool(allowed),
        }
        try:
            self._audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self._audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            try:
                self._audit_path.chmod(0o600)
            except OSError:
                pass
        except OSError:
            # 审计失败不能影响主流程，但要提示运维
            print(f"[安全警告] 无法写入审计日志: {self._audit_path}", flush=True)
