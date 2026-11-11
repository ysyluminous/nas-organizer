# NAS Organizer

> 一个跑在 Docker 里的 NAS 数据治理工具：**索引 → 分类 → 查重 → 计划 → 安全执行**。
> 支持群晖、威联通、铁威马、飞牛、绿联及任意 x86 / ARM64 Docker 环境，不绑定任何品牌与盘符。

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## 它解决什么问题

家用/小团队 NAS 用久了，通常会出现：

- 文件堆在"待整理"目录里，几万甚至十几万个文件无从下手
- 同一份电影、安装包、电子书散落在多个盘，占满空间
- 不知道哪些文件最近还在用，哪些一年没碰过
- 想按"个人 / 工作 / 生活"和"音乐 / 视频 / 图书 / 文档"重新归档，但手动搬不动
- 自带的文件管理只够"看"，没有索引、规则引擎和批量治理能力

NAS Organizer 把这些事变成一条**可审计、可回滚、需登录**的流水线。

## 设计原则

| 原则 | 说明 |
|---|---|
| **只产出计划，不擅自改文件** | 默认 dry-run，移动/删除必须在 Web 控制台确认后执行 |
| **规则优先，模型兜底** | 90% 分类由路径规则 + 扩展名 + 关键词完成；轻量模型只处理疑难件 |
| **只读扫描 + 受控写入** | 扫描挂载点 `:ro`，只有执行器持有 `:rw` 目录 |
| **认证保护** | 全站 API 需登录；PBKDF2 密码哈希、签名 Token、登录限流 |
| **安全执行** | 复制 → 校验 → 删除，每步写操作日志，支持一键回滚 |
| **配置驱动、平台无关** | 所有路径通过 `.env` 与 YAML 注入，换 NAS 不用改代码 |

## 功能

- **增量索引**：扫描已挂载目录，记录路径、大小、mtime/atime、哈希，存入 SQLite
- **三级分类**：归属域（个人/工作/生活/资源）× 类型（音乐/视频/图书/文档/图片/软件/压缩包）× 温度（热/温/冷）
- **重复检测**：大小分组 → 头尾哈希 → 全量 SHA-256 三级漏斗，快速定位重复
- **迁移计划**：生成"应该移动到哪里"的计划表，支持冲突重命名
- **热数据备份**：rsync 把活跃文件同步到指定目标
- **Web 控制台**：登录认证、统计概览、重复报告、计划预览、批量确认、操作日志与回滚
- **CLI**：所有能力都有命令行入口，方便脚本化与定时调度

## 快速开始（三步）

### 1. 准备目录与配置

```bash
git clone https://github.com/ysyluminous/nas-organizer.git
cd nas-organizer

cp .env.example .env
cp config/config.example.yaml config/config.yaml
```

编辑两个文件：

- `.env`：填你的**宿主机路径**、端口、UID/GID
- `config/config.yaml`：填对应的**容器内路径**（如 `/scan/media1`）

### 2. 启动

```bash
docker compose up -d --build
docker logs -f nas-organizer
```

首次启动日志会打印**随机管理员密码**，也可查看 `data/INITIAL_PASSWORD.txt`。

### 3. 登录体检

浏览器打开 `http://NAS的IP:8765`（若绑定 `127.0.0.1`，见 [安装指南](docs/安装指南.md)），登录后立即在「安全设置」改密。

按顺序执行：`扫描建索引` → `重新分类` → `重复检测`，然后到「概览」看报告。
这三步全部只读，**不会改动你的任何文件**。确认报告合理后再生成并执行迁移计划。

## 文档导航

| 文档 | 内容 |
|---|---|
| [安装指南（总览）](docs/安装指南.md) | 支持平台、通用四步、验证与卸载 |
| → [群晖](docs/安装/群晖.md) · [威联通](docs/安装/威联通.md) · [铁威马](docs/安装/铁威马.md) | 各品牌 Docker 套件操作 |
| → [飞牛](docs/安装/飞牛.md) · [绿联](docs/安装/绿联.md) · [通用 Docker](docs/安装/通用Docker.md) | 包括 Linux / Windows / macOS / ARM |
| [挂载配置指南](docs/挂载配置指南.md) | 三类挂载、路径映射、PUID/PGID、权限排错 |
| [安全与认证](docs/安全与认证.md) | 密码机制、Token、限流、部署安全清单、应急响应 |
| [技术方案](docs/技术方案.md) | 架构、模块、认证设计、分类模型、执行流程 |
| [分类规则](docs/分类规则.md) | 规则表、判定优先级、置信度 |
| [数据库设计](docs/数据库设计.md) | 表结构、索引、查询示例 |
| [同类工具对比](docs/同类工具对比.md) | 与 7 个同类项目的加权评分对比：我们领先在哪、落后在哪 |
| [进度计划](docs/进度计划.md) | 42 项同类优点按 ROI 排序 + 7 天超越路线与可验证验收清单 |
| [案例：绿联 NAS 现状诊断](docs/案例-绿联NAS现状诊断.md) | 作者真实环境的摸底数据与分层方案（仅供参考，非部署前提） |

## 安全须知（务必阅读）

1. **不要把 8765 端口映射到公网**。默认只绑定 `127.0.0.1`；局域网访问请改为 NAS 内网 IP，远程访问请用 VPN 或反代 + HTTPS。
2. **扫描目录一律 `:ro` 只读挂载**，只给执行器单独挂一个具体的 `:rw` 目录。
3. **首次登录后立即修改随机密码**，修改会踢掉所有旧会话。
4. **非 root 运行**：用 `PUID`/`PGID` 映射 NAS 上拥有目录权限的账号。
5. **保护 `data/` 目录**：内含密码哈希与 Token 签名密钥，不要共享或提交 Git。

完整清单见 [安全与认证](docs/安全与认证.md)。

## 本机开发（不跑 Docker）

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
export PYTHONPATH=src

python -m nas_organizer.cli init
python -m nas_organizer.cli scan
python -m nas_organizer.cli classify
python -m nas_organizer.cli dedupe
python -m nas_organizer.cli plan
python -m nas_organizer.cli serve
```

CLI 不需要登录认证（依赖宿主机权限），Web 控制台始终需要登录。

## WorkBuddy Skill

`skills/nas-organizer/SKILL.md` 是配套的 WorkBuddy 技能，安装后可用自然语言驱动：

```
帮我扫描一下 NAS 上的重复文件
生成迁移计划，先别执行
看看分类结果，把工作类电子书单独列出来
```

安装方式：把 `skills/nas-organizer` 目录复制到 `~/.workbuddy/skills/` 下。

## 项目状态

- [x] 技术方案、数据库设计、分类规则
- [x] 核心模块（扫描/分类/查重/计划/执行/备份）
- [x] 认证与安全（PBKDF2 / 签名 Token / 限流 / 危险操作确认）
- [x] Web 控制台（登录页、统计、重复报告、计划、改密）
- [x] CLI 与 WorkBuddy Skill
- [x] 通用多平台挂载与 6 个品牌安装文档
- [ ] 模型兜底（fastText 训练与集成）
- [ ] 真机多品牌联调

## License

MIT
