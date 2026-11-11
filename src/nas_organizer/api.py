"""FastAPI Web 控制台后端（带认证保护）。

安全约定：
- 除 /api/auth/login、/api/auth/status 外，所有 /api/* 接口必须携带 Bearer Token
- 认证使用 Authorization 请求头而非 Cookie，天然免疫 CSRF
- 危险操作（execute / rollback）即使拿到 Token 也需二次确认参数 confirm=true
- 响应统一附加安全头，禁止被 iframe 嵌套与内容嗅探
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import AuthError, AuthManager
from .config import Config
from .db import Database
from .planner import Planner

# 生产环境默认关闭交互式文档，避免暴露 API 面；开发时设 NAS_ORGANIZER_DOCS=1 开启
_DOCS = os.getenv("NAS_ORGANIZER_DOCS", "").strip().lower() in ("1", "true", "yes")
_DOCS_KW = (
    {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
    if _DOCS else
    {"docs_url": None, "redoc_url": None, "openapi_url": None}
)

app = FastAPI(title="NAS Organizer", version="0.2.0", **_DOCS_KW)
_config: Config | None = None
_db: Database | None = None
_auth: AuthManager | None = None

PUBLIC_PATHS = {"/api/auth/login", "/api/auth/status"}


def setup(config: Config, db: Database) -> FastAPI:
    global _config, _db, _auth
    _config = config
    _db = db
    db.init_schema()

    data_dir = config.resolve(config.get("database.path", "data/nas_organizer.db")).parent
    _auth = AuthManager(data_dir, config.get("auth", {}) or {})

    web_dir = Path(__file__).parent.parent.parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")
    return app


def _extract_token(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # 兼容部分客户端自定义头
    return request.headers.get("X-Auth-Token")


def _client_ip(request: Request) -> str:
    # 只信任直连地址；反向代理场景请在受控网络中部署，勿公网暴露
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    path = request.url.path
    headers = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }

    if path.startswith("/api/") and path not in PUBLIC_PATHS:
        token = _extract_token(request)
        username = _auth.verify_token(token) if _auth else None
        if not username:
            return JSONResponse(
                {"detail": "未授权，请先登录"},
                status_code=401,
                headers={**headers, "WWW-Authenticate": "Bearer"},
            )
        request.state.username = username

    try:
        response = await call_next(request)
    except AuthError as exc:
        extra = {"Retry-After": str(exc.retry_after)} if exc.retry_after else {}
        response = JSONResponse({"detail": exc.message}, status_code=exc.status, headers={**headers, **extra})
    for key, value in headers.items():
        response.headers[key] = value
    return response


def _get_db() -> Database:
    if _db is None:
        raise HTTPException(500, "数据库未初始化")
    return _db


def _get_auth() -> AuthManager:
    if _auth is None:
        raise HTTPException(500, "认证模块未初始化")
    return _auth


# ---------- 认证接口 ----------

class LoginRequest(BaseModel):
    password: str


class PasswordRequest(BaseModel):
    old_password: str
    new_password: str


class ConfirmRequest(BaseModel):
    confirm: bool = False
    limit: int = 0


@app.get("/api/auth/status")
def auth_status():
    """前端据此判断是否需要显示登录页；不泄露任何凭据信息。"""
    return {"auth_required": True, "password_set": _get_auth().is_password_set() if _auth else False}


@app.post("/api/auth/login")
def login(req: LoginRequest, request: Request):
    token = _get_auth().verify_login(req.password, _client_ip(request))
    return {"token": token, "expires_in": _get_auth().token_ttl, "token_type": "Bearer"}


@app.get("/api/auth/me")
def me(request: Request):
    return {"username": getattr(request.state, "username", "admin"), "expires_in": _get_auth().token_ttl}


@app.post("/api/auth/logout")
def logout(request: Request):
    _get_auth().revoke_token(_extract_token(request))
    return {"ok": True}


@app.post("/api/auth/password")
def change_password(req: PasswordRequest, request: Request):
    new_token = _get_auth().change_password(req.old_password, req.new_password, _client_ip(request))
    return {"token": new_token, "expires_in": _get_auth().token_ttl, "message": "密码已修改，请使用新 Token 重新登录"}


# ---------- 业务接口 ----------

@app.get("/")
def index():
    web_dir = Path(__file__).parent.parent.parent / "web"
    index_file = web_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "NAS Organizer API", "docs": "设置 NAS_ORGANIZER_DOCS=1 可启用"}


@app.get("/api/stats")
def stats():
    db = _get_db()
    conn = db.connect()
    by_volume = conn.execute(
        "SELECT volume, COUNT(*) n, SUM(size) bytes FROM files WHERE status='active' GROUP BY volume"
    ).fetchall()
    by_domain = conn.execute(
        "SELECT domain, COUNT(*) n FROM files WHERE status='active' GROUP BY domain"
    ).fetchall()
    by_ftype = conn.execute(
        "SELECT ftype, COUNT(*) n FROM files WHERE status='active' GROUP BY ftype"
    ).fetchall()
    by_temp = conn.execute(
        "SELECT temp, COUNT(*) n FROM files WHERE status='active' GROUP BY temp"
    ).fetchall()
    dup = conn.execute(
        "SELECT COUNT(*) n, COALESCE(SUM(size),0) bytes FROM files WHERE is_duplicate=1"
    ).fetchone()
    return {
        "total": db.count_files(),
        "by_volume": [dict(r) for r in by_volume],
        "by_domain": [dict(r) for r in by_domain],
        "by_ftype": [dict(r) for r in by_ftype],
        "by_temp": [dict(r) for r in by_temp],
        "duplicates": {"count": dup["n"], "bytes": dup["bytes"]},
    }


@app.get("/api/files")
def files(domain: str | None = None, ftype: str | None = None, temp: str | None = None,
          limit: int = 50, offset: int = 0):
    conn = _get_db().connect()
    clauses = ["status='active'"]
    params: list = []
    for col, val in (("domain", domain), ("ftype", ftype), ("temp", temp)):
        if val:
            clauses.append(f"{col}=?")
            params.append(val)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT id, path, size, mtime, domain, ftype, temp, confidence, "
        f"classified_by, is_duplicate FROM files WHERE {where} "
        f"ORDER BY size DESC LIMIT ? OFFSET ?",
        (*params, min(limit, 1000), max(0, offset)),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/duplicates")
def duplicates(limit: int = 50):
    conn = _get_db().connect()
    rows = conn.execute(
        """SELECT full_hash, COUNT(*) n, SUM(size) wasted
           FROM files WHERE full_hash IS NOT NULL AND status='active'
           GROUP BY full_hash HAVING n > 1 ORDER BY wasted DESC LIMIT ?""",
        (min(limit, 1000),),
    ).fetchall()
    result = []
    for row in rows:
        members = conn.execute(
            "SELECT id, path, size, mtime FROM files WHERE full_hash=?", (row["full_hash"],)
        ).fetchall()
        result.append({
            "hash": row["full_hash"],
            "count": row["n"],
            "wasted_bytes": row["wasted"],
            "files": [dict(m) for m in members],
        })
    return result


class PlanRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
    ftype: str | None = None
    temp: str | None = None
    only_duplicates: bool = False
    limit: int = 0


@app.post("/api/plans")
def create_plan(req: PlanRequest):
    planner = Planner(_config or Config.load(), _get_db())
    plan = planner.build_plan(
        name=req.name, domain=req.domain, ftype=req.ftype,
        temp=req.temp, only_duplicates=req.only_duplicates, limit=req.limit,
    )
    return {"id": plan.id, "name": plan.name, "item_count": plan.item_count}


@app.get("/api/plans")
def list_plans():
    conn = _get_db().connect()
    rows = conn.execute("SELECT * FROM plans ORDER BY id DESC LIMIT 500").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/plans/{plan_id}")
def get_plan(plan_id: int):
    plan = _get_db().get_plan(plan_id)
    if not plan:
        raise HTTPException(404, "计划不存在")
    return {
        "id": plan.id, "name": plan.name, "status": plan.status,
        "item_count": plan.item_count,
        "items": [
            {
                "id": i.id, "action": i.action, "src": i.src_path,
                "dst": i.dst_path, "reason": i.reason, "status": i.status, "error": i.error,
            }
            for i in plan.items
        ],
    }


@app.post("/api/plans/{plan_id}/execute")
def execute_plan(plan_id: int, req: ConfirmRequest):
    """危险操作：必须显式 confirm=true，防止误触或脚本误调用。"""
    if not req.confirm:
        raise HTTPException(400, "危险操作需显式确认：confirm=true")
    from .executor import Executor
    executor = Executor(_config or Config.load(), _get_db())
    return executor.execute_plan(plan_id, min(req.limit, 10_000))


@app.post("/api/rollback")
def rollback(req: ConfirmRequest):
    """危险操作：必须显式 confirm=true。"""
    if not req.confirm:
        raise HTTPException(400, "危险操作需显式确认：confirm=true")
    from .executor import Executor
    executor = Executor(_config or Config.load(), _get_db())
    return executor.rollback_last(min(req.limit or 10, 10_000))


@app.post("/api/scan")
def scan():
    from .scanner import Scanner
    return Scanner(_config or Config.load(), _get_db()).scan_all()


@app.post("/api/classify")
def classify():
    from .classifier import Classifier
    return Classifier(_config or Config.load(), _get_db()).classify_all()


@app.get("/api/tasks")
def tasks(limit: int = 50):
    """任务监控：运行中进度 + 调度/操作历史（只读，不触发任务）。"""
    from .tasks import task_snapshot
    return task_snapshot(_get_db(), limit)


@app.get("/api/views")
def list_views():
    """虚拟目录视图清单（按标签分组，不移动文件）。"""
    from .virtual import VirtualDirectory
    return VirtualDirectory(_config, _get_db()).list_views()


@app.get("/api/views/{tag}")
def get_view(tag: str, limit: int = 100, offset: int = 0):
    from .virtual import VirtualDirectory
    return VirtualDirectory(_config, _get_db()).view(tag, limit, offset)


@app.post("/api/dedupe")
def dedupe():
    from .dedupe import Deduper
    return Deduper(_config or Config.load(), _get_db()).find_duplicates()
