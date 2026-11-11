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

echo "[entrypoint] 应用进程以 UID=$PUID GID=$PGID 运行"
exec gosu "$PUID:$PGID" python -m nas_organizer.cli serve "$@"
