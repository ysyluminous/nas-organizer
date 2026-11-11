"""安全执行器：复制 → 校验 → 删除，全程记录日志可回滚."""

import hashlib
import shutil
import time
from pathlib import Path

from .config import Config
from .db import Database
from .models import OpLog
from .recycle import RecycleBin
from .security import PathGuard, PathViolation


class Executor:
    """执行迁移计划，保证每步可回滚."""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self.recycle_root = Path(config.get("executor.recycle_root", "data/recycle"))
        self.recycle_root.mkdir(parents=True, exist_ok=True)
        self.guard = PathGuard(config)
        self.recycle = RecycleBin(config, db)

    def execute_plan(self, plan_id: int, limit: int = 0, resume: bool = False) -> dict:
        """执行计划中 pending 的条目。

        D5 断点续传：resume=True 时跳过 status=done 的条目，并对目标已存在的
        done 项重校验哈希，损坏则重置为 pending 重跑（防中断时写坏）。
        """
        from .reliability import ProgressStore

        plan = self.db.get_plan(plan_id)
        if not plan:
            raise ValueError(f"计划 {plan_id} 不存在")
        if plan.status not in ("draft", "confirmed", "executing"):
            raise ValueError(f"计划状态 {plan.status} 不可执行")

        with self.db.transaction() as conn:
            conn.execute("UPDATE plans SET status='executing' WHERE id=?", (plan_id,))

        progress = ProgressStore(self.db)
        items = [i for i in plan.items if i.status == "pending"]
        if resume:
            # 已完成的项：目标存在且哈希一致则跳过；否则重置重跑
            for done_item in (i for i in plan.items if i.status == "done"):
                dst = Path(done_item.dst_path or "")
                if dst.exists() and self._verify_exists(dst, done_item.id):
                    continue
                self._set_item_status(done_item.id, "pending", "续传重校验未通过，重新执行")
                items.insert(0, done_item)
        if limit > 0:
            items = items[:limit]

        stats = {"total": len(items), "done": 0, "failed": 0, "skipped": 0,
                 "resumed": bool(resume)}
        for item in items:
            result = self._execute_item(item.id, item.src_path, item.dst_path or "")
            stats[result] += 1
            if result == "done":
                progress.mark_done(plan_id, item.id, total=len(plan.items))

        # 全部 pending 清空才算 done；否则保持 executing 供续传
        remaining = self.db.connect().execute(
            "SELECT COUNT(*) AS n FROM plan_items WHERE plan_id=? AND status='pending'",
            (plan_id,),
        ).fetchone()["n"]
        final_status = "done" if remaining == 0 else "executing"
        with self.db.transaction() as conn:
            conn.execute("UPDATE plans SET status=? WHERE id=?", (final_status, plan_id))
        return stats

    def _verify_exists(self, dst: Path, item_id: int) -> bool:
        """续传重校验：op_logs 里记录了目标 sha256 时逐字节校验，否则仅比存在性。"""
        row = self.db.connect().execute(
            "SELECT sha256 FROM op_logs WHERE plan_item_id=? ORDER BY id DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        if row and row["sha256"]:
            try:
                return self._sha256(dst) == row["sha256"]
            except OSError:
                return False
        return True

    def _execute_item(self, item_id: int, src: str, dst: str) -> str:
        if not dst:
            self._set_item_status(item_id, "failed", "目标路径为空")
            return "failed"
        try:
            src_path = self.guard.check(
                src, source="executor", action="execute", write=True
            )
            dst_path = self.guard.check(
                dst, source="executor", action="execute", write=True
            )
        except PathViolation as exc:
            self._set_item_status(item_id, "failed", str(exc))
            return "failed"

        if not src_path.exists():
            self._set_item_status(item_id, "failed", "源文件不存在")
            return "failed"
        if dst_path.exists():
            if self.config.get("executor.on_conflict", "rename") == "rename":
                dst_path = self._rename_for_conflict(dst_path)
            else:
                self._set_item_status(item_id, "skipped", "目标已存在")
                return "skipped"

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dst_path.with_suffix(dst_path.suffix + ".tmp")

        try:
            # 1. 复制到临时文件
            shutil.copy2(src_path, tmp_path)
            # 2. 校验
            if not self._verify(src_path, tmp_path):
                tmp_path.unlink(missing_ok=True)
                self._set_item_status(item_id, "failed", "校验失败")
                return "failed"
            # 3. 重命名临时文件为目标
            tmp_path.rename(dst_path)
            # 4. 源文件移入两级回收站 stage1（不物理删除，可还原）
            recycle = Path(self.recycle.quarantine(src_path, reason=f"plan {item_id} move"))
            # 5. 记录日志
            self.db.log_op(OpLog(
                plan_item_id=item_id,
                action="move",
                src_path=str(recycle),
                dst_path=str(dst_path),
                sha256=self._sha256(dst_path),
                size=dst_path.stat().st_size,
                executed_at=time.time(),
            ))
            self._set_item_status(item_id, "done")
            return "done"
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            self._set_item_status(item_id, "failed", str(exc))
            return "failed"

    def _verify(self, src: Path, dst: Path) -> bool:
        if src.stat().st_size != dst.stat().st_size:
            return False
        return self._sha256(src) == self._sha256(dst)

    @staticmethod
    def _sha256(path: Path) -> str:
        h = hashlib.sha256()
        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def _rename_for_conflict(path: Path) -> Path:
        ts = time.strftime("%Y%m%d_%H%M%S")
        return path.with_name(f"{path.stem}_{ts}{path.suffix}")

    def _to_recycle(self, src: Path) -> Path:
        safe = str(src).replace("/", "_").replace("\\", "_").lstrip("_")
        return self.recycle_root / safe

    def _set_item_status(self, item_id: int, status: str, error: str | None = None) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE plan_items SET status=?, error=? WHERE id=?",
                (status, error, item_id),
            )

    def rollback_last(self, limit: int = 0) -> dict:
        """回滚最近的操作（按 op_logs 倒序）."""
        ops = self.db.get_unrolled_ops()
        if limit > 0:
            ops = ops[:limit]
        stats = {"total": len(ops), "done": 0, "failed": 0}
        for op in ops:
            try:
                dst = Path(op.dst_path)
                src_recycle = Path(op.src_path)
                if not dst.exists():
                    raise FileNotFoundError(f"目标不存在: {dst}")
                # 把目标移回回收区记录的原位置
                shutil.move(str(dst), str(src_recycle))
                self.db.mark_rolled_back(op.id)
                stats["done"] += 1
            except Exception as exc:
                print(f"[ROLLBACK] 失败 {op.id}: {exc}")
                stats["failed"] += 1
        return stats
