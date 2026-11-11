"""四级判定链分类器：路径规则 → 扩展名 → 关键词 → 模型兜底."""

import re
import time
from pathlib import Path
from typing import Any

import yaml

from .config import Config
from .db import Database
from .models import FileRecord

# 内置默认规则（config 文件可覆盖）
DEFAULT_TYPE_RULES: dict[str, list[str]] = {
    "音乐": ["flac", "mp3", "wav", "m4a", "aac", "ogg", "ape", "wma", "opus"],
    "视频": ["mp4", "mkv", "avi", "mov", "rmvb", "wmv", "flv", "webm", "m4v", "ts"],
    "图书": ["epub", "mobi", "azw3", "djvu"],
    "图片": ["jpg", "jpeg", "png", "gif", "bmp", "webp", "heic", "tiff", "svg"],
    "软件": ["exe", "msi", "apk", "dmg", "deb", "rpm", "appimage"],
    "压缩包": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz"],
    "文档": ["doc", "docx", "xls", "xlsx", "ppt", "pptx", "csv", "md", "txt"],
}

DEFAULT_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "工作": [
        "合同", "报表", "项目", "方案", "报告", "会议", "客户", "财务", "审计",
        "招标", "发票", "工资", "考勤", "周报", "总结", "规划", "架构", "接口",
        "api", "src", "config", "deploy", "代码",
    ],
    "生活": [
        "照片", "家庭", "孩子", "旅游", "菜谱", "做饭", "健康", "体检",
        "保险", "房贷", "购物", "影视", "音乐", "电影", "电视剧",
    ],
    "个人": [
        "证件", "身份证", "护照", "学历", "证书", "简历", "银行卡",
        "密码", "账号", "笔记", "日记",
    ],
    "资源": [
        "课程", "教程", "电子书", "古籍", "合集", "素材", "下载",
        "收藏", "资源", "报告", "白皮书",
    ],
}

# 通用路径关键词：只有语义明确的目录才作为归属信号。
# 特定 NAS 的目录名请写进 config/domain_rules.yaml，不要改代码。
DEFAULT_DOMAIN_PATHS: dict[str, list[str]] = {
    "工作": ["/工作/", "/项目/", "/work/", "/project/"],
    "生活": ["/生活/", "/photos/", "/相册/", "/照片/", "/做饭/", "/life/"],
    "个人": ["/个人/", "/private/", "/documents/"],
    "资源": ["/电子书/", "/国学/", "/备份/", "/课程/", "/ebooks/", "/books/"],
}

# 路径规则 → 直接确定归属域和类型（最高优先级，语义明确的目录）
DEFAULT_PATH_RULES: list[tuple[str, str, str]] = [
    ("/电影/", "生活", "视频"),
    ("/电视剧/", "生活", "视频"),
    ("/影视/", "生活", "视频"),
    ("/音乐/", "生活", "音乐"),
    ("/电子书/", "资源", "图书"),
]


class Classifier:
    """对文件记录打上 domain / ftype / temp 三个标签."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.type_rules = self._load_yaml("classification.type_rules", DEFAULT_TYPE_RULES)
        self.domain_keywords = self._load_yaml(
            "classification.domain_rules", DEFAULT_DOMAIN_KEYWORDS, key="keywords"
        )
        self.domain_paths = self._load_yaml(
            "classification.domain_rules", DEFAULT_DOMAIN_PATHS, key="paths"
        )
        self.path_rules = self._load_path_rules()
        self.model_enabled = bool(config.get("classification.model.enabled", False))
        self.threshold = float(config.get("classification.model.threshold", 0.8))
        # 热卷来自配置中 role=hot 的挂载点，不再硬编码任何盘符
        self.hot_volumes = {
            str(v.get("name", "")) for v in (config.get("volumes", []) or [])
            if isinstance(v, dict) and str(v.get("role", "")).lower() == "hot"
        }

    def _load_yaml(self, cfg_key: str, default: dict, key: str | None = None) -> dict:
        path = self.config.get(cfg_key)
        if path and Path(str(path)).exists():
            try:
                with open(str(path), "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                return data.get(key, data) if key else data
            except Exception as exc:
                print(f"[WARN] 加载规则文件失败 {path}: {exc}")
        return default

    def _load_path_rules(self) -> list[tuple[str, str, str | None]]:
        rules: list[tuple[str, str, str | None]] = list(DEFAULT_PATH_RULES)
        path = self.config.get("classification.domain_rules")
        if path and Path(str(path)).exists():
            try:
                with open(str(path), "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                for rule in data.get("path_rules", []):
                    rules.append((rule["prefix"], rule["domain"], rule.get("ftype")))
            except Exception:
                pass
        return rules

    def classify_all(self) -> dict[str, int]:
        """对全部 active 文件分类，返回统计."""
        records = self.db.get_all_active()
        stats: dict[str, int] = {"total": len(records), "path": 0, "ext": 0, "keyword": 0, "model": 0, "manual": 0}
        for rec in records:
            ftype, domain, temp, confidence, by = self.classify(rec)
            self.db.update_classification(rec.id, ftype, domain, temp, confidence, by)
            stats[by] = stats.get(by, 0) + 1
        return stats

    def classify(self, record: FileRecord) -> tuple[str, str, str, float, str]:
        """返回 (ftype, domain, temp, confidence, classified_by)."""
        path_lower = record.path.lower()
        name_lower = record.filename.lower()

        # 1. 路径规则（最高优先级）
        for prefix, domain, ftype_hint in self.path_rules:
            if prefix.lower() in path_lower:
                ftype = ftype_hint or self._by_ext(record.ext)
                return ftype, domain, self._by_temp(record), 1.0, "path"

        # 2. 扩展名 → 类型
        ftype = self._by_ext(record.ext)
        # 3. 关键词 → 归属
        domain = self._by_keywords(name_lower, path_lower)
        if domain and ftype != "其他":
            return ftype, domain, self._by_temp(record), 0.95, "keyword"
        if ftype != "其他" and self._by_domain_path(path_lower):
            return ftype, self._by_domain_path(path_lower), self._by_temp(record), 0.9, "keyword"
        if domain:
            return ftype, domain, self._by_temp(record), 0.85, "keyword"
        if ftype != "其他":
            return ftype, "资源", self._by_temp(record), 0.7, "ext"

        # 4. 模型兜底（未启用时归为其他）
        if self.model_enabled:
            model_domain, conf = self._by_model(record)
            if conf >= self.threshold:
                return ftype or "其他", model_domain, self._by_temp(record), conf, "model"
            return ftype or "其他", "资源", self._by_temp(record), conf, "model"
        return "其他", "资源", self._by_temp(record), 0.3, "ext"

    def _by_ext(self, ext: str) -> str:
        ext = ext.lower().lstrip(".")
        for ftype, exts in self.type_rules.items():
            if ext in exts:
                return ftype
        # pdf/txt 默认归文档，路径规则可覆盖为图书
        if ext in ("pdf", "txt"):
            return "文档"
        return "其他"

    def _by_keywords(self, name: str, path: str) -> str | None:
        text = f"{name} {path}"
        for domain, keywords in self.domain_keywords.items():
            for kw in keywords:
                if kw.lower() in text:
                    return domain
        return None

    def _by_domain_path(self, path: str) -> str | None:
        for domain, prefixes in self.domain_paths.items():
            for prefix in prefixes:
                if prefix.lower() in path:
                    return domain
        return None

    def _by_temp(self, record: FileRecord) -> str:
        now = time.time()
        age = now - max(record.mtime, record.atime)
        if record.volume in self.hot_volumes:
            return "热"
        if age <= 90 * 86400:
            return "热"
        if age <= 365 * 86400:
            return "温"
        return "冷"

    def _by_model(self, record: FileRecord) -> tuple[str, float]:
        """模型兜底，当前为占位实现，返回低置信度."""
        # TODO: 集成 fastText / TF-IDF 模型
        return ("资源", 0.0)
