---
name: nas-organizer
description: 通过 nas-organizer CLI 或 Web API 管理 NAS 数据：增量扫描建索引、按个人/工作/生活与音乐/视频/图书/文档分类、重复文件检测、生成迁移计划并安全执行、热数据备份与回滚。支持任意品牌 NAS 与 Docker 环境。当用户要整理 NAS 文件、查重复、归档分类、生成迁移计划时使用。
---

# NAS Organizer Skill

管理部署在 NAS / Docker 上的 `nas-organizer` 数据治理工具。所有操作通过项目 CLI 或 Web API，禁止直接操作 NAS 文件。

## 环境

- 项目路径：`D:\code\nas-organizer`（本机默认路径，可按实际调整）
- Python 入口：`python -m nas_organizer.cli`，需设置 `PYTHONPATH=src`（Windows Git Bash 下用 `PYTHONPATH=src python -m ...`）
- 配置文件：`config/config.yaml`（不存在时用 `config/config.example.yaml`）
- Web 控制台：默认 `http://NAS_IP:8765`，**需登录认证**；初始随机密码见容器日志或 `data/INITIAL_PASSWORD.txt`
- 路径说明：`.env` 为宿主机路径，`config.yaml` 为容器内 `/scan/*`、`/write/*` 路径；不同 NAS 品牌的宿主机路径不同

## 严格规则

1. **绝不自动执行写操作**：扫描、分类、查重、生成计划都是安全的；`execute` / `rollback` 必须用户明确确认后才能执行
2. **先计划后执行**：任何迁移都必须先 `plan` 生成计划、`show-plan` 预览，用户确认后才 `execute`
3. **无 RAID 环境默认**：许多 NAS 使用单盘 basic 池，写操作必须走"复制→校验→删除"，工具已内置，不要绕过
4. **不碰系统目录**：`#recycle`、`@eaDir`、`#recycle`、`node_modules`、`.git` 以及容器数据目录不要扫描或移动
5. **报告看结果字段**：执行后报告 `done` / `failed` 数，不要只看退出码
6. **Web 写操作需确认**：通过 API 调用 `execute` / `rollback` 时必须带 `confirm=true`，且已取得用户确认
7. **不处理密码与 Token**：不要读取、输出或存储 `data/auth.yaml`、`data/.auth_secret`、登录 Token

## 工作流

### 1. 首次初始化

```bash
cd /d/code/nas-organizer
PYTHONPATH=src python -m nas_organizer.cli init
```

### 2. 扫描建索引

```bash
PYTHONPATH=src python -m nas_organizer.cli scan
```

首次全量扫描耗时取决于文件量（十万级文件约 2-5 分钟，视磁盘性能）。之后增量扫描。

### 3. 分类

```bash
PYTHONPATH=src python -m nas_organizer.cli classify
PYTHONPATH=src python -m nas_organizer.cli stats
```

分类维度：归属域（个人/工作/生活/资源）× 类型（音乐/视频/图书/文档/图片/软件/压缩包/其他）× 温度（热/温/冷）。

### 4. 查重

```bash
PYTHONPATH=src python -m nas_organizer.cli dedupe
```

三级哈希漏斗：大小分组 → 头尾哈希 → 全量哈希。重复文件标记为"可删除候选"，不直接删除。

### 5. 生成计划（安全，可随便做）

```bash
# 全部
PYTHONPATH=src python -m nas_organizer.cli plan

# 按条件筛选
PYTHONPATH=src python -m nas_organizer.cli plan --domain 生活 --ftype 视频
PYTHONPATH=src python -m nas_organizer.cli plan --temp 冷 --limit 100
PYTHONPATH=src python -m nas_organizer.cli plan --duplicates

# 预览
PYTHONPATH=src python -m nas_organizer.cli show-plan <计划ID>
```

### 6. 执行（需用户明确确认）

```bash
# 先向用户展示计划预览，得到确认后
PYTHONPATH=src python -m nas_organizer.cli execute <计划ID> --limit 10
```

执行后报告成功/失败数。如有问题：

```bash
PYTHONPATH=src python -m nas_organizer.cli rollback --limit 10
```

### 7. 备份与校验

```bash
PYTHONPATH=src python -m nas_organizer.cli backup   # 热数据备份
PYTHONPATH=src python -m nas_organizer.cli verify   # 冷数据校验
```

### 8. Web 控制台

```bash
PYTHONPATH=src python -m nas_organizer.cli serve
```

浏览器访问 `http://NAS_IP:8765`，登录后可见概览、重复文件、迁移计划、操作四个页签。

## 常用查询（SQLite）

数据库位置：`data/nas_organizer.db`

```bash
# 各卷文件数
PYTHONPATH=src python -c "
from nas_organizer.config import Config; from nas_organizer.db import Database
c=Config.load(); d=Database(c.resolve(c.get('database.path')))
for r in d.connect().execute('SELECT volume,COUNT(*) n,SUM(size)/1073741824 gb FROM files WHERE status=\"active\" GROUP BY volume'): print(dict(r))
"

# 重复组
PYTHONPATH=src python -c "
from nas_organizer.config import Config; from nas_organizer.db import Database
c=Config.load(); d=Database(c.resolve(c.get('database.path')))
for r in d.connect().execute('SELECT full_hash,COUNT(*) n,SUM(size)/1073741824 gb FROM files WHERE full_hash IS NOT NULL GROUP BY full_hash HAVING n>1 ORDER BY gb DESC LIMIT 20'): print(dict(r))
"
```

## 规则配置

- 类型规则：`config/type_rules.yaml`（扩展名 → 类型）
- 归属规则：`config/domain_rules.yaml`（关键词、路径前缀）
- 温度规则：`config/temperature_rules.yaml`（时间、大小阈值）

分类不准时优先补充这些规则文件，而不是改代码。低置信度文件在 Web 控制台人工指定。

## 文档参考

- 安装：`docs/安装指南.md` 与 `docs/安装/` 下的品牌文档
- 挂载与权限：`docs/挂载配置指南.md`
- 安全：`docs/安全与认证.md`
- 案例（作者个人环境，仅供参考）：`docs/案例-绿联NAS现状诊断.md`

## 故障处理

| 现象 | 处理 |
|---|---|
| 扫描报权限错误 | 检查容器只读挂载是否正确，PUID/PGID 是否与宿主机目录属主一致 |
| 扫描 0 文件 | 确认 `config.yaml` 用容器内路径 `/scan/...`，不是宿主机路径 |
| 分类结果大量"其他/资源" | 补充 `domain_rules.yaml` 的关键词和路径规则 |
| 执行失败 | 查看 `plan_items.error`；已执行的可 `rollback` |
| 数据库锁定 | 停止并发 CLI 进程，SQLite 是单写者 |
| Web 打不开 | 检查 `docker compose ps`、监听 IP/端口、防火墙；401 说明需要先登录 |
