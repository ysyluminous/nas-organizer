"""D7 可选 extractor 插件接口：OCR + LLM 文档语义分类（默认空实现）。

设计约束（对应进度计划 #37）：
- 默认空实现：无 Tesseract / 无配置时 capability().ocr=false，主流程零依赖、零行为变化。
- 插件接口稳定：Extractor.extract(path) -> str | None；有新实现时注册即可，不改分类器。
- 服务端不接 LLM：OCR 只出文本，语义分类仍由规则/Agent 层完成，服务端不存密钥。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Protocol


class Extractor(Protocol):
    """文本提取器接口：返回文本内容，无法提取时返回 None。"""

    name: str

    def available(self) -> bool: ...
    def extract(self, path: str | Path) -> str | None: ...


class NullExtractor:
    """默认空实现：什么都不做，永远不可用。"""

    name = "none"

    def available(self) -> bool:
        return False

    def extract(self, path: str | Path) -> str | None:
        return None


class TesseractExtractor:
    """Tesseract OCR（可选）：需系统安装 tesseract，缺失时 available=false。"""

    name = "tesseract"

    def __init__(self, lang: str = "chi_sim+eng", timeout: int = 30):
        self.lang = lang
        self.timeout = timeout

    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def extract(self, path: str | Path) -> str | None:
        if not self.available():
            return None
        try:
            proc = subprocess.run(
                ["tesseract", str(path), "stdout", "-l", self.lang],
                capture_output=True, timeout=self.timeout,
            )
            if proc.returncode != 0:
                return None
            text = proc.stdout.decode("utf-8", errors="replace").strip()
            return text or None
        except (subprocess.TimeoutExpired, OSError):
            return None


def capability() -> dict[str, bool]:
    return {"ocr": shutil.which("tesseract") is not None}


def build_extractor(enabled: bool, provider: str = "tesseract",
                    lang: str = "chi_sim+eng") -> Extractor:
    """按配置构造 extractor；enabled=false 时返回空实现（零行为变化）。"""
    if not enabled:
        return NullExtractor()
    if provider == "tesseract":
        return TesseractExtractor(lang=lang)
    return NullExtractor()
