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
| **应用级白名单** | `allowed_paths` 防路径穿越与软链逃逸，所有越界尝试留审计日志 |
| **安全执行** | 复制 → 校验 → 删除，每步写操作日志，支持一键回滚 |
| **两级回收站** | 源文件先进隔离区（可还原），超时提升待清除区；物理清除必须 `confirm=true` |
| **Agent 友好** | 76 个 MCP 原子工具 + 10 个 WorkBuddy Skill，行为边界写在 `AGENTS.md` |
| **配置驱动、平台无关** | 所有路径通过 `.env` 与 YAML 注入，换 NAS 不用改代码 |
| **定时调度，默认全关** | 5 段 cron 只触发只读任务，危险操作永不自动执行，每次触发写审计日志 |

## 功能

- **增量索引**：扫描已挂载目录，记录路径、大小、mtime/atime、哈希，存入 SQLite
- **三级分类**：归属域（个人/工作/生活/资源）× 类型（15 类 323 种扩展名）× 温度（热/温/冷）
- **文件夹别名**：205 个别名按目录段精确识别（电影/影片/Movies、剧集/电视剧、照片/Photos…），不误伤项目目录
- **重复检测**：大小分组 → 头尾哈希 → 全量 SHA-256 三级漏斗，快速定位重复
- **迁移计划**：生成"应该移动到哪里"的计划表，支持冲突重命名
- **热数据备份**：rsync 把活跃文件同步到指定目标
- **备份审计**：存在性 / 版本轮换 / 陈旧性 / 覆盖冲突四项只读检查
- **清理发现器**：12 类垃圾候选（空文件/临时/.part/种子/陈旧压缩包/重复命名…），只出清单不删除
- **批量归类**：一次按域/类型/温度拆成多个计划，1200 文件实测有进度日志
- **定时调度**：可选 cron 定时跑 scan/classify/dedupe/backup/verify，默认全关且只触发只读任务
- **Web 控制台**：12 个功能页（概览/文件浏览/搜索/重复/虚拟目录/计划/清理/回收站/复核/能力/任务/操作）+ 登录认证 + PWA + 深色模式 + 首启引导
- **CLI**：所有能力都有命令行入口（`schedule` / `recycle` / `plan --batch` 等），方便脚本化

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
| [定时调度（示例配置）](config/schedule.example.yaml) | cron 配置、只读白名单、默认全关说明 |
| [数据库设计](docs/数据库设计.md) | 表结构、索引、查询示例 |
| [AGENTS.md](AGENTS.md) | Agent / MCP / Skill 的行为契约与安全边界 |
| [MCP 工具清单](docs/MCP工具清单.md) | 76 个原子工具、认证模型、JSON-RPC 示例、客户端配置 |
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

## MCP 与 WorkBuddy Skill

**MCP（76 个原子工具，同类最多）**：

```bash
PYTHONPATH=src NAS_ORGANIZER_TOKEN=<Web 登录 Token> python -m nas_organizer.mcp_server
```

- 不设 Token → 只读模式（35 个工具可用），写工具返回"需要授权"
- `plan_execute` / `rollback_last` / `backup_hot` / `recycle_restore` / `recycle_purge` 另需 `confirm=true`
- 工具清单与客户端配置见 [docs/MCP工具清单.md](docs/MCP工具清单.md)

**10 个 WorkBuddy Skill**（1 路由 + 9 专项）：

```bash
bash scripts/install-skills.sh   # 安装到 ~/.workbuddy/skills/
```

`nas-report` / `dedup-finder` / `file-sorter` / `photo-organizer` / `music-organizer` /
`work-organizer` / `download-cleaner` / `backup-auditor` / `migration-review`

安装后可用自然语言驱动：

```
帮我体检一下 NAS，看看哪个盘快满了
找重复文件，先出清单别删
生成迁移计划，先别执行
把工作类电子书单独列出来
```

所有 Agent 行为必须遵守仓库根 [AGENTS.md](AGENTS.md)。

## 项目状态

- [x] 技术方案、数据库设计、分类规则
- [x] 核心模块（扫描/分类/查重/计划/执行/备份）
- [x] 认证与安全（PBKDF2 / 签名 Token / 限流 / 危险操作确认）
- [x] Web 控制台（登录页、统计、重复报告、计划、改密）
- [x] CLI 与 10 个 WorkBuddy Skill
- [x] 通用多平台挂载与 6 个品牌安装文档
- [x] D1：MCP 工具 + 应用级白名单 + Agent policy（[进度计划](docs/进度计划.md)）
- [x] D2：205 文件夹别名 / 定时调度 / 323 种扩展名
- [x] D3：两级回收站 / 12 类专项清理 / 备份审计 / 批量归类（MCP 增至 46）
- [x] D4：反馈学习 / 可选 Ollama 兜底 / exif·ID3 媒体识别 / pHash 相似图片（MCP 增至 54）
- [x] D5：多维虚拟目录 / 断点续传 / CSV 全量 undo / 剧集识别 / 任务监控（MCP 增至 64）
- [x] D6：人工复核台 / 视频关键帧 / rclone 只读索引 / 自然语言意图映射（MCP 增至 72）
- [x] D7：FTS5 全文搜索 / 可选 OCR / GGUF 文档 / PWA（MCP 增至 76，综合评分 4.95）
- [ ] 真机多品牌联调（唯一剩余项：需在你的 NAS 上实际部署验证）

## D4 快速体验

```bash
# 人工纠正（越用越准）：把某文件归入「个人」，抽取关键词供低置信度文件兜底
python -m nas_organizer feedback add "/nas/云账户账单明细.pdf" --field domain --value 个人
python -m nas_organizer feedback list

# 媒体感知（只读建议，不移动文件；Pillow/mutagen 缺失时优雅降级）
python -m nas_organizer media capability
python -m nas_organizer media suggest-photos --target /write/album
python -m nas_organizer media suggest-music --target /write/music
python -m nas_organizer media phash-similar

# 可选 AI 兜底：默认关闭；在 config.yaml 设 ai.enabled=true 后仅处理疑难件
```

AI 未启用时零依赖、行为与 D3 完全一致；断网或无 Ollama 时自动降级为人工复核队列。详见 [进度计划 D4 报告](docs/进度计划.md)。

## D5 快速体验

```bash
# 多维虚拟目录：不移动文件，同一文件多视角归类
python -m nas_organizer view auto            # 按 域/类型/温度/年份 自动打标
python -m nas_organizer view list            # 列出所有视图
python -m nas_organizer view get "域:工作"    # 查看某视图（虚拟，源零改动）

# 断点续传：计划中断后安全重跑（跳过已完成、重校验已复制文件）
python -m nas_organizer execute 7 --resume
python -m nas_organizer tasks                # 任务进度与历史

# CSV 全量 undo：导出操作日志，跨实例生成反向回滚计划
python -m nas_organizer undo export -o undo.csv
python -m nas_organizer undo import undo.csv

# 剧集季数识别（只读建议）
python -m nas_organizer media season-suggest --target /write/series
```

虚拟目录、CSV undo、任务监控均不移动/删除源文件，落地写操作仍走「计划→确认→执行」链路。详见 [进度计划 D5 报告](docs/进度计划.md)。

## D6 快速体验

```bash
# 复核台：低置信度分类集中人工裁决（Web「复核台」tab 或 CLI）
python -m nas_organizer review queue          # 待复核队列
python -m nas_organizer review decide 12 --field domain --value 工作   # 修正+写反馈
python -m nas_organizer review history        # 裁决审计

# 可选外部工具（缺失自动降级，不影响主流程）
python -m nas_organizer remote capability     # 查看 ffmpeg / rclone 是否可用
python -m nas_organizer media video-similar   # FFmpeg 关键帧找相似视频（需 FFmpeg）
python -m nas_organizer remote ls "nas:Media" # rclone lsjson 只读预览（需 rclone）
```

自然语言交互通过 Agent 调用（服务端只接受结构化参数、不接 LLM）：例如"帮我体检 NAS"→ `get_stats`，"找重复先出清单别删"→ `list_duplicates`，"这些文件归到工作"→ `review_queue`/`review_decide`。调用 `intent_schema` 工具可获取完整意图映射表。详见 [进度计划 D6 报告](docs/进度计划.md)。

## D7 快速体验（7 天路线图收官）

```bash
# FTS5 全文搜索：中文子串也能命中（先建索引）
python -m nas_organizer reindex --force
python -m nas_organizer search 财务报表      # 命中"财务报表2024.pdf"
python -m nas_organizer search 报表          # 子串同样命中

# 查看全部可选能力（AI/OCR/FFmpeg/rclone/exif/ID3/pHash）
python -m nas_organizer plugins
```

Web 控制台现支持 PWA：手机浏览器打开后选"添加到主屏幕"即可像 App 一样使用（Service Worker 不缓存任何 `/api/` 数据，安全性不退化）。可选本地模型与 GGUF 加载见 [GGUF与本地模型](docs/GGUF与本地模型.md)。

**七天成果**：76 个 MCP 工具（同类标杆 26）、10 个 Skill（标杆 9）、323 扩展名/205 别名（标杆 255/135）、两级回收站、FTS5 搜索、PWA，综合评分 **4.95/5.00**（D0 4.05）。所有写操作沿用 `dry-run → 确认 → 执行 → 审计 → 回滚`，安全评分始终保持 5。详见 [进度计划全量验收](docs/进度计划.md#13-d7-完成报告--全量验收2026-11-11)。

## 七天之后：持续打磨（Round A–D）

七天路线图闭环后，又按产品经理/架构师视角做了一轮**页面、细节、测试**的系统性打磨：

| 轮次 | 主题 | 关键交付 |
|---|---|---|
| Round A | 能力对齐 | 后端 14 个只读 REST 端点（搜索/清理/回收站/审计/媒体/插件/调度/反馈）；Web 从 5 个 tab 扩到 11 个；统一 loading/empty/error；深色模式 |
| Round B | 体验细节 | 文件浏览 tab（域/类型/温度筛选 + 分页）；首启三步引导；修复运行时审计日志被误提交（`.gitignore` 补 `*.jsonl`） |
| Round D | 吸优收官 | 公开 `/api/health`（Docker healthcheck 改用）；操作审计时间线（执行→校验→回滚可视化） |
| Round C | 质量加固 | 新增 `test_api_routes.py`（25 只读 + 2 确认端点全路由认证边界）与 `test_web.py`（DOM 契约 + XSS 转义 + SW 缓存隔离） |

**验收**：19 个测试文件 / 91 个检查点全通过；`app.js` 通过 `node --check`；本地与远端 `main` 完全同步。

## License

MIT
