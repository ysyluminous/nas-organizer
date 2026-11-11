---
name: nas-organizer
description: NAS 数据治理工具集的总入口与路由。根据意图选择 9 个专项 Skill（nas-report / dedup-finder / file-sorter / photo-organizer / music-organizer / work-organizer / download-cleaner / backup-auditor / migration-review）。当用户提到 NAS 文件整理、查重、归类、备份审计、迁移计划，但尚未明确具体任务时使用。
---

# NAS Organizer（路由入口）

本 Skill 是**路由器**，具体任务请加载下方专项 Skill。所有行为必须遵守仓库根目录 `AGENTS.md`。

## 专项 Skill 路由表

| 用户意图 | 加载 Skill |
|---|---|
| 看看 NAS 里有什么、磁盘快满了、数据体检、不知道从哪开始 | `nas-report` |
| 找重复、清理重复、释放空间 | `dedup-finder` |
| 按类型/归属/冷热归类、整理文件堆 | `file-sorter` |
| 整理照片、截图、按时间归档图片 | `photo-organizer` |
| 整理音乐、无损音乐、专辑散落 | `music-organizer` |
| 工作文件、合同报表、项目资料归档 | `work-organizer` |
| 下载目录乱、安装包/种子/未完成下载 | `download-cleaner` |
| 备份是否健康、冷数据完整性、最近改动 | `backup-auditor` |
| 检查迁移计划、能不能执行、回滚 | `migration-review` |

## 两种调用方式

### 方式 A：MCP（推荐，跨客户端）

在 Claude / Cursor / Cherry Studio / WorkBuddy 中配置 MCP server：

```bash
# 命令
python -m nas_organizer.mcp_server   # 需设置 PYTHONPATH=src
# 或容器中
docker compose run --rm -e NAS_ORGANIZER_TOKEN=<token> nas-organizer python -m nas_organizer.mcp_server
```

- 只读工具（含 cleanup_discover / backup_audit / recycle_list 等）：无需 Token
- 写工具（scan/classify/dedupe/plan_create/plan_batch/execute/rollback/backup/recycle_*）：需 `NAS_ORGANIZER_TOKEN`
- 危险操作（execute/rollback/backup/recycle_restore/recycle_purge）额外需要 `confirm=true`
- 共 46 个原子工具，完整清单见 `docs/MCP工具清单.md`

### 方式 B：CLI（本机或容器）

```bash
PYTHONPATH=src python -m nas_organizer.cli init
PYTHONPATH=src python -m nas_organizer.cli scan
PYTHONPATH=src python -m nas_organizer.cli classify
PYTHONPATH=src python -m nas_organizer.cli dedupe
PYTHONPATH=src python -m nas_organizer.cli plan
PYTHONPATH=src python -m nas_organizer.cli show-plan <ID>
PYTHONPATH=src python -m nas_organizer.cli execute <ID> --limit 10
PYTHONPATH=src python -m nas_organizer.cli rollback --limit 10
```

## 通用环境说明

- 项目可在任意支持 Docker 的 NAS / x86 / ARM64 主机运行，路径全部由 `.env` 与 `config.yaml` 注入
- `.env` 写**宿主机路径**，`config.yaml` 写**容器内路径**（如 `/scan/media1`、`/write/target`）
- 不同品牌的宿主机路径不同，见 `docs/安装/` 下对应文档
- Web 控制台默认 `http://NAS_IP:8765`，需登录；**不要映射到公网**

## 不可逾越的规则

1. 默认 dry-run；`execute` / `rollback` 必须用户明确确认并带 `confirm=true`
2. 不读取或输出 `auth.yaml`、`.auth_secret`、`INITIAL_PASSWORD.txt`、Token
3. 所有路径必须在 `security.allowed_paths` 内；越界立即停止
4. 不物理删除文件；走回收站 + `op_logs` + 可回滚
5. 分类不准时改 YAML 规则，不改代码；低置信度件进人工复核

## 文档

- 行为契约：`AGENTS.md`
- 同类对比与追赶计划：`docs/同类工具对比.md`、`docs/进度计划.md`
- 安全：`docs/安全与认证.md`
- 挂载与权限：`docs/挂载配置指南.md`
- 安装：`docs/安装指南.md` 与 `docs/安装/`
