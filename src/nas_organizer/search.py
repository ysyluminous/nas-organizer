"""D7 轻量语义搜索：SQLite FTS5 全文检索（文件名 + 路径 + 标签）。

设计约束（对应进度计划 #35）：
- 不引入向量库/embedding：用 SQLite 内置 FTS5 做文件名/路径/标签的全文检索，
  万级文件查询 <1s；语义扩展靠中文词块切分 + 标签（虚拟目录已打标）。
- 只读：查询不改任何文件；索引表由 build_index() 显式重建（写库，需调用方确认）。
- FTS5 不可用时（极老 SQLite）优雅降级为 LIKE 查询，主流程不中断。
"""

from __future__ import annotations

import re
from typing import Any

from .config import Config
from .db import Database


class SearchIndex:
    """FTS5 全文检索 + LIKE 降级。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db

    def _fts_available(self) -> bool:
        try:
            self.db.connect().execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
            self.db.connect().execute("DROP TABLE _fts_probe")
            self.db.connect().commit()
            return True
        except Exception:
            return False

    def ensure_index(self) -> bool:
        """确保 FTS 表存在；返回是否使用 FTS5。

        unicode61 把连续中文当作一个整词，中文子串（如"报表"匹配"财务报表"）
        无法命中。因此额外存一列 tokens：中文按 2-gram 拆开、英文按词，
        查询时也用同样切法 MATCH 这一列。
        """
        if not self._fts_available():
            return False
        self.db.connect().executescript(
            """CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
                   path, filename, tags, tokens, content='', tokenize='unicode61');"""
        )
        self.db.connect().commit()
        return True

    def build_index(self) -> dict[str, Any]:
        """从 files + file_tags 重建全文索引（写库，只读文件）。"""
        use_fts = self.ensure_index()
        if not use_fts:
            return {"fts5": False, "indexed": 0, "note": "SQLite 不支持 FTS5，查询将降级为 LIKE"}
        conn = self.db.connect()
        with self.db.transaction() as c:
            c.execute("DELETE FROM files_fts")
            rows = c.execute(
                """SELECT f.id, f.path, f.filename,
                          COALESCE(GROUP_CONCAT(t.name, ' '), '') AS tags
                   FROM files f LEFT JOIN file_tags ft ON ft.file_id=f.id
                   LEFT JOIN tags t ON t.id=ft.tag_id
                   WHERE f.status='active' GROUP BY f.id"""
            ).fetchall()
            for r in rows:
                tokens = " ".join(self._index_tokens(
                    f"{r['filename']} {r['path']} {r['tags']}"))
                c.execute(
                    "INSERT INTO files_fts(rowid, path, filename, tags, tokens) VALUES (?, ?, ?, ?, ?)",
                    (r["id"], r["path"], r["filename"], r["tags"], tokens),
                )
        return {"fts5": True, "indexed": len(rows)}

    @staticmethod
    def _index_tokens(text: str) -> list[str]:
        """建索引用的 token：中文切 2-gram + 完整词块，英文按词。

        中文 2-gram 让"报表"命中"财务报表"；完整词块让"财务报表"优先精确匹配。
        """
        text = text.lower()
        tokens: list[str] = []
        for block in re.findall(r"[\u4e00-\u9fff]{2,}", text):
            tokens.append(block)
            tokens.extend(block[i:i + 2] for i in range(len(block) - 1))
        tokens.extend(re.findall(r"[a-z0-9_-]{2,}", text))
        return tokens

    @staticmethod
    def _tokens(query: str) -> list[str]:
        """查询 token：与建索引同样的切法，保证中文子串可匹配。"""
        tokens = SearchIndex._index_tokens(query)
        return tokens or [query]

    def search(self, query: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        """全文检索；返回命中文件（只读）。FTS5 不可用时降级 LIKE。"""
        if not query.strip():
            return {"query": query, "engine": "none", "count": 0, "results": []}
        conn = self.db.connect()
        # 尝试 FTS5
        try:
            tokens = self._tokens(query)
            # 用 AND 要求所有查询 token 都命中，避免 2-gram 展开后误召回过多；
            # 若查询词被拆成多个 2-gram，同组内用 OR 保证覆盖。
            match = " AND ".join(
                "(" + " OR ".join(f'"{t}"' for t in SearchIndex._index_tokens(tok)) + ")"
                for tok in tokens if tok
            )
            rows = conn.execute(
                """SELECT f.id, f.path, f.filename, f.ftype, f.domain, f.temp,
                          bm25(files_fts) AS score
                   FROM files_fts JOIN files f ON f.id=files_fts.rowid
                   WHERE files_fts.tokens MATCH ? AND f.status='active'
                   ORDER BY score LIMIT ? OFFSET ?""",
                (match, limit, offset),
            ).fetchall()
            return {
                "query": query, "engine": "fts5", "count": len(rows),
                "results": [dict(r) for r in rows],
            }
        except Exception:
            pass
        # LIKE 降级
        like = f"%{query}%"
        rows = conn.execute(
            """SELECT id, path, filename, ftype, domain, temp FROM files
               WHERE status='active' AND (path LIKE ? OR filename LIKE ?)
               ORDER BY id LIMIT ? OFFSET ?""",
            (like, like, limit, offset),
        ).fetchall()
        return {"query": query, "engine": "like", "count": len(rows),
                "results": [dict(r) for r in rows],
                "note": "FTS5 未建索引或不可用，已降级为 LIKE"}

    def stats(self) -> dict[str, Any]:
        conn = self.db.connect()
        try:
            n = conn.execute("SELECT COUNT(*) AS n FROM files_fts").fetchone()["n"]
            return {"fts5": True, "indexed": int(n)}
        except Exception:
            return {"fts5": False, "indexed": 0}
