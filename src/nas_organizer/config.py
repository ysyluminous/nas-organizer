"""配置加载与管理."""

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "database": {"path": "data/nas_organizer.db"},
    "volumes": [],
    "write_targets": [],
    "classification": {
        "type_rules": "config/type_rules.yaml",
        "domain_rules": "config/domain_rules.yaml",
        "folder_aliases": "config/folder_aliases.yaml",
        "temperature_rules": "config/temperature_rules.yaml",
        "model": {"enabled": False, "threshold": 0.8},
    },
    "dedupe": {"head_size": 65536, "tail_size": 65536, "workers": 2},
    "scanner": {"workers": 4, "exclude_dirs": ["#recycle", "@eaDir", ".git", "node_modules"]},
    "backup": {"hot": {"enabled": False}, "cold": {"enabled": False}},
    "auth": {"token_ttl_seconds": 28800, "max_failed_attempts": 5},
    "schedule": {"enabled": False, "config": "config/schedule.yaml"},
    "security": {
        "allowed_paths": [],
        "strict": True,
        "audit_log": "data/security_audit.jsonl",
    },
    # D4 人工反馈学习：默认开启，规则优先，只影响低置信度文件
    "feedback": {"enabled": True, "max_keywords": 12},
    # D4 可选 AI 兜底：默认关闭，关闭时零依赖、行为与之前完全一致
    "ai": {
        "enabled": False,
        "provider": "ollama",
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen2.5:3b",
        "timeout": 15,
        "threshold": 0.8,          # 仅处理置信度低于此值的疑难件
        "domains": ["个人", "工作", "生活", "资源"],
    },
    # D4 媒体元数据感知：无可选依赖时优雅降级，绝不报错中断主流程
    "media": {"exif_enabled": True, "id3_enabled": True},
    # D4 感知哈希相似图片：默认开启但只在图片组内计算
    "perceptual": {"enabled": True, "threshold": 10, "hash_size": 8},
    "web": {"host": "0.0.0.0", "port": 8765},
}


class Config:
    """配置对象，支持点号访问."""

    def __init__(self, data: dict[str, Any], base_dir: Path | None = None):
        self._data = data
        self._base_dir = base_dir or Path.cwd()

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        """从 YAML 加载配置，未指定路径时使用默认配置."""
        if path is None:
            path = os.getenv("NAS_ORGANIZER_CONFIG", "config/config.yaml")
        p = Path(path)
        if p.exists():
            with p.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            merged = _deep_merge(DEFAULT_CONFIG, data)
            return cls(merged, p.parent.parent)
        return cls(_deep_merge(DEFAULT_CONFIG, {}), Path.cwd())

    def get(self, key: str, default: Any = None) -> Any:
        """点号分隔的键访问，如 config.get('web.port')."""
        node: Any = self._data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def resolve(self, path: str) -> Path:
        """相对路径基于项目根目录解析."""
        p = Path(path)
        return p if p.is_absolute() else (self._base_dir / p)


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并字典."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
