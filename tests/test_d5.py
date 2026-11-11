"""D5 单元测试：虚拟目录 / 断点续传 / CSV undo / 剧集识别 / 任务监控。"""

import csv
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config
from nas_organizer.db import Database
from nas_organizer.models import FileRecord, OpLog, Plan
from nas_organizer.reliability import CSVUndo, ProgressStore, SeasonOrganizer, parse_episode
from nas_organizer.tasks import TaskRegistry, task_snapshot
from nas_organizer.virtual import VirtualDirectory


def make_db() -> Database:
    db = Database(":memory:")
    db.init_schema()
    return db


def add_file(db, path: str, domain="工作", ftype="文档", temp="热") -> int:
    ts = time.time()
    rec = FileRecord(path=path, volume="v1", filename=Path(path).name,
                     ext=Path(path).suffix.lstrip("."), size=100,
                     mtime=ts, atime=ts, ctime=ts, domain=domain, ftype=ftype, temp=temp)
    fid = db.upsert_file(rec)
    db.update_classification(fid, ftype, domain, temp, 0.9, "test")
    return fid


def test_virtual_directory():
    db = make_db()
    f1 = add_file(db, "/nas/合同2024.pdf")
    f2 = add_file(db, "/nas/电影S01E01.mp4", "生活", "视频")
    vd = VirtualDirectory(Config({}), db)
    vd.tag(f1, "重点项目")
    vd.tag(f1, "域:工作")  # 同一文件多标签
    # 幂等
    vd.tag(f1, "重点项目")
    assert set(db.get_file_tags(f1)) == {"重点项目", "域:工作"}
    assert len(vd.view("重点项目")["files"]) == 1
    assert vd.view("重点项目")["files"][0]["id"] == f1
    vd.untag(f1, "重点项目")
    assert "重点项目" not in db.get_file_tags(f1)
    removed = vd.delete_tag("域:工作")["assignments_removed"]
    assert removed >= 1 and vd.view("域:工作")["count"] == 0
    print("[OK] 虚拟目录：多标签、幂等打标、视图检索、源零改动")


def test_auto_tag():
    db = make_db()
    add_file(db, "/nas/合同.pdf")
    add_file(db, "/nas/照片.jpg", "生活", "图片")
    vd = VirtualDirectory(Config({}), db)
    result = vd.auto_tag()
    assert result["files"] == 2
    tags = {t["name"] for t in db.list_tags()}
    assert "域:工作" in tags and "域:生活" in tags and "类型:文档" in tags
    assert any(t.startswith("年份:") for t in tags)
    # 幂等重跑
    vd.auto_tag()
    assert vd.stats()["total_tags"] == len(tags)
    print("[OK] 自动打标：域/类型/温度/年份四维度，幂等")


def test_progress_store():
    db = make_db()
    ps = ProgressStore(db)
    assert ps.get(1)["last_done"] == 0
    ps.mark_done(1, 5, total=10)
    assert ps.get(1)["last_done"] == 5 and ps.get(1)["total"] == 10
    ps.mark_done(1, 9, total=12)
    assert ps.get(1)["last_done"] == 9 and ps.get(1)["total"] == 12
    ps.reset(1)
    assert ps.get(1)["last_done"] == 0
    print("[OK] 断点续传进度：标记/更新/重置")


def test_csv_undo_roundtrip():
    db = make_db()
    fid = add_file(db, "/orig/合同.pdf")
    plan = Plan(name="p"); plan.id = db.create_plan(plan)
    item_id = db.add_plan_item(plan.id, __import__(
        "nas_organizer.models", fromlist=["PlanItem"]).PlanItem(
        file_id=fid, action="move", src_path="/recycle/合同.pdf",
        dst_path="/write/合同.pdf"))
    db.log_op(OpLog(plan_item_id=item_id, action="move",
                    src_path="/recycle/合同.pdf", dst_path="/write/合同.pdf",
                    size=100, executed_at=time.time()))
    with tempfile.TemporaryDirectory() as d:
        csv_path = Path(d) / "undo.csv"
        cu = CSVUndo(Config({"security": {"allowed_paths": [str(Path(d))], "strict": True}}), db)
        exp = cu.export(csv_path)
        assert exp["exported"] == 1
        rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
        assert rows[0]["dst_path"] == "/write/合同.pdf"
        imp = cu.import_plan(csv_path, name="undo-1")
        assert imp["items"] == 1
        restored = db.get_plan(imp["plan_id"])
        assert restored.items[0].src_path == "/write/合同.pdf"
        assert restored.items[0].dst_path == "/recycle/合同.pdf"
    print("[OK] CSV undo：导出→导入生成反向计划，外键约束满足")


def test_csv_undo_cross_instance_placeholder():
    # 目标路径在库中不存在时创建占位记录，不污染 active 统计
    db = make_db()
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=CSVUndo.FIELDS)
            w.writeheader()
            w.writerow({"plan_item_id": 1, "action": "move", "src_path": "/a",
                        "dst_path": "/not-in-db/file.pdf", "sha256": "", "size": 0,
                        "executed_at": 0})
        cu = CSVUndo(Config({"security": {"allowed_paths": [str(Path(d))], "strict": True}}), db)
        assert cu.import_plan(p)["items"] == 1
        assert db.count_files() == 0  # 占位记录为 archived
    print("[OK] CSV undo 跨实例：占位记录不污染 active 统计")


def test_episode_parsing():
    cases = {
        "剧名.S01E02.mkv": ("剧名", 1, 2),
        "西游记 第03集.mp4": ("西游记", 1, 3),
        "Friends EP05.avi": ("Friends", 1, 5),
        "Show 2x07.mkv": ("Show", 2, 7),
    }
    for fn, (title, season, ep) in cases.items():
        info = parse_episode(fn)
        assert info, fn
        assert (info["title"], info["season"], info["episode"]) == (title, season, ep), (fn, info)
    assert parse_episode("周杰伦-晴天.flac") is None
    print("[OK] 剧集识别：S01E02 / 第N集 / EP / NxNN 四种，非剧集零误判")


def test_season_suggest():
    db = make_db()
    add_file(db, "/nas/剧名.S01E02.mkv", "生活", "视频")
    add_file(db, "/nas/剧名.S02E01.mkv", "生活", "视频")
    org = SeasonOrganizer(Config({}), db)
    recs = db.get_all_active()
    items = org.suggest(recs, "/write/series")
    assert len(items) == 2
    seasons = {i["season_dir"] for i in items}
    assert seasons == {"Season 01", "Season 02"}, seasons
    assert all("剧名" in i["dst_path"] and "Season" in i["dst_path"] for i in items)
    print("[OK] 剧集归档建议：按季分目录")


def test_task_registry():
    reg = TaskRegistry()
    reg.start("t1", "scan", total=100)
    reg.update("t1", 50)
    assert reg.get("t1")["progress"] == 0.5
    reg.finish("t1")
    assert reg.get("t1")["status"] == "done"
    assert "duration_sec" in reg.get("t1")
    reg.start("t2", "failing")
    reg.finish("t2", error="boom")
    assert reg.get("t2")["status"] == "failed"
    assert reg.clear_finished() == 2
    print("[OK] 任务监控：进度/完成/失败/清理")


if __name__ == "__main__":
    test_virtual_directory()
    test_auto_tag()
    test_progress_store()
    test_csv_undo_roundtrip()
    test_csv_undo_cross_instance_placeholder()
    test_episode_parsing()
    test_season_suggest()
    test_task_registry()
    print("D5 测试全部通过")
