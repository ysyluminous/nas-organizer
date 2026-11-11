"""轻量定时调度器：纯标准库 cron（5 段）解析，零新依赖。

安全设计：
  - 默认全关：没有 schedule.yaml / enabled=false 时不启动任何后台线程。
  - 命令白名单：只接受 DRY_RUN_COMMANDS，绝不自动执行写操作。
  - 每次触发写入 op_logs（action=scheduled:<command>），可审计、可追溯。
  - 单实例内存锁：同一任务未结束不重复触发。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import Config
from .db import Database

# 调度器允许触发的命令——全部是 dry-run 或非破坏性操作
DRY_RUN_COMMANDS = {"scan", "classify", "dedupe", "backup", "verify"}
FORBIDDEN_COMMANDS = {"execute", "rollback", "plan_execute", "delete", "purge"}


def parse_cron(expr: str) -> dict[str, set[int]]:
    """解析 5 段 cron 为 {minute,hour,day,month,weekday} → 允许值集合。

    支持：* 、*/n 、a-b 、a,b,c 。不支持 L/W/# 等扩展语法（明确报错）。
    """
    fields = ("minute", "hour", "day", "month", "weekday")
    limits = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    parts = str(expr).strip().split()
    if len(parts) != 5:
        raise ValueError(f"cron 需要 5 段，实际 {len(parts)} 段: {expr!r}")
    result: dict[str, set[int]] = {}
    for name, part, (lo, hi) in zip(fields, parts, limits):
        if any(ch in part for ch in "LW#?"):
            raise ValueError(f"不支持的 cron 扩展语法: {part!r}")
        values: set[int] = set()
        for token in part.split(","):
            if token == "*":
                values.update(range(lo, hi + 1))
            elif token.startswith("*/"):
                step = int(token[2:])
                if step < 1:
                    raise ValueError(f"步长必须 ≥1: {token!r}")
                values.update(range(lo, hi + 1, step))
            elif "-" in token:
                a, b = token.split("-", 1)
                values.update(range(int(a), int(b) + 1))
            else:
                values.add(int(token))
        out = {v for v in values if lo <= v <= hi}
        if name == "weekday":
            out = {0 if v == 7 else v for v in out}
        if not out:
            raise ValueError(f"cron 字段 {name} 无有效值: {part!r}")
        result[name] = out
    return result


def cron_matches(expr: str, when: datetime) -> bool:
    """判断给定时间是否命中 cron（按分钟粒度）。"""
    rules = parse_cron(expr)
    weekday = (when.weekday() + 1) % 7  # 周一=1..周日=0，与 cron 0=周日一致
    return (
        when.minute in rules["minute"]
        and when.hour in rules["hour"]
        and when.day in rules["day"]
        and when.month in rules["month"]
        and weekday in rules["weekday"]
    )


class Scheduler:
    """周期性触发 dry-run 任务；默认不启动任何任务。"""

    def __init__(self, config: Config, db: Database, runner: Callable[[str], Any] | None = None):
        self.config = config
        self.db = db
        self.runner = runner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self.jobs = self._load_jobs()

    def _load_jobs(self) -> list[dict[str, Any]]:
        if not self.config.get("schedule.enabled", False):
            return []
        path = self.config.get("schedule.config", "config/schedule.yaml")
        p = Path(str(path))
        if not p.exists():
            return []
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            print(f"[WARN] 加载调度配置失败 {p}: {exc}")
            return []
        jobs: list[dict[str, Any]] = []
        for job in data.get("jobs", []):
            if not job.get("enabled", False):
                continue
            command = str(job.get("command", "")).strip()
            if command in FORBIDDEN_COMMANDS:
                print(f"[WARN] 调度器拒绝危险命令: {command}")
                continue
            if command not in DRY_RUN_COMMANDS:
                print(f"[WARN] 调度器忽略未知命令: {command}")
                continue
            try:
                parse_cron(str(job.get("cron", "")))
            except ValueError as exc:
                print(f"[WARN] 任务 {job.get('name')} cron 无效，跳过: {exc}")
                continue
            jobs.append({"name": str(job.get("name", command)), "command": command,
                         "cron": str(job["cron"])})
        return jobs

    def _default_runner(self, command: str) -> Any:
        """未注入 runner 时，直接用子进程调用 CLI，隔离崩溃。"""
        import subprocess
        import sys
        return subprocess.run(
            [sys.executable, "-m", "nas_organizer.cli", command],
            capture_output=True, text=True, timeout=3600,
        )

    def _run_job(self, job: dict[str, Any]) -> None:
        name = job["name"]
        with self._lock:
            if name in self._running:
                return
            self._running.add(name)
        started = time.time()
        status, detail = "ok", ""
        try:
            runner = self.runner or self._default_runner
            runner(job["command"])
        except Exception as exc:
            status, detail = "failed", str(exc)[:500]
            print(f"[ERROR] 定时任务 {name} 失败: {exc}")
        finally:
            with self._lock:
                self._running.discard(name)
            try:
                conn = self.db.connect()
                conn.execute(
                    """INSERT INTO scheduler_logs
                       (job_name, command, status, detail, started_at, duration_sec)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (name, job["command"], status, detail, started, time.time() - started),
                )
                conn.commit()
            except Exception as exc:
                print(f"[WARN] 定时任务审计日志写入失败: {exc}")
            print(f"[scheduler] {name} {status} 耗时 {time.time() - started:.1f}s")

    def _loop(self) -> None:
        last_minute = ""
        while not self._stop.is_set():
            now = datetime.now()
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            if minute_key != last_minute:
                last_minute = minute_key
                for job in self.jobs:
                    try:
                        if cron_matches(job["cron"], now):
                            threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
                    except Exception as exc:
                        print(f"[WARN] 任务 {job['name']} 匹配失败: {exc}")
            self._stop.wait(1.0)

    def start(self) -> bool:
        """启动后台调度线程；无任务时返回 False。"""
        if not self.jobs:
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="nas-scheduler")
        self._thread.start()
        print(f"[scheduler] 已启动，启用 {len(self.jobs)} 个定时任务")
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def describe(self) -> list[dict[str, str]]:
        return [{"name": j["name"], "command": j["command"], "cron": j["cron"]}
                for j in self.jobs]
