"""命令行入口."""

import argparse
import sys

from .config import Config
from .db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nas-organizer",
        description="NAS 数据治理工具：索引、分类、查重、迁移、备份",
    )
    parser.add_argument("--config", "-c", default=None, help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="初始化数据库")
    sub.add_parser("scan", help="扫描并建立索引")

    p_class = sub.add_parser("classify", help="分类")
    p_class.add_argument("--no-model", action="store_true", help="禁用模型兜底")

    sub.add_parser("dedupe", help="重复检测")

    p_plan = sub.add_parser("plan", help="生成迁移计划")
    p_plan.add_argument("--name", default=None)
    p_plan.add_argument("--domain", default=None)
    p_plan.add_argument("--ftype", default=None)
    p_plan.add_argument("--temp", default=None)
    p_plan.add_argument("--duplicates", action="store_true")
    p_plan.add_argument("--limit", type=int, default=0)

    p_show = sub.add_parser("show-plan", help="预览计划")
    p_show.add_argument("plan_id", type=int)

    p_exec = sub.add_parser("execute", help="执行计划")
    p_exec.add_argument("plan_id", type=int)
    p_exec.add_argument("--limit", type=int, default=0)

    p_rb = sub.add_parser("rollback", help="回滚")
    p_rb.add_argument("--limit", type=int, default=0)

    sub.add_parser("backup", help="热数据备份")
    sub.add_parser("verify", help="冷数据校验")
    sub.add_parser("stats", help="统计信息")

    p_serve = sub.add_parser("serve", help="启动 Web 控制台")
    p_serve.add_argument("--host", default=None)
    p_serve.add_argument("--port", type=int, default=None)
    p_serve.add_argument("--with-scheduler", action="store_true",
                         help="同时启动定时调度器（默认关闭，需在配置中启用）")

    p_sched = sub.add_parser("schedule", help="定时调度器管理")
    sched_sub = p_sched.add_subparsers(dest="schedule_action", required=True)
    sched_sub.add_parser("list", help="列出已启用的定时任务")
    sched_sub.add_parser("run", help="前台运行调度循环（Ctrl+C 停止）")
    sched_sub.add_parser("check", help="校验 cron 表达式（不启动任务）")

    args = parser.parse_args(argv)
    config = Config.load(args.config)
    db = Database(config.resolve(config.get("database.path", "data/nas_organizer.db")))

    if args.command == "init":
        db.init_schema()
        print("[OK] 数据库已初始化")

    elif args.command == "scan":
        from .scanner import Scanner
        results = Scanner(config, db).scan_all()
        print("[OK] 扫描完成:", results)

    elif args.command == "classify":
        from .classifier import Classifier
        if args.no_model:
            config.data["classification"]["model"]["enabled"] = False
        stats = Classifier(config, db).classify_all()
        print("[OK] 分类完成:", stats)

    elif args.command == "dedupe":
        from .dedupe import Deduper
        stats = Deduper(config, db).find_duplicates()
        print("[OK] 查重完成:", stats)

    elif args.command == "plan":
        from .planner import Planner
        plan = Planner(config, db).build_plan(
            name=args.name, domain=args.domain, ftype=args.ftype,
            temp=args.temp, only_duplicates=args.duplicates, limit=args.limit,
        )
        print(f"[OK] 计划已生成: ID={plan.id} 共 {plan.item_count} 项")

    elif args.command == "show-plan":
        from .planner import Planner
        Planner(config, db).preview(args.plan_id)

    elif args.command == "execute":
        from .executor import Executor
        stats = Executor(config, db).execute_plan(args.plan_id, args.limit)
        print("[OK] 执行完成:", stats)

    elif args.command == "rollback":
        from .executor import Executor
        stats = Executor(config, db).rollback_last(args.limit)
        print("[OK] 回滚完成:", stats)

    elif args.command == "backup":
        from .backup import BackupManager
        print("[OK] 备份:", BackupManager(config, db).backup_hot())

    elif args.command == "verify":
        from .backup import BackupManager
        print("[OK] 校验:", BackupManager(config, db).verify_cold())

    elif args.command == "stats":
        conn = db.connect()
        print(f"文件总数: {db.count_files()}")
        for row in conn.execute(
            "SELECT volume, COUNT(*) n FROM files WHERE status='active' GROUP BY volume"
        ):
            print(f"  {row[0]}: {row[1]}")

    elif args.command == "schedule":
        from .scheduler import Scheduler
        if args.schedule_action == "check":
            from .scheduler import parse_cron
            # 校验配置中所有 cron；无配置时给出提示
            scheduler = Scheduler(config, db)
            jobs = scheduler.describe()
            print(f"[OK] 调度配置校验通过，共 {len(jobs)} 个启用任务")
        elif args.schedule_action == "list":
            scheduler = Scheduler(config, db)
            jobs = scheduler.describe()
            if not jobs:
                print("未启用任何定时任务（默认安全状态）。")
            for job in jobs:
                print(f"  {job['name']}: {job['command']} [{job['cron']}]")
        elif args.schedule_action == "run":
            scheduler = Scheduler(config, db)
            if not scheduler.start():
                print("[WARN] 没有启用的任务，调度器未启动")
            else:
                try:
                    while True:
                        import time as _t
                        _t.sleep(1)
                except KeyboardInterrupt:
                    print("\n[scheduler] 收到中断，正在停止...")
                finally:
                    scheduler.stop()

    elif args.command == "serve":
        import uvicorn
        from .api import setup
        app = setup(config, db)
        scheduler = None
        if args.with_scheduler:
            from .scheduler import Scheduler
            scheduler = Scheduler(config, db)
            scheduler.start()
        try:
            uvicorn.run(
                app,
                host=args.host or config.get("web.host", "0.0.0.0"),
                port=args.port or config.get("web.port", 8765),
            )
        finally:
            if scheduler:
                scheduler.stop()

    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
