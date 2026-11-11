"""迁移计划生成：根据分类结果计算目标路径."""

import time
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Config
from .db import Database
from .models import FileRecord, Plan, PlanItem
from .security import PathGuard, PathViolation

# 每个写入目标卷对应的归属域
VOLUME_DOMAIN_MAP = {
    "volume4": ["工作", "个人"],   # SSD 热数据
    "volume5": ["个人", "生活", "工作"],  # 温数据主盘
    "volume1": ["个人"],
    "volume2": ["资源", "工作"],
    "volume3": ["资源"],
}


class Planner:
    """生成迁移计划，不改变任何文件."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.target_root = config.get("backup.hot.source", "/write/16t2")
        self.conflict_policy = config.get("planner.conflict_policy", "rename")
        self.guard = PathGuard(config)

    def build_plan(
        self,
        name: str | None = None,
        domain: str | None = None,
        ftype: str | None = None,
        temp: str | None = None,
        only_duplicates: bool = False,
        limit: int = 0,
    ) -> Plan:
        """按筛选条件生成计划."""
        records = self.db.get_all_active()
        filtered = [
            r for r in records
            if self._match(r, domain, ftype, temp, only_duplicates)
        ]
        if limit > 0:
            filtered = filtered[:limit]

        plan = Plan(
            name=name or f"plan-{time.strftime('%Y%m%d-%H%M%S')}",
            description=f"domain={domain} ftype={ftype} temp={temp} dup={only_duplicates}",
        )
        plan.id = self.db.create_plan(plan)

        for rec in filtered:
            dst = self._target_path(rec)
            if dst is None or dst == rec.path:
                continue
            try:
                self.guard.check(rec.path, source="planner", action="plan")
                self.guard.check(dst, source="planner", action="plan", write=True)
            except PathViolation:
                continue
            item = PlanItem(
                file_id=rec.id,
                action="move",
                src_path=rec.path,
                dst_path=str(dst),
                reason=f"{rec.domain}/{rec.ftype}/{rec.temp}",
            )
            item.id = self.db.add_plan_item(plan.id, item)
            plan.items.append(item)

        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE plans SET item_count=? WHERE id=?",
                (len(plan.items), plan.id),
            )
        plan.item_count = len(plan.items)
        print(f"[PLAN] 生成计划 {plan.name}: {plan.item_count} 项")
        return plan

    def _match(
        self, r: FileRecord, domain: str | None, ftype: str | None,
        temp: str | None, only_duplicates: bool,
    ) -> bool:
        if domain and r.domain != domain:
            return False
        if ftype and r.ftype != ftype:
            return False
        if temp and r.temp != temp:
            return False
        if only_duplicates and not r.is_duplicate:
            return False
        return True

    def _target_path(self, rec: FileRecord) -> Path | None:
        """计算目标路径：/{target}/{domain}/{ftype}/{temp}/{filename}."""
        if not rec.domain or not rec.ftype or not rec.temp:
            return None
        # 重复文件计划移入待删除候选区
        if rec.is_duplicate:
            return Path(self.target_root) / "_待删除候选" / rec.filename
        target_volume = self._pick_volume(rec)
        return (
            Path(self.target_root)
            / target_volume
            / rec.domain
            / rec.ftype
            / rec.temp
            / rec.filename
        )

    @staticmethod
    def _pick_volume(rec: FileRecord) -> str:
        if rec.temp == "热":
            return "volume4"
        if rec.temp == "冷":
            # 资源类冷数据放归档盘，其余放主盘
            return "volume2" if rec.domain == "资源" else "volume1"
        return "volume5"

    def build_batch_plans(
        self,
        group_by: str = "domain",
        name_prefix: str | None = None,
        limit: int = 0,
        progress: Any = None,
    ) -> list[dict]:
        """批量归类：按 group_by 字段把文件拆成多个计划，一次调用生成全部。

        参数：
          group_by: domain / ftype / temp / domain_ftype
          limit:    总文件数上限，0 表示不限
          progress: 可选回调 progress(done, total, label)
        返回：每个计划的摘要列表（只读生成计划，不执行任何文件移动）。
        """
        valid = {"domain", "ftype", "temp", "domain_ftype"}
        if group_by not in valid:
            raise ValueError(f"group_by 必须是 {sorted(valid)} 之一")

        records = self.db.get_all_active()
        if limit > 0:
            records = records[:limit]
        total = len(records)

        groups: dict[str, list] = {}
        for rec in records:
            if group_by == "domain_ftype":
                key = f"{rec.domain or '未分类'}/{rec.ftype or '其他'}"
            else:
                key = str(getattr(rec, group_by, None) or "未分类")
            groups.setdefault(key, []).append(rec)

        prefix = name_prefix or f"batch-{time.strftime('%Y%m%d-%H%M%S')}"
        results: list[dict] = []
        done = 0
        for key, recs in sorted(groups.items()):
            plan = Plan(name=f"{prefix}-{key}", description=f"batch group_by={group_by} key={key}")
            plan.id = self.db.create_plan(plan)
            added = 0
            for rec in recs:
                dst = self._target_path(rec)
                if dst and dst != rec.path:
                    try:
                        self.guard.check(rec.path, source="planner", action="plan")
                        self.guard.check(dst, source="planner", action="plan", write=True)
                    except PathViolation:
                        pass
                    else:
                        item = PlanItem(
                            file_id=rec.id, action="move", src_path=rec.path,
                            dst_path=str(dst), reason=f"batch:{key}",
                        )
                        item.id = self.db.add_plan_item(plan.id, item)
                        added += 1
                done += 1
                if progress and done % 100 == 0:
                    progress(done, total, key)
            with self.db.transaction() as conn:
                conn.execute("UPDATE plans SET item_count=? WHERE id=?", (added, plan.id))
            results.append({"plan_id": plan.id, "name": plan.name, "group": key, "items": added})
        if progress:
            progress(done, total, "done")
        print(f"[PLAN] 批量归类完成: {len(results)} 个计划 / {total} 文件")
        return results

    def preview(self, plan_id: int, limit: int = 30) -> None:
        """打印计划预览."""
        plan = self.db.get_plan(plan_id)
        if not plan:
            print(f"[ERROR] 计划 {plan_id} 不存在")
            return
        print(f"\n计划: {plan.name} (ID={plan.id})  共 {plan.item_count} 项  状态={plan.status}")
        for item in plan.items[:limit]:
            print(f"  [{item.id}] {item.action}: {item.src_path}")
            print(f"        -> {item.dst_path}  ({item.reason})")
        if plan.item_count > limit:
            print(f"  ... 还有 {plan.item_count - limit} 项")
