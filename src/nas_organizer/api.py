"""FastAPI Web 控制台后端."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import Config
from .db import Database
from .planner import Planner

app = FastAPI(title="NAS Organizer", version="0.1.0")
_config: Config | None = None
_db: Database | None = None


def setup(config: Config, db: Database) -> FastAPI:
    global _config, _db
    _config = config
    _db = db
    db.init_schema()
    web_dir = Path(__file__).parent.parent.parent / "web"
    if web_dir.exists():
        app.mount("/static", StaticFiles(directory=str(web_dir)), name="static")
    return app


def _get_db() -> Database:
    if _db is None:
        raise HTTPException(500, "数据库未初始化")
    return _db


class PlanRequest(BaseModel):
    name: str | None = None
    domain: str | None = None
    ftype: str | None = None
    temp: str | None = None
    only_duplicates: bool = False
    limit: int = 0


@app.get("/")
def index():
    web_dir = Path(__file__).parent.parent.parent / "web"
    index_file = web_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file))
    return {"message": "NAS Organizer API", "docs": "/docs"}


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
        (*params, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/duplicates")
def duplicates(limit: int = 50):
    conn = _get_db().connect()
    rows = conn.execute(
        """SELECT full_hash, COUNT(*) n, SUM(size) wasted
           FROM files WHERE full_hash IS NOT NULL AND status='active'
           GROUP BY full_hash HAVING n > 1 ORDER BY wasted DESC LIMIT ?""",
        (limit,),
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


@app.post("/api/plans")
def create_plan(req: PlanRequest):
    db = _get_db()
    planner = Planner(_config or Config.load(), db)
    plan = planner.build_plan(
        name=req.name, domain=req.domain, ftype=req.ftype,
        temp=req.temp, only_duplicates=req.only_duplicates, limit=req.limit,
    )
    return {"id": plan.id, "name": plan.name, "item_count": plan.item_count}


@app.get("/api/plans")
def list_plans():
    conn = _get_db().connect()
    rows = conn.execute("SELECT * FROM plans ORDER BY id DESC").fetchall()
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
def execute_plan(plan_id: int, limit: int = 0):
    from .executor import Executor
    executor = Executor(_config or Config.load(), _get_db())
    return executor.execute_plan(plan_id, limit)


@app.post("/api/rollback")
def rollback(limit: int = 0):
    from .executor import Executor
    executor = Executor(_config or Config.load(), _get_db())
    return executor.rollback_last(limit)


@app.post("/api/scan")
def scan():
    from .scanner import Scanner
    scanner = Scanner(_config or Config.load(), _get_db())
    return scanner.scan_all()


@app.post("/api/classify")
def classify():
    from .classifier import Classifier
    classifier = Classifier(_config or Config.load(), _get_db())
    return classifier.classify_all()


@app.post("/api/dedupe")
def dedupe():
    from .dedupe import Deduper
    deduper = Deduper(_config or Config.load(), _get_db())
    return deduper.find_duplicates()
