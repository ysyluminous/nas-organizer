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
    p_plan.add_argument("--batch", choices=["domain", "ftype", "temp", "domain_ftype"],
                        default=None, help="批量归类：按字段拆成多个计划")

    p_show = sub.add_parser("show-plan", help="预览计划")
    p_show.add_argument("plan_id", type=int)

    p_exec = sub.add_parser("execute", help="执行计划")
    p_exec.add_argument("plan_id", type=int)
    p_exec.add_argument("--limit", type=int, default=0)
    p_exec.add_argument("--resume", action="store_true",
                        help="断点续传：跳过已完成项，重校验已复制文件，中断可安全重跑")

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

    p_fb = sub.add_parser("feedback", help="人工反馈学习（越用越准）")
    fb_sub = p_fb.add_subparsers(dest="feedback_action", required=True)
    p_fb_add = fb_sub.add_parser("add", help="记录一次人工纠正")
    p_fb_add.add_argument("path", help="被纠正文件的路径（抽取关键词）")
    p_fb_add.add_argument("--field", choices=["domain", "ftype"], required=True)
    p_fb_add.add_argument("--value", required=True, help="人工指定的归属域或类型")
    p_fb_list = fb_sub.add_parser("list", help="列出反馈条目")
    p_fb_list.add_argument("--field", choices=["domain", "ftype"], default=None)
    p_fb_reset = fb_sub.add_parser("reset", help="清空全部反馈")
    p_fb_reset.add_argument("--confirm", action="store_true", help="确认清空（必须）")

    p_view = sub.add_parser("view", help="多维虚拟目录（标签归类，不移动文件）")
    view_sub = p_view.add_subparsers(dest="view_action", required=True)
    view_sub.add_parser("list", help="列出所有虚拟视图")
    p_vget = view_sub.add_parser("get", help="查看某视图下的文件")
    p_vget.add_argument("tag")
    p_vget.add_argument("--limit", type=int, default=100)
    p_vtag = view_sub.add_parser("tag", help="给文件打标签")
    p_vtag.add_argument("file_id", type=int)
    p_vtag.add_argument("tag")
    p_vuntag = view_sub.add_parser("untag", help="移除文件标签")
    p_vuntag.add_argument("file_id", type=int)
    p_vuntag.add_argument("tag")
    view_sub.add_parser("auto", help="按域/类型/温度/年份自动打标（幂等）")

    p_undo = sub.add_parser("undo", help="CSV 全量回滚（跨实例可移植）")
    undo_sub = p_undo.add_subparsers(dest="undo_action", required=True)
    p_uexp = undo_sub.add_parser("export", help="导出未回滚操作为 CSV")
    p_uexp.add_argument("--output", "-o", required=True)
    p_uexp.add_argument("--limit", type=int, default=0)
    p_uimp = undo_sub.add_parser("import", help="从 CSV 生成回滚计划（不执行）")
    p_uimp.add_argument("path")
    p_uimp.add_argument("--name", default=None)

    p_media = sub.add_parser("media", help="媒体元数据感知（只读建议）")
    media_sub = p_media.add_subparsers(dest="media_action", required=True)
    media_sub.add_parser("capability", help="查看 exif/ID3/pHash 依赖可用性")
    p_photo = media_sub.add_parser("suggest-photos", help="按 exif 日期生成照片归档建议")
    p_photo.add_argument("--target", required=True, help="归档根目录")
    p_photo.add_argument("--limit", type=int, default=200)
    p_music = media_sub.add_parser("suggest-music", help="按 ID3 标签生成音乐归档建议")
    p_music.add_argument("--target", required=True)
    p_music.add_argument("--limit", type=int, default=200)
    media_sub.add_parser("phash-similar", help="感知哈希查找相似图片（只读）")
    p_season = media_sub.add_parser("season-suggest", help="剧集季数识别归档建议（只读）")
    p_season.add_argument("--target", required=True)
    p_season.add_argument("--limit", type=int, default=200)

    sub.add_parser("tasks", help="查看任务监控快照（运行中进度与历史，只读）")

    p_recycle = sub.add_parser("recycle", help="两级回收站管理")
    rec_sub = p_recycle.add_subparsers(dest="recycle_action", required=True)
    rec_sub.add_parser("list", help="列出回收站项目")
    rec_sub.add_parser("stats", help="回收站统计")
    p_promote = rec_sub.add_parser("promote", help="stage1 超时项提升到 stage2")
    p_promote.add_argument("--days", type=int, default=None)
    p_restore = rec_sub.add_parser("restore", help="还原项目到原路径")
    p_restore.add_argument("item_id", type=int)
    p_restore.add_argument("--confirm", action="store_true", help="确认还原")
    p_purge = rec_sub.add_parser("purge", help="清除 stage2（不可逆）")
    p_purge.add_argument("--confirm", action="store_true", help="确认清除（必须）")

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
        planner = Planner(config, db)
        if args.batch:
            def _progress(done: int, total: int, label: str) -> None:
                print(f"[批量归类进度] {done}/{total} ({label})")
            results = planner.build_batch_plans(
                group_by=args.batch, name_prefix=args.name,
                limit=args.limit, progress=_progress,
            )
            print(f"[OK] 批量归类完成: {len(results)} 个计划")
            for r in results:
                print(f"  [{r['plan_id']}] {r['name']}: {r['items']} 项")
        else:
            plan = planner.build_plan(
                name=args.name, domain=args.domain, ftype=args.ftype,
                temp=args.temp, only_duplicates=args.duplicates, limit=args.limit,
            )
            print(f"[OK] 计划已生成: ID={plan.id} 共 {plan.item_count} 项")

    elif args.command == "show-plan":
        from .planner import Planner
        Planner(config, db).preview(args.plan_id)

    elif args.command == "execute":
        from .executor import Executor
        stats = Executor(config, db).execute_plan(args.plan_id, args.limit, resume=args.resume)
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

    elif args.command == "view":
        from .virtual import VirtualDirectory
        vd = VirtualDirectory(config, db)
        action = args.view_action
        if action == "list":
            views = vd.list_views()
            print(f"[OK] 虚拟视图共 {views['total_tags']} 个标签")
            for prefix, tags in views["groups"].items():
                print(f"  [{prefix}]")
                for t in tags:
                    print(f"    {t['name']}: {t['file_count']} 文件")
        elif action == "get":
            result = vd.view(args.tag, args.limit)
            print(f"[OK] 视图 '{args.tag}'：{result['count']} 个文件（虚拟，未移动）")
            for f in result["files"][:30]:
                print(f"  {f['path']}")
        elif action == "tag":
            print("[OK] 打标:", vd.tag(args.file_id, args.tag)["tags"])
        elif action == "untag":
            print("[OK] 移除标签:", vd.untag(args.file_id, args.tag)["tags"])
        elif action == "auto":
            print("[OK] 自动打标:", vd.auto_tag())

    elif args.command == "undo":
        from .reliability import CSVUndo
        cu = CSVUndo(config, db)
        if args.undo_action == "export":
            print("[OK] 导出:", cu.export(args.output, args.limit))
        elif args.undo_action == "import":
            print("[OK] 回滚计划:", cu.import_plan(args.path, args.name))

    elif args.command == "tasks":
        from .tasks import task_snapshot
        snap = task_snapshot(db)
        print(f"[OK] 运行中任务 {snap['running_count']} 个")
        for t in snap["recent"]:
            print(f"  [{t['status']}] {t['name']}: {t['done']}/{t['total']} ({t['progress']:.0%})")
        for h in snap["history"][:20]:
            print(f"  [history:{h['source']}] {h['name']}: {h['status']} {h['duration_sec']}s")

    elif args.command == "feedback":
        from .feedback import FeedbackLearner
        learner = FeedbackLearner(config, db)
        action = args.feedback_action
        if action == "add":
            n = learner.learn_from_correction(args.path, args.field, args.value)
            print(f"[OK] 反馈已记录：{n} 个关键词 → {args.field}={args.value}")
        elif action == "list":
            rows = db.get_feedback(field=args.field)
            if not rows:
                print("暂无反馈记录")
            for r in rows:
                print(f"  [{r['id']}] {r['field']}={r['value']} ← '{r['keyword']}' (命中 {r['hits']})")
        elif action == "reset":
            if not args.confirm:
                print("[拒绝] 清空反馈需要 --confirm")
            else:
                print(f"[OK] 已清空 {learner.reset()} 条反馈")

    elif args.command == "media":
        from .media import MediaOrganizer
        from .perceptual import PerceptualFinder
        from .security import PathGuard
        guard = PathGuard(config)
        action = args.media_action
        if action == "capability":
            caps = MediaOrganizer.capability()
            caps.update(PerceptualFinder.capability())
            for k, v in caps.items():
                print(f"  {k}: {'可用' if v else '不可用（将优雅降级）'}")
        elif action == "suggest-photos":
            root = guard.check(args.target, source="cli", action="suggest", write=True)
            recs = [r for r in db.get_all_active() if (r.ftype or "") == "图片"][:args.limit]
            items = MediaOrganizer(config).suggest_photos(recs, str(root))
            print(f"[OK] 照片归档建议 {len(items)} 项（只读，未移动文件）")
            for it in items[:30]:
                print(f"  {it['date']}[{it['source']}] {it['src_path']}\n      -> {it['dst_path']}")
        elif action == "suggest-music":
            root = guard.check(args.target, source="cli", action="suggest", write=True)
            recs = [r for r in db.get_all_active() if (r.ftype or "") == "音乐"][:args.limit]
            items = MediaOrganizer(config).suggest_music(recs, str(root))
            print(f"[OK] 音乐归档建议 {len(items)} 项（只读，未移动文件）")
            for it in items[:30]:
                print(f"  [{it['source']}] {it['src_path']}\n      -> {it['dst_path']}")
        elif action == "phash-similar":
            result = PerceptualFinder(config, db).find_similar()
            print(f"[OK] 感知哈希扫描：{result.get('images_scanned', 0)} 张图片，"
                  f"{result.get('similar_groups', 0)} 组相似，{result.get('similar_pairs', 0)} 对")
            for g in result.get("groups", [])[:20]:
                print(f"  [距离 {g['max_distance']}] {g['paths'][0]}")
                for p in g["paths"][1:]:
                    print(f"      ≈ {p}")
        elif action == "season-suggest":
            from .reliability import SeasonOrganizer
            root = guard.check(args.target, source="cli", action="suggest", write=True)
            recs = [r for r in db.get_all_active() if (r.ftype or "") == "视频"][:args.limit]
            items = SeasonOrganizer(config, db).suggest(recs, str(root))
            print(f"[OK] 剧集归档建议 {len(items)} 项（只读，未移动）")
            for it in items[:30]:
                print(f"  S{it['season']:02d}E{it['episode']:02d} {it['title']}\n      -> {it['dst_path']}")

    elif args.command == "recycle":
        from .recycle import RecycleBin
        rb = RecycleBin(config, db)
        action = args.recycle_action
        if action == "list":
            items = rb.list_items()
            if not items:
                print("回收站为空")
            for it in items:
                print(f"  [{it['id']}] stage{it['stage']} {it['original_path']}")
        elif action == "stats":
            print("[OK] 回收站统计:", rb.stats())
        elif action == "promote":
            print("[OK] 提升:", rb.promote(args.days))
        elif action == "restore":
            result = rb.restore(args.item_id, confirm=args.confirm)
            print("[OK]" if result.get("ok") else "[拒绝]", result)
        elif action == "purge":
            result = rb.purge(stage=2, confirm=args.confirm)
            print("[OK]" if result.get("ok") else "[拒绝]", result)

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
