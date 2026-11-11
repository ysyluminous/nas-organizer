#!/bin/sh
# 容器入口：以最小权限启动应用进程
# 只在初始化阶段使用 root，随后通过 gosu 降权到 PUID/PGID 指定的用户
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# 让内置 app 用户的 UID/GID 与宿主机共享文件夹的属主一致。
# 若目标 UID/GID 已被镜像内其他用户占用，忽略错误并直接用数字 ID 降权。
if [ "$PGID" != "1000" ]; then
  groupmod -g "$PGID" app 2>/dev/null || true
fi
if [ "$PUID" != "1000" ]; then
  usermod -u "$PUID" app 2>/dev/null || true
fi

# 应用私有数据目录必须可写（挂载自宿主机的 config 目录为只读，不在此处修改）
mkdir -p /app/data /app/logs
chown -R "$PUID:$PGID" /app/data /app/logs 2>/dev/null || true

# ---------------------------------------------------------------------------
# 挂载目录权限自检（只提示、不擅自 chown 用户数据）
# 逐个检查通过 MOUNT_CHECK_PATHS 传入的目录：属主 UID/GID 与 PUID/PGID 不符
# 或缺少读权限时，输出可直接在宿主机复制执行的修复命令。
# ---------------------------------------------------------------------------
check_mount_permissions() {
  # 逗号分隔，例如 "/scan,/write,/backup"
  targets="${MOUNT_CHECK_PATHS:-}"
  [ -z "$targets" ] && return 0

  old_ifs="$IFS"
  IFS=","
  for target in $targets; do
    IFS="$old_ifs"
    target=$(printf '%s' "$target" | xargs 2>/dev/null || printf '%s' "$target")
    [ -z "$target" ] && { IFS=","; continue; }
    if [ ! -d "$target" ]; then
      echo "[entrypoint][WARN] 挂载目录不存在或不可访问: $target"
      echo "[entrypoint][WARN] 请核对 docker-compose 的 volumes 映射（容器内路径）。"
      IFS=","; continue
    fi

    owner_uid=$(stat -c '%u' "$target" 2>/dev/null || echo "?")
    owner_gid=$(stat -c '%g' "$target" 2>/dev/null || echo "?")
    perms=$(stat -c '%a' "$target" 2>/dev/null || echo "?")

    if [ "$owner_uid" != "?" ] && { [ "$owner_uid" != "$PUID" ] || [ "$owner_gid" != "$PGID" ]; }; then
      echo "---------------------------------------------------------------"
      echo "[entrypoint][WARN] 权限不匹配: $target"
      echo "[entrypoint][WARN]   目录属主 UID:GID = $owner_uid:$owner_gid ($perms)"
      echo "[entrypoint][WARN]   期望进程 UID:GID = $PUID:$PGID"
      echo "[entrypoint][WARN]   扫描目录可能需要读权限；写入目录必须可写。"
      echo "[entrypoint][WARN] 请在【宿主机】按需执行以下任一修复方式："
      echo "[entrypoint][WARN]   A. 推荐：改 PUID/PGID 为实际属主 $owner_uid:$owner_gid 后重建容器"
      echo "[entrypoint][WARN]   B. 授权写入目录：sudo chown -R $owner_uid:$owner_gid <宿主机共享目录>"
      echo "[entrypoint][WARN]      或 sudo chmod -R a+rwX <宿主机共享目录>"
      echo "[entrypoint][WARN] 注意：本容器不会自动 chown 你的用户数据，以免破坏既有权限。"
      echo "---------------------------------------------------------------"
    else
      echo "[entrypoint][OK] $target 属主匹配 (UID=$PUID GID=$PGID, mode=$perms)"
    fi
    IFS=","
  done
  IFS="$old_ifs"
}
check_mount_permissions

echo "[entrypoint] 应用进程以 UID=$PUID GID=$PGID 运行"
exec gosu "$PUID:$PGID" python -m nas_organizer.cli serve "$@"
