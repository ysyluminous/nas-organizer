"""应用级路径白名单测试（可直接运行，无需 pytest）。"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.executor import Executor
from nas_organizer.models import FileRecord, Plan, PlanItem
from nas_organizer.security import PathGuard, PathViolation


def _config(base: Path, **overrides):
    data = {"security": {
        "allowed_paths": [],
        "strict": True,
        "audit_log": "data/security_audit.jsonl",
    }}
    data.update(overrides)
    return Config(data, base)


def test_explicit_allowlist():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        allowed = base / "allowed"
        outside = base / "outside"
        allowed.mkdir()
        outside.mkdir()

        cfg = _config(base, security={
            "allowed_paths": [str(allowed)], "strict": True,
            "audit_log": "data/security_audit.jsonl",
        })
        guard = PathGuard(cfg)

        assert guard.is_allowed(allowed / "song.flac"), "白名单内路径应放行"
        assert not guard.is_allowed(outside / "secret.txt"), "白名单外路径应拒绝"

        try:
            guard.check(outside / "secret.txt", source="test", action="read")
            raise AssertionError("strict 模式应抛出 PathViolation")
        except PathViolation:
            pass
    print("[OK] 显式白名单：内路径放行，外路径拒绝")


def test_traversal_blocked():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        allowed = base / "allowed"
        allowed.mkdir()
        cfg = _config(base, security={
            "allowed_paths": [str(allowed)], "strict": True,
            "audit_log": "data/security_audit.jsonl",
        })
        guard = PathGuard(cfg)

        traversing = allowed / ".." / "outside" / "x.txt"
        assert not guard.is_allowed(traversing), ".. 穿越必须被拦截"
        assert any(".." in str(r) for r in guard.roots) is False
    print("[OK] 路径穿越：.. 归一化后越界被拦截")


def test_auto_derived_roots():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        scan = base / "scan"
        write = base / "write"
        scan.mkdir()
        write.mkdir()
        cfg = _config(base, volumes=[{"name": "v1", "path": str(scan)}],
                      write_targets=[{"name": "w1", "path": str(write)}])
        guard = PathGuard(cfg)

        assert guard.explicit is False, "未显式配置应标记为自动推导"
        assert guard.is_allowed(scan / "a.mp4"), "自动推导应包含扫描卷"
        assert guard.is_allowed(write / "b.mp4"), "自动推导应包含写入目标"
        assert guard.is_allowed(base / "data" / "db.sqlite"), "自动推导应包含 data 目录"
        assert not guard.is_allowed(base / "etc" / "passwd")
    print("[OK] 自动推导：存量配置升级后仍可正常工作")


def test_audit_log_written():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        allowed = base / "allowed"
        allowed.mkdir()
        cfg = _config(base, security={
            "allowed_paths": [str(allowed)], "strict": False,
            "audit_log": "data/security_audit.jsonl",
        })
        guard = PathGuard(cfg)

        result = guard.check(base / "outside" / "x.txt", source="test", action="probe")
        assert result == (base / "outside" / "x.txt").resolve()

        log = base / "data" / "security_audit.jsonl"
        assert log.exists(), "审计日志应被创建"
        entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        assert any(e["allowed"] is False and e["source"] == "test" for e in entries)
    print("[OK] 审计日志：非严格模式放行越界但留痕")


def test_executor_blocks_violation():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        allowed = base / "allowed"
        write = base / "write"
        allowed.mkdir()
        write.mkdir()
        src = allowed / "src.txt"
        src.write_text("hello", encoding="utf-8")

        cfg = _config(base, security={
            "allowed_paths": [str(allowed), str(write)], "strict": True,
            "audit_log": "data/security_audit.jsonl",
        })
        db = Database(base / "data" / "test.db")
        db.init_schema()
        try:
            file_id = db.upsert_file(FileRecord(
                path=str(src), volume="v1", filename="src.txt", ext="txt",
                size=5, mtime=0, atime=0, ctime=0,
            ))
            plan = Plan(name="evil", description="outside write")
            plan.id = db.create_plan(plan)
            item = PlanItem(
                file_id=file_id, plan_id=plan.id, action="move",
                src_path=str(src), dst_path=str(base / "outside" / "stolen.txt"),
                reason="test",
            )
            item.id = db.add_plan_item(plan.id, item)

            stats = Executor(cfg, db).execute_plan(plan.id)
            assert stats["failed"] == 1 and stats["done"] == 0, stats
            row = db.connect().execute(
                "SELECT status, error FROM plan_items WHERE id=?", (item.id,)
            ).fetchone()
            assert row["status"] == "failed"
            assert "路径越界" in row["error"], row["error"]
            assert not (base / "outside" / "stolen.txt").exists()
        finally:
            db.close()
    print("[OK] 执行器拦截：越界写入计划被标记失败且未产生文件")


if __name__ == "__main__":
    test_explicit_allowlist()
    test_traversal_blocked()
    test_auto_derived_roots()
    test_audit_log_written()
    test_executor_blocks_violation()
    print("白名单测试全部通过")
