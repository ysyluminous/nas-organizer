"""MCP（Model Context Protocol）服务：把 NAS Organizer 能力暴露为原子工具。

设计约束：
- 纯标准库实现 JSON-RPC 2.0 over stdio，不引入 MCP SDK，镜像体积不变
- 只读工具可匿名调用；受控写工具必须提供有效的 NAS_ORGANIZER_TOKEN
- 所有路径类工具调用 PathGuard；危险写工具强制 confirm=true
- 行为边界见仓库根目录 AGENTS.md

启动方式：
    export NAS_ORGANIZER_CONFIG=config/config.yaml
    export NAS_ORGANIZER_TOKEN=<从 Web 登录后获取的 Bearer Token>
    python -m nas_organizer.mcp_server
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .auth import AuthManager
from .config import Config
from .db import Database
from .planner import Planner
from .security import PathGuard, PathViolation

VERSION = "0.5.0"
PROTOCOL_VERSION = "2025-06-18"


class ToolError(Exception):
    """工具执行异常，返回给 MCP 客户端而不是崩溃服务。"""


def _text(payload: Any) -> dict:
    """MCP 工具结果封装。"""
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False, default=str)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _error(message: str) -> dict:
    return {"content": [{"type": "text", "text": message}], "isError": True}


class MCPServer:
    """轻量 MCP 服务，工具注册后通过 handle() 处理 JSON-RPC 请求。"""

    def __init__(self, config: Config, db: Database, token: str | None = None):
        self.config = config
        self.db = db
        self.db.init_schema()
        data_dir = config.resolve(
            str(config.get("database.path", "data/nas_organizer.db"))
        ).parent
        self.auth = AuthManager(data_dir, config.get("auth", {}) or {})
        self.guard = PathGuard(config)
        self.token = token if token is not None else os.getenv("NAS_ORGANIZER_TOKEN", "").strip()
        self.authorized = bool(self.token and self.auth.verify_token(self.token))
        if self.token and not self.authorized:
            raise ToolError("NAS_ORGANIZER_TOKEN 无效或已过期，请重新登录获取")
        self._tools: dict[str, dict] = {}
        self._register_tools()

    # ---------- 工具注册 ----------

    def _register(
        self,
        name: str,
        description: str,
        properties: dict | None = None,
        required: list[str] | None = None,
        handler: Callable[[dict], Any] | None = None,
        write: bool = False,
        confirm: bool = False,
    ) -> None:
        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": {
                "type": "object",
                "properties": properties or {},
                "required": required or [],
            },
            "_handler": handler,
            "_write": write,
            "_confirm": confirm,
        }

    def _register_tools(self) -> None:
        s = self
        # ---- 只读：概览与安全 ----
        s._register("health", "服务健康检查；返回版本、认证状态与工具数量",
                    handler=lambda a: s._health())
        s._register("get_agent_policy", "获取 Agent 行为约定（AGENTS.md 全文）",
                    handler=lambda a: s._agent_policy())
        s._register("get_config_summary", "获取脱敏配置摘要（卷数量、白名单数量等）",
                    handler=lambda a: s._config_summary())
        s._register("list_volumes", "列出配置的扫描卷及角色（hot/warm/cold）",
                    handler=lambda a: s.config.get("volumes", []))
        s._register("list_write_targets", "列出受控写入目标",
                    handler=lambda a: s.config.get("write_targets", []))
        s._register("security_status", "查看路径白名单根目录与审计状态（不含凭据）",
                    handler=lambda a: s._security_status())
        s._register("security_check_path", "检查一个路径是否在白名单内",
                    {"path": {"type": "string", "description": "待检查的绝对路径"}}, ["path"],
                    lambda a: s._security_check(a["path"]))

        # ---- 只读：统计 ----
        s._register("get_stats", "全量统计：文件总数、重复占用、各维度分布",
                    handler=lambda a: s._stats())
        s._register("get_stats_by_volume", "按卷统计文件数与占用字节",
                    handler=lambda a: s._group_stat("volume"))
        s._register("get_stats_by_domain", "按归属域（个人/工作/生活/资源）统计",
                    handler=lambda a: s._group_stat("domain"))
        s._register("get_stats_by_type", "按文件类型（音乐/视频/图书/文档等）统计",
                    handler=lambda a: s._group_stat("ftype"))
        s._register("get_stats_by_temperature", "按温度层（热/温/冷）统计",
                    handler=lambda a: s._group_stat("temp"))
        s._register("get_duplicate_summary", "重复文件汇总：组数、可释放空间",
                    handler=lambda a: s._duplicate_summary())
        s._register("backup_verify", "校验冷数据文件是否仍存在且大小未变（只读）",
                    handler=lambda a: s._backup_verify())

        # ---- 只读：文件检索 ----
        s._register("list_files", "按域/类型/温度分页查询文件索引",
                    {"domain": {"type": "string"}, "ftype": {"type": "string"},
                     "temp": {"type": "string"}, "limit": {"type": "integer", "default": 50},
                     "offset": {"type": "integer", "default": 0}},
                    handler=lambda a: s._list_files(a))
        s._register("get_file", "按 ID 获取单个文件记录",
                    {"file_id": {"type": "integer"}}, ["file_id"],
                    lambda a: s._row_dict("files", int(a["file_id"])))
        s._register("count_files", "按条件统计文件数量（不返回明细）",
                    {"domain": {"type": "string"}, "ftype": {"type": "string"},
                     "temp": {"type": "string"}},
                    handler=lambda a: s._count_files(a))
        s._register("search_files", "按路径/文件名关键词搜索（LIKE）",
                    {"keyword": {"type": "string"}, "limit": {"type": "integer", "default": 50}},
                    ["keyword"], lambda a: s._search_files(a))
        s._register("find_low_confidence", "查找低置信度分类结果，供人工复核",
                    {"threshold": {"type": "number", "default": 0.8},
                     "limit": {"type": "integer", "default": 50}},
                    handler=lambda a: s._low_confidence(a))
        s._register("find_large_files", "查找大文件（默认 > 2GB）",
                    {"min_bytes": {"type": "integer", "default": 2147483648},
                     "limit": {"type": "integer", "default": 50}},
                    handler=lambda a: s._large_files(a))
        s._register("find_old_files", "查找超过 N 天未修改的文件",
                    {"days": {"type": "integer", "default": 365},
                     "limit": {"type": "integer", "default": 50}},
                    handler=lambda a: s._old_files(a))

        # ---- 只读：重复与计划 ----
        s._register("list_duplicates", "列出重复文件组（按可释放空间降序）",
                    {"limit": {"type": "integer", "default": 50}},
                    handler=lambda a: s._duplicates(a))
        s._register("get_duplicate_group", "查看某一重复组的所有成员",
                    {"hash": {"type": "string"}}, ["hash"],
                    lambda a: s._duplicate_group(a["hash"]))
        s._register("list_plans", "列出迁移计划",
                    {"limit": {"type": "integer", "default": 50}},
                    handler=lambda a: s._list_plans(a))
        s._register("get_plan", "获取计划详情与明细",
                    {"plan_id": {"type": "integer"}}, ["plan_id"],
                    lambda a: s._get_plan(int(a["plan_id"])))
        s._register("preview_plan", "预览计划的前 N 条 old→new",
                    {"plan_id": {"type": "integer"}, "limit": {"type": "integer", "default": 30}},
                    ["plan_id"], lambda a: s._preview_plan(a))
        s._register("list_plan_items", "按状态筛选计划条目",
                    {"plan_id": {"type": "integer"}, "status": {"type": "string"},
                     "limit": {"type": "integer", "default": 50}}, ["plan_id"],
                    lambda a: s._plan_items(a))
        s._register("list_op_logs", "查看操作审计日志（用于回滚追溯）",
                    {"limit": {"type": "integer", "default": 50},
                     "include_rolled_back": {"type": "boolean", "default": False}},
                    handler=lambda a: s._op_logs(a))
        s._register("list_scan_states", "查看各卷最近扫描时间与状态",
                    handler=lambda a: s._scan_states())
        s._register("get_scan_state", "查看单个卷的扫描状态",
                    {"volume": {"type": "string"}}, ["volume"],
                    lambda a: s._scan_state(a["volume"]))
        s._register("list_rules", "查看分类规则条目数量（类型/域/温度）",
                    handler=lambda a: s._rules())
        # ---- 只读：D3 清理发现 / 备份审计 / 回收站 ----
        s._register("cleanup_discover", "只读扫描 12 类清理候选（空文件/临时/种子/陈旧压缩包等），不删除",
                    {"roots": {"type": "array", "items": {"type": "string"}},
                     "kinds": {"type": "array", "items": {"type": "string"}}}, ["roots"],
                    handler=lambda a: s._cleanup_discover(a))
        s._register("backup_audit", "备份审计四项检查（存在性/版本/陈旧/冲突），只读",
                    handler=lambda a: s._backup_audit())
        s._register("recycle_list", "列出两级回收站项目",
                    {"stage": {"type": "integer"}, "limit": {"type": "integer", "default": 100}},
                    handler=lambda a: s._recycle_list(a))
        s._register("recycle_stats", "回收站统计（各阶段数量与占用字节）",
                    handler=lambda a: s._recycle_stats())
        # ---- 只读：D4 反馈 / AI / 媒体 / 感知哈希 ----
        s._register("feedback_list", "查看人工反馈学习条目（关键词→域/类型权重）",
                    {"field": {"type": "string"}, "limit": {"type": "integer", "default": 100}},
                    handler=lambda a: s._feedback_list(a))
        s._register("ai_status", "查看可选 AI 兜底是否启用、provider 与依赖可用性",
                    handler=lambda a: s._ai_status())
        s._register("media_capability", "查看 exif/ID3/pHash 可选依赖是否可用",
                    handler=lambda a: s._media_capability())
        s._register("photo_plan_suggest", "按 exif 拍摄日期生成照片归档建议（只读，不移动）",
                    {"target_root": {"type": "string"}, "ftype": {"type": "string", "default": "图片"},
                     "limit": {"type": "integer", "default": 200}},
                    ["target_root"], handler=lambda a: s._photo_suggest(a))
        s._register("music_plan_suggest", "按 ID3 标签生成音乐归档建议（只读，不移动）",
                    {"target_root": {"type": "string"}, "limit": {"type": "integer", "default": 200}},
                    ["target_root"], handler=lambda a: s._music_suggest(a))
        s._register("phash_similar", "感知哈希查找相似图片（缩放/压缩同图），只读",
                    {"limit": {"type": "integer", "default": 50}},
                    handler=lambda a: s._phash_similar(a))
        # ---- 只读：D5 虚拟目录 / 进度 / undo 预览 / 任务监控 ----
        s._register("view_list", "列出所有虚拟目录视图（标签分组），不移动文件",
                    handler=lambda a: s._view_list())
        s._register("view_get", "查看一个虚拟视图下的文件（按标签检索）",
                    {"tag": {"type": "string"}, "limit": {"type": "integer", "default": 100},
                     "offset": {"type": "integer", "default": 0}}, ["tag"],
                    handler=lambda a: s._view_get(a))
        s._register("plan_progress", "查看计划执行进度（断点续传状态）",
                    {"plan_id": {"type": "integer"}}, ["plan_id"],
                    handler=lambda a: s._plan_progress(a))
        s._register("csv_undo_preview", "从 CSV 预览回滚条目数量（只读，不生成计划）",
                    {"path": {"type": "string"}}, ["path"],
                    handler=lambda a: s._csv_undo_preview(a))
        s._register("season_suggest", "剧集季数识别：生成 S01E02/第01集/EP02 归档建议（只读）",
                    {"target_root": {"type": "string"}, "limit": {"type": "integer", "default": 200}},
                    ["target_root"], handler=lambda a: s._season_suggest(a))
        s._register("task_list", "任务监控：运行中任务进度与调度/操作历史（只读）",
                    {"limit": {"type": "integer", "default": 50}},
                    handler=lambda a: s._task_list(a))

        # ---- 受控写：需要 Token ----
        s._register("scan_start", "扫描配置的卷并建立索引（只读，不改文件）",
                    handler=lambda a: s._scan(), write=True)
        s._register("classify_start", "重新执行规则分类（只读，不改文件）",
                    handler=lambda a: s._classify(), write=True)
        s._register("dedupe_start", "执行三级哈希查重（只读，不改文件）",
                    handler=lambda a: s._dedupe(), write=True)
        s._register("plan_create", "按筛选条件生成迁移计划（不执行）",
                    {"name": {"type": "string"}, "domain": {"type": "string"},
                     "ftype": {"type": "string"}, "temp": {"type": "string"},
                     "only_duplicates": {"type": "boolean", "default": False},
                     "limit": {"type": "integer", "default": 0}},
                    handler=lambda a: s._plan_create(a), write=True)
        s._register("plan_execute", "执行迁移计划；必须 confirm=true",
                    {"plan_id": {"type": "integer"}, "confirm": {"type": "boolean"},
                     "limit": {"type": "integer", "default": 0}}, ["plan_id", "confirm"],
                    lambda a: s._plan_execute(a), write=True, confirm=True)
        s._register("rollback_last", "回滚最近操作；必须 confirm=true",
                    {"confirm": {"type": "boolean"}, "limit": {"type": "integer", "default": 10}},
                    ["confirm"], lambda a: s._rollback(a), write=True, confirm=True)
        s._register("backup_hot", "热数据备份到目标目录；必须 confirm=true",
                    {"confirm": {"type": "boolean"}}, ["confirm"],
                    lambda a: s._backup(a), write=True, confirm=True)
        # D3 批量与回收站写操作
        s._register("plan_batch", "批量归类：按字段生成多目录计划（不执行移动）",
                    {"group_by": {"type": "string", "default": "domain"},
                     "name_prefix": {"type": "string"},
                     "limit": {"type": "integer", "default": 0}},
                    handler=lambda a: s._plan_batch(a), write=True)
        s._register("recycle_promote", "stage1 超时项提升到 stage2（不删除）",
                    {"days": {"type": "integer"}},
                    handler=lambda a: s._recycle_promote(a), write=True)
        s._register("recycle_restore", "还原回收站项目；必须 confirm=true",
                    {"item_id": {"type": "integer"}, "confirm": {"type": "boolean"}},
                    ["item_id", "confirm"],
                    lambda a: s._recycle_restore(a), write=True, confirm=True)
        s._register("recycle_purge", "物理清除 stage2；必须 confirm=true（不可逆）",
                    {"confirm": {"type": "boolean"}}, ["confirm"],
                    lambda a: s._recycle_purge(a), write=True, confirm=True)
        # D4 反馈学习写操作
        s._register("feedback_add", "记录一次人工纠正（关键词→域/类型），影响后续低置信度分类",
                    {"path": {"type": "string"}, "field": {"type": "string", "enum": ["domain", "ftype"]},
                     "value": {"type": "string"}}, ["path", "field", "value"],
                    lambda a: s._feedback_add(a), write=True)
        s._register("feedback_reset", "清空全部人工反馈；必须 confirm=true",
                    {"confirm": {"type": "boolean"}}, ["confirm"],
                    lambda a: s._feedback_reset(a), write=True, confirm=True)
        # D5 虚拟目录与可靠性写操作
        s._register("view_tag", "给文件打标签（虚拟归类，不移动文件）",
                    {"file_id": {"type": "integer"}, "tag": {"type": "string"}},
                    ["file_id", "tag"], lambda a: s._view_tag(a), write=True)
        s._register("view_untag", "移除文件标签（不移动文件）",
                    {"file_id": {"type": "integer"}, "tag": {"type": "string"}},
                    ["file_id", "tag"], lambda a: s._view_untag(a), write=True)
        s._register("view_auto_tag", "按域/类型/温度/年份自动打标（幂等，不移动文件）",
                    handler=lambda a: s._view_auto_tag(), write=True)
        s._register("csv_undo_import", "从 CSV 生成回滚计划（只建计划，不执行文件操作）",
                    {"path": {"type": "string"}, "name": {"type": "string"}}, ["path"],
                    lambda a: s._csv_undo_import(a), write=True)

    # ---------- 工具实现：基础 ----------

    def _health(self) -> dict:
        return {
            "status": "ok", "version": VERSION, "protocol": PROTOCOL_VERSION,
            "authorized": self.authorized,
            "tool_count": len(self._tools),
            "guard_mode": "explicit" if self.guard.explicit else "derived",
            "time": time.time(),
        }

    def _agent_policy(self) -> str:
        for candidate in (
            Path(__file__).parent.parent.parent / "AGENTS.md",
            Path.cwd() / "AGENTS.md",
        ):
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        return "AGENTS.md 未找到；请参照 docs/安全与认证.md 执行。"

    def _config_summary(self) -> dict:
        return {
            "volumes": len(self.config.get("volumes", []) or []),
            "write_targets": len(self.config.get("write_targets", []) or []),
            "allowed_paths": len(self.guard.roots),
            "guard_strict": self.guard.strict,
            "guard_mode": "explicit" if self.guard.explicit else "derived",
            "model_enabled": bool(self.config.get("classification.model.enabled", False)),
            "web_port": self.config.get("web.port", 8765),
        }

    def _security_status(self) -> dict:
        log = self.config.resolve(
            str(self.config.get("security.audit_log", "data/security_audit.jsonl"))
        )
        denied = 0
        if log.exists():
            try:
                for line in log.read_text(encoding="utf-8").splitlines():
                    if line.strip() and json.loads(line).get("allowed") is False:
                        denied += 1
            except (OSError, json.JSONDecodeError):
                pass
        return {
            "allowed_roots": self.guard.roots,
            "strict": self.guard.strict,
            "mode": "explicit" if self.guard.explicit else "derived",
            "audit_log": str(log),
            "denied_events": denied,
        }

    def _security_check(self, path: str) -> dict:
        normalized = self.guard.check(path, source="mcp", action="check")
        return {"path": path, "normalized": str(normalized),
                "allowed": self.guard.is_allowed(path)}

    # ---------- 工具实现：统计 ----------

    def _stats(self) -> dict:
        conn = self.db.connect()
        def count(sql: str, params: tuple = ()) -> dict:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else {}
        return {
            "total": self.db.count_files(),
            "by_volume": [dict(r) for r in conn.execute(
                "SELECT volume, COUNT(*) n, SUM(size) bytes FROM files "
                "WHERE status='active' GROUP BY volume")],
            "by_domain": [dict(r) for r in conn.execute(
                "SELECT domain, COUNT(*) n FROM files WHERE status='active' GROUP BY domain")],
            "by_type": [dict(r) for r in conn.execute(
                "SELECT ftype, COUNT(*) n FROM files WHERE status='active' GROUP BY ftype")],
            "by_temperature": [dict(r) for r in conn.execute(
                "SELECT temp, COUNT(*) n FROM files WHERE status='active' GROUP BY temp")],
            "duplicates": count(
                "SELECT COUNT(*) n, COALESCE(SUM(size),0) bytes FROM files WHERE is_duplicate=1"),
        }

    def _group_stat(self, column: str) -> list[dict]:
        if column not in ("volume", "domain", "ftype", "temp"):
            raise ToolError("不支持的统计维度")
        return [dict(r) for r in self.db.connect().execute(
            f"SELECT {column} AS value, COUNT(*) n, SUM(size) bytes "
            f"FROM files WHERE status='active' GROUP BY {column} ORDER BY n DESC")]

    def _duplicate_summary(self) -> dict:
        row = self.db.connect().execute(
            "SELECT COUNT(*) groups, COALESCE(SUM(wasted),0) wasted FROM ("
            "SELECT COUNT(*) n, SUM(size) wasted FROM files "
            "WHERE full_hash IS NOT NULL AND status='active' "
            "GROUP BY full_hash HAVING n>1)"
        ).fetchone()
        return {"groups": int(row["groups"]), "wasted_bytes": int(row["wasted"])}

    def _backup_verify(self) -> dict:
        from .backup import BackupManager
        return BackupManager(self.config, self.db).verify_cold()

    def _row_dict(self, table: str, row_id: int) -> dict:
        if table not in ("files", "plans", "plan_items"):
            raise ToolError("不支持的表")
        row = self.db.connect().execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        if not row:
            raise ToolError(f"记录 {row_id} 不存在")
        return dict(row)

    def _filter_clauses(self, args: dict) -> tuple[str, list]:
        clauses, params = ["status='active'"], []
        for key in ("domain", "ftype", "temp"):
            if args.get(key):
                clauses.append(f"{key}=?")
                params.append(args[key])
        return " AND ".join(clauses), params

    def _list_files(self, args: dict) -> list[dict]:
        where, params = self._filter_clauses(args)
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        offset = max(int(args.get("offset", 0)), 0)
        rows = self.db.connect().execute(
            f"SELECT id, path, size, mtime, domain, ftype, temp, confidence "
            f"FROM files WHERE {where} ORDER BY size DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return [self._safe_file(dict(r)) for r in rows]

    @staticmethod
    def _safe_file(row: dict) -> dict:
        row.pop("classified_by", None)
        return row

    def _count_files(self, args: dict) -> dict:
        where, params = self._filter_clauses(args)
        row = self.db.connect().execute(
            f"SELECT COUNT(*) n, COALESCE(SUM(size),0) bytes FROM files WHERE {where}",
            tuple(params),
        ).fetchone()
        return {"count": int(row["n"]), "bytes": int(row["bytes"])}

    def _search_files(self, args: dict) -> list[dict]:
        keyword = str(args.get("keyword", "")).strip()
        if not keyword:
            raise ToolError("keyword 不能为空")
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        rows = self.db.connect().execute(
            "SELECT id, path, size, ftype, domain FROM files "
            "WHERE status='active' AND (path LIKE ? OR filename LIKE ?) LIMIT ?",
            (f"%{keyword}%", f"%{keyword}%", limit),
        )
        return [dict(r) for r in rows]

    def _low_confidence(self, args: dict) -> list[dict]:
        threshold = float(args.get("threshold", 0.8))
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        rows = self.db.connect().execute(
            "SELECT id, path, domain, ftype, temp, confidence FROM files "
            "WHERE status='active' AND confidence < ? ORDER BY confidence LIMIT ?",
            (threshold, limit),
        )
        return [dict(r) for r in rows]

    def _large_files(self, args: dict) -> list[dict]:
        min_bytes = max(int(args.get("min_bytes", 2147483648)), 0)
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        rows = self.db.connect().execute(
            "SELECT id, path, size, ftype, temp FROM files "
            "WHERE status='active' AND size>=? ORDER BY size DESC LIMIT ?",
            (min_bytes, limit),
        )
        return [dict(r) for r in rows]

    def _old_files(self, args: dict) -> list[dict]:
        days = max(int(args.get("days", 365)), 1)
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        cutoff = time.time() - days * 86400
        rows = self.db.connect().execute(
            "SELECT id, path, size, mtime, temp FROM files "
            "WHERE status='active' AND mtime<? ORDER BY mtime LIMIT ?",
            (cutoff, limit),
        )
        return [dict(r) for r in rows]

    # ---------- 工具实现：重复与计划 ----------

    def _duplicates(self, args: dict) -> list[dict]:
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        conn = self.db.connect()
        groups = conn.execute(
            "SELECT full_hash, COUNT(*) n, SUM(size) wasted FROM files "
            "WHERE full_hash IS NOT NULL AND status='active' "
            "GROUP BY full_hash HAVING n>1 ORDER BY wasted DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(g) for g in groups]

    def _duplicate_group(self, digest: str) -> list[dict]:
        if not digest or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ToolError("hash 必须是 64 位 SHA-256 十六进制字符串")
        return [dict(r) for r in self.db.connect().execute(
            "SELECT id, path, size, mtime FROM files WHERE full_hash=?", (digest,))]

    def _list_plans(self, args: dict) -> list[dict]:
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        return [dict(r) for r in self.db.connect().execute(
            "SELECT id, name, status, item_count, created_at FROM plans "
            "ORDER BY id DESC LIMIT ?", (limit,))]

    def _get_plan(self, plan_id: int) -> dict:
        plan = self.db.get_plan(plan_id)
        if not plan:
            raise ToolError(f"计划 {plan_id} 不存在")
        return {
            "id": plan.id, "name": plan.name, "status": plan.status,
            "item_count": plan.item_count,
            "items": [{"id": i.id, "action": i.action, "src": i.src_path,
                       "dst": i.dst_path, "status": i.status} for i in plan.items],
        }

    def _preview_plan(self, args: dict) -> dict:
        plan = self._get_plan(int(args["plan_id"]))
        limit = min(max(int(args.get("limit", 30)), 1), 200)
        plan["items"] = plan["items"][:limit]
        return plan

    def _plan_items(self, args: dict) -> list[dict]:
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        sql = "SELECT id, action, src_path, dst_path, reason, status, error FROM plan_items WHERE plan_id=?"
        params: list = [int(args["plan_id"])]
        if args.get("status"):
            sql += " AND status=?"
            params.append(args["status"])
        sql += " LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.db.connect().execute(sql, tuple(params))]

    def _op_logs(self, args: dict) -> list[dict]:
        limit = min(max(int(args.get("limit", 50)), 1), 1000)
        sql = "SELECT * FROM op_logs"
        if not args.get("include_rolled_back", False):
            sql += " WHERE rolled_back=0"
        sql += " ORDER BY id DESC LIMIT ?"
        return [dict(r) for r in self.db.connect().execute(sql, (limit,))]

    def _scan_states(self) -> list[dict]:
        return [dict(r) for r in self.db.connect().execute(
            "SELECT * FROM scan_state ORDER BY volume")]

    def _scan_state(self, volume: str) -> dict:
        row = self.db.connect().execute(
            "SELECT * FROM scan_state WHERE volume=?", (volume,)).fetchone()
        return dict(row) if row else {"volume": volume, "status": "never"}

    def _rules(self) -> dict:
        result = {}
        for key in ("type_rules", "domain_rules", "temperature_rules"):
            rel = self.config.get(f"classification.{key}", "")
            path = self.config.resolve(str(rel)) if rel else None
            result[key] = {
                "path": str(path) if path else None,
                "exists": bool(path and path.exists()),
            }
        return result

    def _cleanup_discover(self, args: dict) -> dict:
        from .cleanup import CleanupFinder
        finder = CleanupFinder(self.config, self.db)
        report = finder.discover(args.get("roots", []), args.get("kinds"))
        return {"scanned_dirs": report.scanned_dirs, "by_kind": report.by_kind(),
                "total_bytes": report.total_bytes(),
                "candidates": [c.__dict__ for c in report.candidates[:500]]}

    def _backup_audit(self) -> dict:
        from .audit import BackupAuditor
        return BackupAuditor(self.config, self.db).audit().summary()

    def _recycle_list(self, args: dict) -> dict:
        from .recycle import RecycleBin
        rb = RecycleBin(self.config, self.db)
        return {"items": rb.list_items(stage=args.get("stage"),
                                       limit=int(args.get("limit", 100)))}

    def _recycle_stats(self) -> dict:
        from .recycle import RecycleBin
        return RecycleBin(self.config, self.db).stats()

    # ---------- 工具实现：D4 只读 ----------

    def _feedback_list(self, args: dict) -> dict:
        rows = self.db.get_feedback(field=args.get("field"), limit=int(args.get("limit", 100)))
        return {"enabled": bool(self.config.get("feedback.enabled", True)),
                "count": len(rows), "items": rows}

    def _ai_status(self) -> dict:
        enabled = bool(self.config.get("ai.enabled", False))
        provider = str(self.config.get("ai.provider", "ollama"))
        threshold = float(self.config.get("ai.threshold", 0.8))
        available = False
        if enabled:
            try:
                from .ai import build_provider
                p = build_provider(self.config)
                available = bool(p and p.available())
            except Exception:
                available = False
        return {"enabled": enabled, "provider": provider, "threshold": threshold,
                "available": available,
                "note": "未启用时零依赖加载；仅处理置信度低于阈值的疑难件"}

    def _media_capability(self) -> dict:
        from .media import MediaOrganizer
        from .perceptual import PerceptualFinder
        caps = MediaOrganizer.capability()
        caps.update(PerceptualFinder.capability())
        return caps

    def _photo_suggest(self, args: dict) -> dict:
        from .media import MediaOrganizer
        root = self.guard.check(args["target_root"], source="mcp", action="suggest", write=True)
        records = [r for r in self.db.get_all_active()
                   if (r.ftype or "") == args.get("ftype", "图片")]
        limit = int(args.get("limit", 200))
        items = MediaOrganizer(self.config).suggest_photos(records[:limit], str(root))
        return {"count": len(items), "items": items}

    def _music_suggest(self, args: dict) -> dict:
        from .media import MediaOrganizer
        root = self.guard.check(args["target_root"], source="mcp", action="suggest", write=True)
        records = [r for r in self.db.get_all_active() if (r.ftype or "") == "音乐"]
        limit = int(args.get("limit", 200))
        items = MediaOrganizer(self.config).suggest_music(records[:limit], str(root))
        return {"count": len(items), "items": items}

    def _phash_similar(self, args: dict) -> dict:
        from .perceptual import PerceptualFinder
        result = PerceptualFinder(self.config, self.db).find_similar()
        if "groups" in result:
            result["groups"] = result["groups"][:int(args.get("limit", 50))]
        return result

    # ---------- 工具实现：D5 只读 ----------

    def _view_list(self) -> dict:
        from .virtual import VirtualDirectory
        return VirtualDirectory(self.config, self.db).list_views()

    def _view_get(self, args: dict) -> dict:
        from .virtual import VirtualDirectory
        return VirtualDirectory(self.config, self.db).view(
            args["tag"], int(args.get("limit", 100)), int(args.get("offset", 0)))

    def _plan_progress(self, args: dict) -> dict:
        from .reliability import ProgressStore
        return ProgressStore(self.db).get(int(args["plan_id"]))

    def _csv_undo_preview(self, args: dict) -> dict:
        import csv
        p = Path(args["path"])
        if not p.exists():
            raise ToolError(f"CSV 不存在: {p}")
        with p.open("r", newline="", encoding="utf-8-sig") as f:
            count = sum(1 for _ in csv.DictReader(f))
        return {"path": str(p), "entries": count, "note": "预览只读；import 才生成回滚计划"}

    def _season_suggest(self, args: dict) -> dict:
        from .reliability import SeasonOrganizer
        root = self.guard.check(args["target_root"], source="mcp", action="suggest", write=True)
        records = [r for r in self.db.get_all_active() if (r.ftype or "") == "视频"]
        items = SeasonOrganizer(self.config, self.db).suggest(
            records[:int(args.get("limit", 200))], str(root))
        return {"count": len(items), "items": items}

    def _task_list(self, args: dict) -> dict:
        from .tasks import task_snapshot
        return task_snapshot(self.db, int(args.get("limit", 50)))

    # ---------- 工具实现：受控写 ----------

    def _require_confirm(self, args: dict) -> None:
        if args.get("confirm") is not True:
            raise ToolError("危险操作必须显式提供 confirm=true")

    def _scan(self) -> dict:
        from .scanner import Scanner
        return Scanner(self.config, self.db).scan_all()

    def _classify(self) -> dict:
        from .classifier import Classifier
        return Classifier(self.config, self.db).classify_all()

    def _dedupe(self) -> dict:
        from .dedupe import Deduper
        return Deduper(self.config, self.db).find_duplicates()

    def _plan_create(self, args: dict) -> dict:
        plan = Planner(self.config, self.db).build_plan(
            name=args.get("name"), domain=args.get("domain"),
            ftype=args.get("ftype"), temp=args.get("temp"),
            only_duplicates=bool(args.get("only_duplicates", False)),
            limit=int(args.get("limit", 0)),
        )
        return {"id": plan.id, "name": plan.name, "item_count": plan.item_count}

    def _plan_execute(self, args: dict) -> dict:
        self._require_confirm(args)
        from .executor import Executor
        return Executor(self.config, self.db).execute_plan(
            int(args["plan_id"]), int(args.get("limit", 0)))

    def _rollback(self, args: dict) -> dict:
        self._require_confirm(args)
        from .executor import Executor
        return Executor(self.config, self.db).rollback_last(int(args.get("limit", 10)))

    def _backup(self, args: dict) -> dict:
        self._require_confirm(args)
        from .backup import BackupManager
        return BackupManager(self.config, self.db).backup_hot()

    def _plan_batch(self, args: dict) -> dict:
        plans = Planner(self.config, self.db).build_batch_plans(
            group_by=str(args.get("group_by", "domain")),
            name_prefix=args.get("name_prefix"),
            limit=int(args.get("limit", 0)),
        )
        return {"plans": plans, "count": len(plans),
                "total_items": sum(p["items"] for p in plans)}

    def _recycle_promote(self, args: dict) -> dict:
        from .recycle import RecycleBin
        return RecycleBin(self.config, self.db).promote(args.get("days"))

    def _recycle_restore(self, args: dict) -> dict:
        self._require_confirm(args)
        from .recycle import RecycleBin
        return RecycleBin(self.config, self.db).restore(
            int(args["item_id"]), confirm=True)

    def _recycle_purge(self, args: dict) -> dict:
        self._require_confirm(args)
        from .recycle import RecycleBin
        return RecycleBin(self.config, self.db).purge(stage=2, confirm=True)

    def _feedback_add(self, args: dict) -> dict:
        # 反馈写入只改索引库，不碰用户文件；路径仍需通过白名单校验
        self.guard.check(args["path"], source="mcp", action="feedback")
        from .feedback import FeedbackLearner
        learner = FeedbackLearner(self.config, self.db)
        count = learner.learn_from_correction(args["path"], args["field"], args["value"])
        return {"ok": True, "keywords_recorded": count, "field": args["field"],
                "value": args["value"]}

    def _feedback_reset(self, args: dict) -> dict:
        self._require_confirm(args)
        from .feedback import FeedbackLearner
        removed = FeedbackLearner(self.config, self.db).reset()
        return {"ok": True, "removed": removed}

    def _view_tag(self, args: dict) -> dict:
        from .virtual import VirtualDirectory
        return VirtualDirectory(self.config, self.db).tag(int(args["file_id"]), args["tag"])

    def _view_untag(self, args: dict) -> dict:
        from .virtual import VirtualDirectory
        return VirtualDirectory(self.config, self.db).untag(int(args["file_id"]), args["tag"])

    def _view_auto_tag(self) -> dict:
        from .virtual import VirtualDirectory
        return VirtualDirectory(self.config, self.db).auto_tag()

    def _csv_undo_import(self, args: dict) -> dict:
        p = Path(args["path"])
        self.guard.check(p, source="mcp", action="undo-import")
        from .reliability import CSVUndo
        return CSVUndo(self.config, self.db).import_plan(str(p), args.get("name"))

    # ---------- JSON-RPC 调度 ----------

    def list_tools(self) -> list[dict]:
        return [
            {k: v for k, v in tool.items() if not k.startswith("_")}
            for tool in self._tools.values()
        ]

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        tool = self._tools.get(name)
        if not tool:
            return _error(f"未知工具: {name}")
        args = arguments or {}
        if not isinstance(args, dict):
            return _error("arguments 必须是对象")
        if tool["_write"] and not self.authorized:
            return _error("该工具需要授权：请设置有效的 NAS_ORGANIZER_TOKEN 后重启 MCP 服务")
        try:
            result = tool["_handler"](args)
            return _text(result)
        except (ToolError, PathViolation, ValueError, OSError) as exc:
            return _error(str(exc))
        except Exception as exc:
            return _error(f"工具执行失败: {type(exc).__name__}: {exc}")

    def handle(self, request: dict) -> dict | None:
        method = request.get("method")
        req_id = request.get("id")
        if method == "initialize":
            return {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "nas-organizer", "version": VERSION},
                },
            }
        if method == "notifications/initialized" or method == "notifications/cancelled":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": self.list_tools()}}
        if method == "tools/call":
            params = request.get("params") or {}
            result = self.call_tool(str(params.get("name", "")), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        return {
            "jsonrpc": "2.0", "id": req_id,
            "error": {"code": -32601, "message": f"未知方法: {method}"},
        }

    def serve(self) -> None:
        """stdio 循环：逐行读取 JSON-RPC 请求，响应写 stdout，日志写 stderr。"""
        if hasattr(sys.stdin, "reconfigure"):
            sys.stdin.reconfigure(encoding="utf-8")
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        print(f"[MCP] nas-organizer {VERSION} 已启动，工具 {len(self._tools)} 个，"
              f"授权状态={'已认证' if self.authorized else '只读模式'}", file=sys.stderr)
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response = {"jsonrpc": "2.0", "id": None,
                            "error": {"code": -32700, "message": f"JSON 解析失败: {exc}"}}
            else:
                try:
                    response = self.handle(request)
                except Exception as exc:
                    response = {"jsonrpc": "2.0", "id": request.get("id"),
                                "error": {"code": -32603, "message": str(exc)}}
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def main() -> int:
    config_path = os.getenv("NAS_ORGANIZER_CONFIG", "config/config.yaml")
    config = Config.load(config_path)
    db_path = config.resolve(str(config.get("database.path", "data/nas_organizer.db")))
    server = MCPServer(config, Database(db_path))
    server.serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
