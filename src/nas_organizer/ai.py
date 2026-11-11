"""D4 可选 AI 兜底：默认关闭，只处理低置信度疑难件，失败自动降级。

设计约束（对应进度计划 #18 / #21）：
- ai.enabled 默认 false：关闭时零依赖加载、行为与 D3 完全一致。
- provider=ollama：本地推理，数据不出 NAS；仅处理置信度 < threshold 的文件。
- 断网 / 无 Ollama / 返回非法 JSON 时一律返回 None，由分类器保留「资源/低置信度」
  结果并进入人工复核队列（find_low_confidence），主流程绝不被 AI 拖垮。
- 只用标准库 urllib，不新增 pip 依赖。
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

from .config import Config

# 让模型只在这几个域里选，避免自由生成脏数据
SYSTEM_PROMPT = (
    "你是文件归类助手。根据文件名和所在目录，判断它最可能属于哪个归属域。"
    "只能从 [{domains}] 中选一个，并用 JSON 回答：{{\"domain\": \"...\", \"confidence\": 0.0~1.0}}。"
    "没有把握就给低 confidence。不要输出其它文字。"
)


class AIProvider:
    """AI 兜底基类：默认不可用。"""

    name = "none"

    def __init__(self, config: Config):
        self.threshold = float(config.get("ai.threshold", 0.8))
        self.domains = list(config.get("ai.domains", ["个人", "工作", "生活", "资源"]))

    def available(self) -> bool:
        return False

    def classify(self, filename: str, path: str, ext: str) -> tuple[str, float] | None:
        """返回 (domain, confidence)；不可用或失败返回 None。"""
        return None


class OllamaProvider(AIProvider):
    """通过本地 Ollama HTTP API 归类，失败静默降级。"""

    name = "ollama"

    def __init__(self, config: Config):
        super().__init__(config)
        self.base_url = str(config.get("ai.base_url", "http://127.0.0.1:11434")).rstrip("/")
        self.model = str(config.get("ai.model", "qwen2.5:3b"))
        self.timeout = float(config.get("ai.timeout", 15))

    def available(self) -> bool:
        return True  # 连接失败在 classify 内降级，不在启动时阻断

    def classify(self, filename: str, path: str, ext: str) -> tuple[str, float] | None:
        prompt = (
            f"文件名：{filename}\n所在目录：{path}\n扩展名：{ext}\n"
            + SYSTEM_PROMPT.format(domains="、".join(self.domains))
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "num_predict": 64},
        }
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.base_url}/api/generate",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
            answer = body.get("response")
            if isinstance(answer, str):
                result = json.loads(answer)
            elif isinstance(answer, dict):
                result = answer
            else:
                return None
            domain = str(result.get("domain", "")).strip()
            confidence = float(result.get("confidence", 0))
            if domain not in self.domains:
                return None
            if not 0 <= confidence <= 1:
                confidence = 0.0
            return domain, confidence
        except (urllib.error.URLError, socket.timeout, TimeoutError, ValueError,
                KeyError, TypeError, json.JSONDecodeError, OSError):
            # 断网、无 Ollama、超时、非法 JSON：一律降级为「不可用」
            return None


def build_provider(config: Config) -> AIProvider | None:
    """按配置构造 provider；ai.enabled=false 时返回 None（零依赖、零行为变化）。"""
    if not config.get("ai.enabled", False):
        return None
    name = str(config.get("ai.provider", "ollama")).lower()
    if name == "ollama":
        return OllamaProvider(config)
    return None
