"""Web 控制台扩展 API（Round A）。

设计原则：
- 把 D1-D7 已有后端能力（搜索/清理/回收站/审计/媒体/感知/插件/调度/反馈）
  以只读优先的方式暴露给 Web；不改动 api.py 里的存量端点。
- 挂在 /api 前缀下，因此自动继承 api.py 的 Bearer 认证中间件与安全响应头。
- 任何会写索引的操作（reindex / auto-tag）一律要求 confirm=true；
  任何会动用户文件的操作（回收站提升/还原/清除）不在 Web 暴露，仍走 CLI/MCP 的 confirm 链路。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .config import Config
from .db import Database

router = APIRouter(prefix="/api")
_cfg: Config | None = None
_db: Database | None = None


def init(config: Config, db: Database) -> None:
    """由 api.setup() 调用，注入配置与数据库（避免循环导入）。"""
    global _cfg, _db
    _cfg, _db = config, db


def _config() -> Config:
    return _cfg or Config.load()


def _dbx() -> Database:
    if _db is None:
        raise HTTPException(500, "数据库未初始化")
    return _db


class ConfirmBody(BaseModel):
    confirm: bool = False


# ---------- 搜索（FTS5） ----------

@router.get("/search")
def search(q: str = "", limit: int = 50, offset: int = 0):
    from .search import SearchIndex
    if not q.strip():
        return {"count": 0, "results": [], "engine": "idle", "query": q}
    return SearchIndex(_config(), _dbx()).search(q.strip(), min(limit, 200), max(offset, 0))


@router.get("/search/stats")
def search_stats():
    from .search import SearchIndex
    return SearchIndex(_config(), _dbx()).stats()


@router.post("/search/reindex")
def search_reindex(body: ConfirmBody):
    if not body.confirm:
        raise HTTPException(400, "重建索引需显式确认：confirm=true")
    from .search import SearchIndex
    return SearchIndex(_config(), _dbx()).build_index()


# ---------- 清理发现（只读） ----------

@router.get("/cleanup")
def cleanup(kinds: str = "", limit: int = 200):
    from .cleanup import CLEANUP_KINDS, CleanupFinder
    finder = CleanupFinder(_config(), _dbx())
    roots = _config().get("cleanup.roots", []) or []
    if not roots:
        for v in (_config().get("volumes", []) or []):
            if isinstance(v, dict) and v.get("path"):
                roots.append(v["path"])
    active = [k.strip() for k in kinds.split(",") if k.strip()] or None
    if active:
        unknown = set(active) - set(CLEANUP_KINDS)
        if unknown:
            raise HTTPException(400, f"未知清理类型: {sorted(unknown)}")
    report = finder.discover([str(r) for r in roots], active)
    candidates = [
        {"kind": c.kind, "path": c.path, "size": c.size, "mtime": c.mtime, "reason": c.reason}
        for c in report.candidates[:min(limit, 1000)]
    ]
    return {
        "scanned_dirs": report.scanned_dirs,
        "by_kind": report.by_kind(),
        "total_bytes": report.total_bytes(),
        "total_count": len(report.candidates),
        "candidates": candidates,
    }


@router.get("/cleanup/kinds")
def cleanup_kinds():
    from .cleanup import CLEANUP_KINDS
    return {"kinds": list(CLEANUP_KINDS)}


# ---------- 回收站（只读） ----------

@router.get("/recycle")
def recycle_list(stage: int | None = None, limit: int = 100):
    from .recycle import RecycleBin
    return RecycleBin(_config(), _dbx()).list_items(stage, min(limit, 1000))


@router.get("/recycle/stats")
def recycle_stats():
    from .recycle import RecycleBin
    return RecycleBin(_config(), _dbx()).stats()


# ---------- 备份审计（只读） ----------

@router.get("/audit/backup")
def backup_audit():
    from .audit import BackupAuditor
    report = BackupAuditor(_config(), _dbx()).audit()
    return {**report.summary(), "findings": [
        {"check": f.check, "path": f.path, "detail": f.detail, "severity": f.severity}
        for f in report.findings[:200]
    ]}


# ---------- 媒体 / 感知 / 插件能力 ----------

@router.get("/media/capability")
def media_capability():
    from .media import MediaOrganizer
    return MediaOrganizer.capability()


@router.get("/plugins/capability")
def plugins_capability():
    """一处汇总所有可选外部依赖的可用性，便于前端能力探测与降级提示。"""
    from . import extractors, perceptual, remote
    from .ai import build_provider
    from .media import MediaOrganizer
    result = {
        "media": MediaOrganizer.capability(),
        "phash": perceptual.PerceptualFinder.capability(),
        "plugins": extractors.capability(),
        "external": remote.capability(),
    }
    try:
        result["ai"] = {"enabled": build_provider(_config()) is not None}
    except Exception:
        result["ai"] = {"enabled": False}
    return result


@router.get("/phash/similar")
def phash_similar(limit: int = 50):
    from .perceptual import PerceptualFinder
    result = PerceptualFinder(_config(), _dbx()).find_similar()
    if "groups" in result:
        result["groups"] = result["groups"][:min(limit, 200)]
    return result


@router.get("/video/similar")
def video_similar(limit: int = 50):
    from .remote import VideoKeyframeFinder
    result = VideoKeyframeFinder(_config(), _dbx()).find_similar()
    if "groups" in result:
        result["groups"] = result["groups"][:min(limit, 200)]
    return result


# ---------- 虚拟目录 / 反馈 / 调度 ----------

@router.get("/views/stats")
def views_stats():
    from .virtual import VirtualDirectory
    return VirtualDirectory(_config(), _dbx()).stats()


@router.post("/views/auto-tag")
def views_auto_tag(body: ConfirmBody):
    if not body.confirm:
        raise HTTPException(400, "自动打标需显式确认：confirm=true")
    from .virtual import VirtualDirectory
    return VirtualDirectory(_config(), _dbx()).auto_tag()


@router.get("/feedback")
def feedback_list(field: str | None = None, limit: int = 100):
    return _dbx().get_feedback(field, min(limit, 500))


@router.get("/schedule")
def schedule_list():
    from .scheduler import Scheduler
    return {"jobs": Scheduler(_config(), _dbx()).describe()}


@router.get("/audit/operations")
def operations_timeline(limit: int = 100, offset: int = 0):
    """操作审计时间线（只读）：把安全执行闭环（执行→校验→回滚）变成可查看的证据链。"""
    conn = _dbx().connect()
    rows = conn.execute(
        """SELECT o.id, o.plan_item_id, o.action, o.src_path, o.dst_path,
                  o.sha256, o.size, o.executed_at, o.rolled_back,
                  p.name AS plan_name
           FROM op_logs o LEFT JOIN plan_items pi ON pi.id=o.plan_item_id
           LEFT JOIN plans p ON p.id=pi.plan_id
           ORDER BY o.executed_at DESC LIMIT ? OFFSET ?""",
        (min(limit, 500), max(offset, 0)),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) n FROM op_logs").fetchone()["n"]
    return {
        "total": total,
        "operations": [
            {
                "id": r["id"], "plan": r["plan_name"], "action": r["action"],
                "src": r["src_path"], "dst": r["dst_path"],
                "size": r["size"], "sha256": r["sha256"],
                "executed_at": r["executed_at"], "rolled_back": bool(r["rolled_back"]),
            }
            for r in rows
        ],
    }
