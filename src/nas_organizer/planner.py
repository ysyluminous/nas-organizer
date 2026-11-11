"""迁移计划生成：根据分类结果计算目标路径."""

import time
from collections import Counter
from pathlib import Path

from .config import Config
from .db import Database
from .models import FileRecord, Plan, PlanItem

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
