"""扫描卷（运行时挂载）绑定管理.

背景：容器无法在运行时热挂载宿主机物理磁盘——真正的磁盘挂载由 docker-compose
的 volumes 决定。本模块管理的是**应用层的扫描卷**：把容器内已经挂载可见的目录
（如 /scan/media1、/write/target）登记为扫描源，或从扫描源中解绑。

设计要点：
- 持久化到 data/mounts.json（可写数据目录），绝不修改只读的 config.yaml。
- config.yaml 里的 volumes 是"静态卷"，只读，解绑用 disabled 标记实现（不删除配置）。
- 运行时新增的卷是"动态卷"，存 added 列表，可真正删除。
- 所有路径必须在"可绑定根"之内（现有卷/写入目标的父目录 + 显式白名单 + data/logs），
  防止把 /etc 之类的系统目录登记为扫描源。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from .config import Config

STATE_REL = "data/mounts.json"


def state_path(config: Config) -> Path:
    return config.resolve(STATE_REL)


def load_state(config: Config) -> dict[str, Any]:
    """读取绑定状态；文件损坏时按空状态处理，保证应用可用。"""
    p = state_path(config)
    data: dict[str, Any] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    added = [v for v in (data.get("added") or []) if isinstance(v, dict)]
    disabled = [str(x) for x in (data.get("disabled") or [])]
    return {"added": added, "disabled": disabled, "updated_at": data.get("updated_at")}


def save_state(config: Config, state: dict[str, Any]) -> None:
    """原子写入，权限 600（路径白名单状态属于安全敏感数据）。"""
    p = state_path(config)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "added": state.get("added", []),
        "disabled": state.get("disabled", []),
        "updated_at": time.time(),
    }
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".mounts-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        os.chmod(p, 0o600)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def bind_roots(config: Config) -> list[str]:
    """允许绑定为扫描卷的根目录列表（字符串，已 resolve）。"""
    roots: set[str] = set()
    for key in ("volumes", "write_targets"):
        for v in (config.get(key, []) or []):
            if not isinstance(v, dict) or not v.get("path"):
                continue
            rp = Path(str(v["path"])).resolve()
            roots.add(str(rp.parent))  # 允许在同一挂载根下新增兄弟目录
            roots.add(str(rp))
    for p in (config.get("security.allowed_paths", []) or []):
        roots.add(str(Path(str(p)).resolve()))
    roots.add(str(config.resolve("data").resolve()))
    roots.add(str(config.resolve("logs").resolve()))
    return sorted(roots)


def check_bind_path(config: Config, path: str) -> Path:
    """校验路径可绑定：必须绝对、在允许根内。返回归一化路径。"""
    p = Path(str(path)).expanduser()
    if not p.is_absolute():
        raise ValueError("路径必须是绝对路径（容器内路径，例如 /scan/media1）")
    rp = p.resolve()
    roots = [Path(r).resolve() for r in bind_roots(config)]
    if not any(_under(rp, r) for r in roots):
        raise ValueError("路径不在允许挂载根内，允许的根：" + ", ".join(str(r) for r in roots))
    return rp


def effective_volumes(config: Config) -> list[dict[str, Any]]:
    """返回当前生效的扫描卷（静态 - 禁用 + 动态 - 禁用）。扫描器与白名单都用它。"""
    state = load_state(config)
    disabled = set(state["disabled"])
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for v in (config.get("volumes", []) or []):
        if not isinstance(v, dict) or not v.get("name") or not v.get("path"):
            continue
        if v["name"] in disabled or v["name"] in seen:
            continue
        result.append({"name": v["name"], "path": v["path"], "role": v.get("role", "cold"), "source": "static"})
        seen.add(v["name"])
    for v in state["added"]:
        name, path = v.get("name"), v.get("path")
        if not name or not path or name in disabled or name in seen:
            continue
        result.append({"name": name, "path": path, "role": v.get("role", "cold"), "source": "dynamic"})
        seen.add(name)
    return result


def list_volumes(config: Config) -> list[dict[str, Any]]:
    """带存在性/文件数状态的卷清单，供 Web 展示。"""
    state = load_state(config)
    disabled = set(state["disabled"])
    rows: list[dict[str, Any]] = []
    for v in effective_volumes(config):
        p = Path(str(v["path"]))
        try:
            rp = p.resolve()
            exists = rp.exists() and rp.is_dir()
        except OSError:
            rp, exists = p, False
        rows.append({
            "name": v["name"], "path": str(rp), "role": v.get("role", "cold"),
            "source": v.get("source", "static"),
            "exists": exists, "enabled": v["name"] not in disabled,
        })
    # 被禁用的静态卷也要展示，否则用户无法重新启用
    for v in (config.get("volumes", []) or []):
        if isinstance(v, dict) and v.get("name") in disabled and v.get("path"):
            rows.append({
                "name": v["name"], "path": str(Path(str(v["path"])).resolve()),
                "role": v.get("role", "cold"), "source": "static",
                "exists": Path(str(v["path"])).exists(), "enabled": False,
            })
    return rows


def discover_mounts(config: Config) -> list[dict[str, Any]]:
    """探测容器内真实挂载点（/proc/mounts），标注是否已绑定为扫描卷。

    非 Linux（如本机 Windows 测试）无 /proc/mounts 时优雅降级为只列配置卷。
    """
    bound = {v["path"]: v["name"] for v in effective_volumes(config)}
    roots = [Path(r).resolve() for r in bind_roots(config)]
    found: dict[str, dict[str, Any]] = {}

    proc = Path("/proc/mounts")
    if proc.exists():
        try:
            for line in proc.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mp, fstype = parts[0], parts[1], parts[2]
                if not any(_under(Path(mp).resolve(), r) for r in roots):
                    continue
                found[mp] = {"device": device, "mount_point": mp, "fstype": fstype}
        except OSError:
            pass

    # 保证静态/动态卷至少出现（即使 /proc 不可读）
    for row in list_volumes(config):
        mp = row["path"]
        found.setdefault(mp, {"device": "-", "mount_point": mp, "fstype": "-"})

    result: list[dict[str, Any]] = []
    for mp, info in found.items():
        total = used = 0.0
        try:
            usage = shutil.disk_usage(mp)
            total, used = float(usage.total), float(usage.used)
        except OSError:
            pass
        result.append({
            **info,
            "total_bytes": total,
            "used_bytes": used,
            "bound_name": bound.get(mp) or bound.get(str(Path(mp).resolve())),
        })
    result.sort(key=lambda x: x["mount_point"])
    return result


def bind(config: Config, name: str, path: str, role: str = "cold") -> dict[str, Any]:
    """绑定一个扫描卷。name 唯一；路径必须在允许根内且存在。"""
    name = str(name).strip()
    if not name or len(name) > 48 or not all(c.isalnum() or c in "-_" for c in name):
        raise ValueError("名称只能包含字母、数字、横线、下划线，且不超过 48 字符")
    role = str(role) if role in ("hot", "warm", "cold") else "cold"
    rp = check_bind_path(config, path)
    if not rp.exists() or not rp.is_dir():
        raise ValueError("目录不存在或不可访问：" + str(rp))

    state = load_state(config)
    # 重新启用被禁用的卷
    state["disabled"] = [n for n in state["disabled"] if n != name]
    # 若同名动态卷已存在则覆盖；若为静态卷同名，动态卷优先
    state["added"] = [v for v in state["added"] if v.get("name") != name]
    state["added"].append({"name": name, "path": str(rp), "role": role})
    save_state(config, state)
    return {"name": name, "path": str(rp), "role": role, "source": "dynamic", "enabled": True}


def unbind(config: Config, name: str) -> dict[str, Any]:
    """解绑扫描卷：动态卷真删除；静态卷加禁用标记（不修改 config.yaml）。"""
    name = str(name).strip()
    state = load_state(config)
    matched_dynamic = [v for v in state["added"] if v.get("name") == name]
    static_names = {v.get("name") for v in (config.get("volumes", []) or []) if isinstance(v, dict)}
    if not matched_dynamic and name not in static_names:
        raise ValueError("未找到扫描卷：" + name)
    state["added"] = [v for v in state["added"] if v.get("name") != name]
    if name in static_names and name not in state["disabled"]:
        state["disabled"].append(name)
    save_state(config, state)
    return {"name": name, "enabled": False, "source": "dynamic" if matched_dynamic else "static"}
