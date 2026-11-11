"""D5 Agent 任务监控：暴露长任务进度、耗时与历史。

设计约束（对应进度计划 #26）：
- 只读：GET /api/tasks 与 MCP task_list 都是只读，不触发任何写操作。
- 内存注册表记录运行中的任务进度；DB 的 scheduler_logs / op_logs 提供持久历史。
- 无任务时返回空列表，不报错；进程重启后运行态清空、历史仍可查。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .db import Database


class TaskRegistry:
    """进程内任务进度注册表（线程安全）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}

    def start(self, task_id: str, name: str, total: int = 0) -> None:
        with self._lock:
            self._tasks[task_id] = {
                "id": task_id, "name": name, "status": "running",
                "done": 0, "total": total,
                "progress": 0.0, "started_at": time.time(),
                "updated_at": time.time(), "finished_at": None, "error": None,
            }

    def update(self, task_id: str, done: int, total: int | None = None) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            t["done"] = done
            if total is not None:
                t["total"] = total
            t["progress"] = round(done / t["total"], 4) if t["total"] else 0.0
            t["updated_at"] = time.time()

    def finish(self, task_id: str, error: str | None = None) -> None:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t:
                return
            t["status"] = "failed" if error else "done"
            t["error"] = error
            t["finished_at"] = time.time()
            t["duration_sec"] = round(t["finished_at"] - t["started_at"], 3)

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            t = self._tasks.get(task_id)
            return dict(t) if t else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            tasks = [dict(t) for t in self._tasks.values()]
        tasks.sort(key=lambda t: t["started_at"], reverse=True)
        return tasks[:limit]

    def clear_finished(self) -> int:
        with self._lock:
            done = [k for k, t in self._tasks.items() if t["status"] in ("done", "failed")]
            for k in done:
                del self._tasks[k]
            return len(done)


# 全局单例：Web 与 MCP 共享同一份运行态
_registry = TaskRegistry()


def get_registry() -> TaskRegistry:
    return _registry


def task_snapshot(db: Database, limit: int = 50) -> dict[str, Any]:
    """运行中任务 + 持久历史（调度/最近操作）合并快照。"""
    running = _registry.list(limit)
    try:
        sched = db.connect().execute(
            "SELECT job_name AS name, status, started_at, duration_sec FROM scheduler_logs "
            "ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        history = [
            {"name": r["name"], "status": r["status"], "started_at": float(r["started_at"]),
             "duration_sec": float(r["duration_sec"]), "source": "scheduler"}
            for r in sched
        ]
    except Exception:
        history = []
    return {"running": [t for t in running if t["status"] == "running"],
            "recent": running[:limit], "history": history,
            "running_count": sum(1 for t in running if t["status"] == "running")}
