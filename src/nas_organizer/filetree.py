"""文件树与目录浏览（Windows 资源管理器式）.

从已索引的 files 表构建目录树，而不是实时遍历磁盘——大盘扫描后
目录树查询必须是毫秒级。核心能力：
- roots()：顶层（各扫描卷）+ 每个卷的文件/目录统计。
- children(path)：给定目录的直接子目录 + 直接文件（分页），模仿资源管理器的
  "进入一级目录"行为。
- 所有路径做前缀归属校验：只允许访问生效扫描卷内的目录，防止越权浏览。

目录信息来自 DB 的 path 列（POSIX '/' 分隔）；Windows 采集的路径可能是反斜杠，
统一按 os.sep 与 '/' 两种分隔符归一化。
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import Any

from .config import Config
from .db import Database


def _norm(path: str) -> str:
    """把路径归一化为 POSIX 风格、无尾随斜杠。"""
    p = str(path or "").replace("\\", "/").strip()
    while len(p) > 1 and p.endswith("/"):
        p = p.rstrip("/")
    return p


def _parent(path: str) -> str:
    """返回父目录（POSIX 语义），根目录返回空串。"""
    p = _norm(path)
    if p in ("", "/"):
        return ""
    parent = str(PurePosixPath(p).parent)
    return "" if parent in (".", "/") else _norm(parent)


def _basename(path: str) -> str:
    p = _norm(path)
    return p.rstrip("/").rsplit("/", 1)[-1] or (p or "/")


def effective_roots(config: Config) -> list[dict[str, Any]]:
    from .mounts import effective_volumes
    roots = []
    for v in effective_volumes(config):
        if v.get("path"):
            roots.append({
                "name": v["name"],
                "path": _norm(str(v["path"])),
                "role": v.get("role", "cold"),
                "source": v.get("source", "static"),
            })
    return roots
    return roots


def _in_roots(path: str, roots: list[dict[str, Any]]) -> bool:
    p = _norm(path)
    for r in roots:
        root = r["path"]
        if p == root or p.startswith(root.rstrip("/") + "/"):
            return True
    return False


class FileTree:
    """基于索引的目录树查询器（只读）。"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.roots = effective_roots(config)

    def root_map(self) -> dict[str, str]:
        """path -> volume 名称映射，用于判断某目录属于哪个卷。"""
        return {r["path"]: r["name"] for r in self.roots}

    def name_for_path(self, path: str) -> str:
        p = _norm(path)
        for r in self.roots:
            if p == r["path"]:
                return r["name"]
        return _basename(p)

    def _stats_for_prefix(self, prefix: str) -> dict[str, int]:
        """统计 prefix 目录下的文件数与直接子目录数（SQL LIKE 前缀）。"""
        conn = self.db.connect()
        like = prefix.rstrip("/") + "/%"
        row = conn.execute(
            "SELECT COUNT(*) n, COUNT(DISTINCT substr(path, ?)) d FROM files "
            "WHERE status='active' AND path LIKE ?",
            (len(prefix.rstrip("/")) + 2, like),
        ).fetchone()
        # d 统计可能包含文件本身所在层级，下面用 children 精算目录数
        return {"files": int(row["n"]), "dirs_hint": int(row["d"])}

    def roots_listing(self) -> dict[str, Any]:
        """返回顶层卷列表，每个卷带文件计数。"""
        conn = self.db.connect()
        counts: dict[str, int] = {}
        for row in conn.execute(
            "SELECT volume, COUNT(*) n FROM files WHERE status='active' GROUP BY volume"
        ):
            counts[row["volume"]] = int(row["n"])
        items = []
        for r in self.roots:
            items.append({
                "name": r["name"],
                "path": r["path"],
                "is_dir": True,
                "kind": "volume",
                "role": r["role"],
                "source": r["source"],
                "file_count": counts.get(r["name"], 0),
            })
        return {"path": "", "items": items, "roots": [r["path"] for r in self.roots]}

    def children(
        self, path: str = "", limit: int = 300, offset: int = 0,
    ) -> dict[str, Any]:
        """列出目录的直接子目录与直接文件（模仿资源管理器进入一级目录）."""
        path = _norm(path)
        limit = max(1, min(1000, int(limit)))
        offset = max(0, int(offset))

        if not path:
            listing = self.roots_listing()
            listing["total"] = len(listing["items"])
            return listing

        if not _in_roots(path, self.roots):
            return {"path": path, "items": [], "total": 0, "denied": True,
                    "roots": [r["path"] for r in self.roots]}

        base = path.rstrip("/") + "/"
        # DB 中的路径用采集时的原生分隔符（Linux='/'，Windows='\\'）；
        # 前端传来的是 POSIX 风格，LIKE 查询需换成原生分隔符才能命中。
        native_sep = os.sep
        native_base = base.replace("/", native_sep)
        conn = self.db.connect()
        rows = conn.execute(
            """SELECT path, filename, ext, size, mtime, ftype, domain, temp, is_duplicate
               FROM files WHERE status='active' AND path LIKE ?""",
            (native_base + "%",),
        ).fetchall()

        dirs: dict[str, dict[str, Any]] = {}
        files: list[dict[str, Any]] = []
        for r in rows:
            full = str(r["path"])
            rest = full[len(native_base):] if full.startswith(native_base) else _norm(full)[len(base):]
            if not rest or rest.startswith(native_sep):
                continue
            if native_sep in rest:
                child_name = rest.split(native_sep, 1)[0]
                child_path = base + child_name
                d = dirs.setdefault(child_path, {
                    "path": child_path, "name": child_name, "is_dir": True,
                    "kind": "dir", "file_count": 0, "size": 0,
                })
                d["file_count"] += 1
                d["size"] += int(r["size"] or 0)
            else:
                files.append({
                    "path": _norm(full), "name": r["filename"], "is_dir": False,
                    "kind": "file", "ext": r["ext"] or "", "size": int(r["size"] or 0),
                    "mtime": float(r["mtime"] or 0), "ftype": r["ftype"],
                    "domain": r["domain"], "temp": r["temp"],
                    "is_duplicate": bool(r["is_duplicate"]),
                })
        dir_items = sorted(dirs.values(), key=lambda d: d["name"])
        file_items = sorted(files, key=lambda f: f["name"])
        all_items = dir_items + file_items
        total = len(all_items)
        return {
            "path": path,
            "name": self.name_for_path(path),
            "parent": _parent(path),
            "items": all_items[offset:offset + limit],
            "total": total,
            "dir_count": len(dir_items),
            "file_count": len(file_items),
            "roots": [r["path"] for r in self.roots],
        }

    def ancestors(self, path: str) -> list[dict[str, str]]:
        """面包屑：从根到当前目录。"""
        path = _norm(path)
        if not path:
            return []
        parts = [p for p in path.split("/") if p]
        result: list[dict[str, str]] = []
        cur = ""
        for part in parts:
            cur = (cur + "/" + part) if cur else ("/" + part if path.startswith("/") else part)
            result.append({"name": part, "path": cur})
        return result
