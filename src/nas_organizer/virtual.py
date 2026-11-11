"""D5 多维虚拟目录：不移动文件，用标签实现多视角归类。

设计约束（对应进度计划 #23）：
- 同一文件可同时属于多个视图（如「工作」+「2024」+「合同」），源文件零改动。
- 标签数据落在已有 tags / file_tags 表，自动规则打标后仍可人工增删。
- 只读生成 / 打标都走索引库，不触碰用户文件；路径仍需 PathGuard 校验。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .config import Config
from .db import Database
from .models import FileRecord


class VirtualDirectory:
    """基于标签的多维视图管理。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def tag(self, file_id: int, tag: str) -> dict[str, Any]:
        rec = self.db.get_file(file_id)
        if not rec:
            raise ValueError(f"文件 {file_id} 不存在")
        self.db.tag_file(file_id, tag)
        return {"ok": True, "file_id": file_id, "tag": tag,
                "tags": self.db.get_file_tags(file_id)}

    def untag(self, file_id: int, tag: str) -> dict[str, Any]:
        self.db.untag_file(file_id, tag)
        return {"ok": True, "file_id": file_id, "tag": tag,
                "tags": self.db.get_file_tags(file_id)}

    def auto_tag(self, progress: Any = None) -> dict[str, Any]:
        """按现有分类维度（域/类型/温度/年份）自动打标，幂等可重复运行。"""
        records = self.db.get_all_active()
        applied = 0
        for idx, rec in enumerate(records, 1):
            tags: list[str] = []
            if rec.domain:
                tags.append(f"域:{rec.domain}")
            if rec.ftype:
                tags.append(f"类型:{rec.ftype}")
            if rec.temp:
                tags.append(f"温度:{rec.temp}")
            # 年份维度：按 mtime，让同一文件自然进入「时间轴」视图
            if rec.mtime:
                from datetime import datetime
                tags.append(f"年份:{datetime.fromtimestamp(rec.mtime).year}")
            for tag in tags:
                self.db.tag_file(rec.id, tag)
                applied += 1
            if progress and idx % 500 == 0:
                progress(idx, len(records))
        if progress:
            progress(len(records), len(records))
        return {"files": len(records), "tag_assignments": applied,
                "tags": len(self.db.list_tags()), "note": "源文件零改动"}

    def list_views(self) -> dict[str, Any]:
        tags = self.db.list_tags()
        groups: dict[str, list[dict]] = {}
        for t in tags:
            prefix = t["name"].split(":", 1)[0] if ":" in t["name"] else "自定义"
            groups.setdefault(prefix, []).append(t)
        return {"total_tags": len(tags), "groups": groups, "tags": tags}

    def view(self, tag: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        files = self.db.files_by_tag(tag, limit, offset)
        return {"tag": tag, "count": len(files), "files": files,
                "note": "虚拟视图：按标签检索索引，未移动任何文件"}

    def stats(self) -> dict[str, Any]:
        tags = self.db.list_tags()
        by_prefix: Counter[str] = Counter()
        for t in tags:
            prefix = t["name"].split(":", 1)[0] if ":" in t["name"] else "自定义"
            by_prefix[prefix] += 1
        return {"total_tags": len(tags), "views": dict(by_prefix),
                "total_assignments": sum(t["file_count"] for t in tags)}

    def delete_tag(self, tag: str) -> dict[str, Any]:
        removed = self.db.delete_tag(tag)
        return {"ok": True, "tag": tag, "assignments_removed": removed,
                "note": "仅删除标签，未触碰源文件"}
