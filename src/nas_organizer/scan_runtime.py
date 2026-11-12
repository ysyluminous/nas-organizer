"""扫描运行时：后台异步扫描 + 实时进度 + 定期增量调度.

设计要点：
- 进程内单例 Runtime，Web 与调度器共享同一份运行态（线程安全）。
- start() 立即返回，真实扫描在 daemon 线程执行；progress() 可被前端高频轮询。
- 增量模式复用 Scanner 的 scan_state.last_scan_at：只 upsert 新增/变更文件，
  新增文件自动入库（对应“新增文件自动添加”）。
- 自动增量调度持久化到 data/scan_settings.json（不碰只读 config.yaml），
  默认关闭；开启后按 interval_minutes 周期触发增量扫描，且不会与手动扫描并发。
- 所有写操作只更新索引数据库，绝不改动用户文件。
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from .config import Config
from .db import Database

SETTINGS_REL = "data/scan_settings.json"
DEFAULT_SETTINGS: dict[str, Any] = {
    "auto_incremental": False,
    "interval_minutes": 30,
}


def _settings_path(config: Config) -> Path:
    return config.resolve(SETTINGS_REL)


def load_settings(config: Config) -> dict[str, Any]:
    data = dict(DEFAULT_SETTINGS)
    p = _settings_path(config)
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in DEFAULT_SETTINGS:
                    if key in loaded:
                        data[key] = loaded[key]
        except (OSError, ValueError):
            pass
    try:
        data["interval_minutes"] = max(1, min(1440, int(data["interval_minutes"])))
    except (TypeError, ValueError):
        data["interval_minutes"] = 30
    data["auto_incremental"] = bool(data["auto_incremental"])
    return data


def save_settings(config: Config, settings: dict[str, Any]) -> dict[str, Any]:
    clean = load_settings(config)
    if "auto_incremental" in settings:
        clean["auto_incremental"] = bool(settings["auto_incremental"])
    if "interval_minutes" in settings:
        try:
            clean["interval_minutes"] = max(1, min(1440, int(settings["interval_minutes"])))
        except (TypeError, ValueError):
            raise ValueError("interval_minutes 必须是 1-1440 的整数")
    p = _settings_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".scan-settings-", suffix=".tmp")
    try:
        with __import__("os").fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        __import__("os").replace(tmp, str(p))
    except Exception:
        try:
            __import__("os").unlink(tmp)
        except OSError:
            pass
        raise
    return clean


class ScanRuntime:
    """线程安全的扫描运行时单例。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._state: dict[str, Any] = self._empty_state()
        self._thread: threading.Thread | None = None
        self._config: Config | None = None
        self._db: Database | None = None
        self._sched_stop = threading.Event()
        self._sched_thread: threading.Thread | None = None

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "status": "idle",          # idle | running | done | error
            "incremental": False,
            "volume": "",
            "done": 0,
            "total": 0,
            "found": 0,                # 本次新增/变更入库数
            "started_at": None,
            "finished_at": None,
            "error": None,
            "phase": "scanning",   # discovering(遍历目录) | scanning(逐文件处理)
            "volumes": {},
        }

    # ---------- 扫描控制 ----------

    def start(self, config: Config, db: Database, incremental: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._state["status"] == "running":
                return {"started": False, "reason": "已有扫描正在运行"}
            self._state = self._empty_state()
            self._state.update({
                "status": "running",
                "incremental": bool(incremental),
                "started_at": time.time(),
            })
            self._config, self._db = config, db
            self._thread = threading.Thread(
                target=self._run, args=(config, db, bool(incremental)),
                daemon=True, name="nas-scan",
            )
            self._thread.start()
            return {"started": True, "incremental": bool(incremental)}

    def _run(self, config: Config, db: Database, incremental: bool) -> None:
        try:
            from .scanner import Scanner

            def volume_cb(name: str, phase: str) -> None:
                with self._lock:
                    self._state["volume"] = name if phase == "start" else ""
                    self._state["found"] = sum(
                        int(v) for v in self._state["volumes"].values()
                        if isinstance(v, (int, float))
                    )

            def progress_cb(done: int, total: int, volume: str, phase: str = "scanning") -> None:
                with self._lock:
                    self._state["done"] = int(done)
                    self._state["total"] = int(total)
                    self._state["volume"] = volume
                    self._state["phase"] = phase
                    if phase == "scanning":
                        self._state["found"] = (
                            sum(int(v) for v in self._state["volumes"].values()
                                if isinstance(v, (int, float))) + int(done)
                        )

            scanner = Scanner(config, db)
            results = scanner.scan_all(
                incremental=incremental,
                progress_cb=progress_cb,
                volume_cb=volume_cb,
            )
            with self._lock:
                self._state["status"] = "done"
                self._state["volumes"] = dict(results)
                self._state["done"] = sum(int(v) for v in results.values())
                self._state["total"] = self._state["done"]
                self._state["volume"] = ""
                self._state["finished_at"] = time.time()
        except Exception as exc:  # noqa: BLE001 - 后台线程必须捕获一切，避免静默死线程
            with self._lock:
                self._state["status"] = "error"
                self._state["error"] = str(exc)[:500]
                self._state["finished_at"] = time.time()

    def progress(self) -> dict[str, Any]:
        with self._lock:
            s = dict(self._state)
            volumes = dict(s.get("volumes") or {})
        if s["status"] == "running" and s["started_at"]:
            elapsed = max(0.001, time.time() - float(s["started_at"]))
            s["elapsed_sec"] = round(elapsed, 1)
            s["speed"] = round(s["done"] / elapsed, 1)
            if s["total"] > s["done"] and s["speed"] > 0:
                s["eta_sec"] = round((s["total"] - s["done"]) / s["speed"], 1)
            else:
                s["eta_sec"] = 0
        else:
            s["elapsed_sec"] = (
                round(float(s["finished_at"]) - float(s["started_at"]), 1)
                if s.get("finished_at") and s.get("started_at") else 0
            )
            s["speed"] = 0
            s["eta_sec"] = 0
        s["volumes"] = volumes
        return s

    # ---------- 自动增量调度 ----------

    def ensure_scheduler(self, config: Config, db: Database) -> None:
        """由 api.setup 调用；幂等。每 30 秒检查一次是否到点触发增量扫描。"""
        with self._lock:
            if self._sched_thread and self._sched_thread.is_alive():
                return
            self._config, self._db = config, db
            self._sched_stop.clear()
            self._sched_thread = threading.Thread(
                target=self._sched_loop, daemon=True, name="nas-auto-incremental",
            )
            self._sched_thread.start()

    def _sched_loop(self) -> None:
        last_run: dict[str, float] = {"ts": 0.0}
        while not self._sched_stop.wait(30.0):
            cfg, db = self._config, self._db
            if cfg is None or db is None:
                continue
            settings = load_settings(cfg)
            if not settings["auto_incremental"]:
                continue
            interval_sec = settings["interval_minutes"] * 60
            now = time.time()
            if last_run["ts"] and now - last_run["ts"] < interval_sec:
                continue
            with self._lock:
                if self._state["status"] == "running":
                    continue
                self.start(cfg, db, incremental=True)
            last_run["ts"] = now

    def stop_scheduler(self) -> None:
        self._sched_stop.set()


# 全局单例：Web 进程内所有路由共享同一运行态
runtime = ScanRuntime()
