"""D4 反馈学习：从人工纠正中本地学习，越用越准。

设计约束（对应进度计划 #14）：
- 规则优先：path / alias / ext / keyword 规则命中的结果不受反馈影响；
  反馈只在分类器默认给出低置信度（如仅凭扩展名归入「资源」）时参与。
- 反馈只调整关键词权重，不新增文件读写、不改动用户数据。
- 可重置：reset_feedback() 一键清空。
"""

from __future__ import annotations

import re
from collections import defaultdict

from .config import Config
from .db import Database

# 从文件名/路径里抽关键词时忽略的通用词（避免把「新建文件夹」学成信号）
STOPWORDS = {
    "新建文件夹", "新建", "文件", "文件夹", "下载", "download", "temp", "tmp",
    "未命名", "untitled", "copy", "副本", "final", "最新", "backup", "备份",
}


def extract_keywords(text: str, max_keywords: int = 12) -> list[str]:
    """从路径/文件名抽取中文词块与英文单词作为反馈关键词。

    - 中文：连续 2–8 汉字切一块（不做分词，靠人工纠正后的重复出现形成信号）
    - 英文/数字：长度 ≥2 的单词
    - 去停用词、去重、保序，最多 max_keywords 个
    """
    text = text.lower()
    raw: list[str] = []
    raw += re.findall(r"[\u4e00-\u9fff]{2,8}", text)
    raw += re.findall(r"[a-z][a-z0-9_-]{1,}", text)
    seen: set[str] = set()
    out: list[str] = []
    for kw in raw:
        if kw in STOPWORDS or kw in seen or len(kw) < 2:
            continue
        seen.add(kw)
        out.append(kw)
        if len(out) >= max_keywords:
            break
    return out


class FeedbackLearner:
    """读取反馈表，为分类器提供关键词 → 域/类型的加权信号。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.enabled = bool(config.get("feedback.enabled", True))
        self.max_keywords = int(config.get("feedback.max_keywords", 12))
        self._cache: dict[str, dict[str, tuple[str, int]]] | None = None

    def reload(self) -> None:
        """强制从数据库重载反馈（新增反馈后调用）。"""
        self._cache = None

    def _weights(self, field: str) -> dict[str, tuple[str, int]]:
        """返回 {keyword: (value, hits)}，hits 越高信号越强。"""
        if not self.enabled:
            return {}
        if self._cache is None:
            self._cache = {}
            for row in self.db.get_feedback(limit=5000):
                bucket = self._cache.setdefault(row["field"], {})
                kw = row["keyword"]
                prev = bucket.get(kw)
                if prev is None or row["hits"] > prev[1]:
                    bucket[kw] = (row["value"], int(row["hits"]))
        return self._cache.get(field, {})

    def suggest(self, text: str, field: str) -> tuple[str, int] | None:
        """若文本命中人工反馈过的关键词，返回 (建议值, 累计命中次数)。

        多个关键词冲突时取 hits 最高者；同分时取更长的关键词（更具体）。
        """
        weights = self._weights(field)
        if not weights:
            return None
        text_lower = text.lower()
        best: tuple[str, int, int] | None = None  # (hits, len, value)
        for kw, (value, hits) in weights.items():
            if kw in text_lower:
                cand = (hits, len(kw), value)
                if best is None or cand[:2] > best[:2]:
                    best = cand
        return (best[2], best[0]) if best else None

    def learn_from_correction(self, path: str, field: str, value: str) -> int:
        """把一次人工纠正写回反馈表，返回写入的关键词数。

        field ∈ {domain, ftype}；value 为人工指定的归属域或类型。
        """
        if field not in ("domain", "ftype"):
            raise ValueError("field 必须是 domain 或 ftype")
        kws = extract_keywords(path, self.max_keywords)
        for kw in kws:
            self.db.add_feedback(kw, field, value)
        self.reload()
        return len(kws)

    def reset(self) -> int:
        """清空全部反馈并重载，返回删除条数。"""
        count = self.db.reset_feedback()
        self.reload()
        return count
