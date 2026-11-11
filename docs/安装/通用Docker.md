# 通用 Docker 安装指南（Linux / x86 主机 / Windows / macOS）

适用于任何能运行 Docker 的环境：Debian/Ubuntu/CentOS 服务器、迷你主机、旧电脑、unRAID、OpenMediaVault、Windows/macOS 开发机，以及以 NFS/CIFS 挂载 NAS 共享文件夹的第三方主机。

## 一、Linux 服务器 / 迷你主机（推荐）

### 1. 安装 Docker

```bash
# Debian / Ubuntu
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# 重新登录后生效
```

### 2. 获取代码

```bash
sudo mkdir -p /opt/nas-organizer
sudo chown $USER:$USER /opt/nas-organizer
cd /opt/nas-organizer

git clone https://github.com/ysyluminous/nas-organizer.git .
cp .env.example .env
cp config/config.example.yaml config/config.yaml
```

### 3. 挂载 NAS 共享文件夹（如果程序跑在 NAS 之外的主机）

以 NFS 为例：

```bash
sudo mkdir -p /mnt/nas/media /mnt/nas/target
sudo mount -t nfs 192.168.1.10:/volume1/media /mnt/nas/media
sudo mount -t nfs 192.168.1.10:/volume1/target /mnt/nas/target
```

> 只读挂载更安全：`sudo mount -t nfs -o ro 192.168.1.10:/volume1/media /mnt/nas/media`

用 CIFS/SMB 则安装 `cifs-utils` 后用 `mount -t cifs`。

### 4. 配置与启动

编辑 `.env`：

```env
LISTEN_IP=127.0.0.1
WEB_PORT=8765
PUID=1000
PGID=1000
SCAN_1=/mnt/nas/media
WRITE_TARGET=/mnt/nas/target
```

编辑 `config/config.yaml`：`volumes[].path` 填 `/scan/media1`，`write_targets[].path` 填 `/write/target`。

```bash
docker compose up -d --build
docker logs -f nas-organizer
```

日志中的随机密码用于首次登录 `http://127.0.0.1:8765`。

### 5. 开机自启

`restart: unless-stopped` 已配置，Docker 服务设为开机启动即可：

```bash
sudo systemctl enable docker
```

## 二、Windows / macOS（Docker Desktop）

1. 安装 Docker Desktop 并启动
2. 在任意目录（如 `D:\code\nas-organizer`）克隆仓库
3. 复制 `.env.example` 为 `.env`，`config/config.example.yaml` 为 `config/config.yaml`
4. 路径写法：

```env
# Windows（Git Bash / Docker Desktop）
SCAN_1=/d/共享文件夹/Media
WRITE_TARGET=/d/共享文件夹/target

# macOS
SCAN_1=/Users/你的用户名/Media
```

5. 在 PowerShell / Terminal 中：

```bash
cd D:\code\nas-organizer
docker compose up -d --build
```

6. 浏览器访问 `http://localhost:8765`

> Windows 上 `PUID/PGID` 保持默认 1000 即可；若挂载 WSL2 中的目录出现权限问题，可在 WSL 中执行 `id` 确认。

## 三、ARM64 设备

镜像基础 `python:3.11-slim` 提供 ARM64 版本，构建时会自动选择匹配架构：

```bash
docker compose up -d --build
```

若在 x86 主机上为 ARM 设备交叉构建，需加 `--platform linux/arm64`，并使用 buildx。一般用户直接在目标设备上构建即可。

## 四、只有 Docker CLI、没有 compose 插件

可以把 compose 转成等效的 `docker run`（示例）：

```bash
docker run -d \
  --name nas-organizer \
  --restart unless-stopped \
  -p 127.0.0.1:8765:8765 \
  -e TZ=Asia/Shanghai \
  -e PUID=1000 \
  -e PGID=1000 \
  -v /mnt/nas/media:/scan/media1:ro \
  -v /mnt/nas/target:/write/target:rw \
  -v /opt/nas-organizer/config:/app/config:ro \
  -v /opt/nas-organizer/data:/app/data:rw \
  -v /opt/nas-organizer/logs:/app/logs:rw \
  nas-organizer:latest
```

先手动构建镜像：

```bash
docker build -t nas-organizer:latest /opt/nas-organizer
```

注意：上面的 `docker run` 示例没有 `.env` 变量展开，路径是硬编码示意，请替换成自己的路径。

## 五、验证

```bash
curl http://127.0.0.1:8765/api/auth/status
# {"auth_required":true,"password_set":true}

docker exec nas-organizer id
docker exec nas-organizer ls -ld /scan/media1
```

## 六、把 NAS 数据交给外部主机处理时的安全提醒

- NAS 的 NFS 导出建议限制来源 IP 为运行本容器的主机
- 用 CIFS 时在 NAS 上建一个**只读账号**专门用于扫描
- 外部主机与 NAS 之间若经过不可信网络，应使用 VPN 或专线
- 仍然遵守：扫描源只读挂载、写入目标严格限定到单个目录
