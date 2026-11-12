"""扫描卷绑定管理测试：绑定/解绑/合并/安全校验/持久化。"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from nas_organizer.config import Config  # noqa: E402
from nas_organizer import mounts  # noqa: E402


def check_all():
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        base = Path(d)
        # 模拟容器内挂载布局：/scan 下两个盘、/write 一个目标
        scan1 = base / "scan" / "media1"
        scan2 = base / "scan" / "media2"
        write = base / "write" / "target"
        for p in (scan1, scan2, write):
            p.mkdir(parents=True, exist_ok=True)

        cfg = Config({
            "database": {"path": str(base / "data" / "t.db")},
            "volumes": [
                {"name": "m1", "path": str(scan1), "role": "cold"},
                {"name": "m2", "path": str(scan2), "role": "hot"},
            ],
            "write_targets": [{"name": "t", "path": str(write)}],
        }, base_dir=base)

        # 1. 初始：两个静态卷生效
        vols = mounts.effective_volumes(cfg)
        assert [v["name"] for v in vols] == ["m1", "m2"], vols

        # 2. 绑定前需确认（API 层校验，这里测业务层异常）
        try:
            mounts.check_bind_path(cfg, "/etc")
            raise AssertionError("越界路径未被拒绝")
        except ValueError:
            pass
        try:
            mounts.check_bind_path(cfg, "relative/path")
            raise AssertionError("相对路径未被拒绝")
        except ValueError:
            pass

        # 3. 动态绑定一个同根下的新目录
        newdir = base / "scan" / "media3"
        newdir.mkdir(parents=True, exist_ok=True)
        r = mounts.bind(cfg, "m3", str(newdir), "warm")
        assert r["source"] == "dynamic" and r["role"] == "warm"
        assert [v["name"] for v in mounts.effective_volumes(cfg)] == ["m1", "m2", "m3"]

        # 4. 持久化：重新构造 Config 仍能看到
        cfg2 = Config({"database": {"path": str(base / "data" / "t.db")},
                       "volumes": cfg.get("volumes"),
                       "write_targets": cfg.get("write_targets")}, base_dir=base)
        assert "m3" in [v["name"] for v in mounts.effective_volumes(cfg2)]

        # 5. 解绑动态卷：真正移除
        mounts.unbind(cfg, "m3")
        assert [v["name"] for v in mounts.effective_volumes(cfg)] == ["m1", "m2"]

        # 6. 解绑静态卷：标记禁用，配置不动
        mounts.unbind(cfg, "m1")
        assert [v["name"] for v in mounts.effective_volumes(cfg)] == ["m2"]
        listed = {v["name"]: v for v in mounts.list_volumes(cfg)}
        assert listed["m1"]["enabled"] is False and listed["m1"]["exists"] is True

        # 7. 重新绑定同名静态卷：清除禁用标记
        mounts.bind(cfg, "m1", str(scan1), "cold")
        assert "m1" in [v["name"] for v in mounts.effective_volumes(cfg)]

        # 8. 非法名称被拒
        try:
            mounts.bind(cfg, "bad name!", str(scan1))
            raise AssertionError("非法名称未被拒绝")
        except ValueError:
            pass

        # 9. 不存在目录被拒
        try:
            mounts.bind(cfg, "m4", str(base / "scan" / "nope"))
            raise AssertionError("不存在目录未被拒绝")
        except ValueError:
            pass

        # 10. mounts.json 权限 600（POSIX；Windows/NTFS 无此语义，跳过）
        if os.name == "posix":
            st = mounts.state_path(cfg).stat()
            assert st.st_mode & 0o777 == 0o600, oct(st.st_mode)

    print("扫描卷绑定测试通过：绑定/解绑/静态禁用/重新启用/合并/越界校验/持久化/权限")


if __name__ == "__main__":
    check_all()
