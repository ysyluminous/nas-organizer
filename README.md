# NAS Organizer

> 一个跑在绿联 NAS Docker 上的数据整理与治理工具：自动索引、智能分类、重复检测、安全迁移、热数据备份。

## 它解决什么问题

家用/小团队 NAS 用久了，通常会出现这几个问题：

- 文件堆在 `待整理` 目录里，几万甚至十几万个文件无从下手
- 同一份电影、安装包、电子书散落在多个盘，占满空间
- 不知道哪些文件最近还在用，哪些一年没碰过
- 想按"个人 / 工作 / 生活"和"音乐 / 视频 / 图书 / 文档"重新归档，但手动搬不动
- 绿联自带的文件管理只有浏览和传输，没有索引、规则和批量治理能力

NAS Organizer 把这些事变成一条可审计、可回滚的流水线。

## 设计原则

| 原则 | 说明 |
|---|---|
| **只产出计划，不擅自改文件** | 默认 dry-run，所有移动/删除都要在 Web 控制台确认后才执行 |
| **规则优先，模型兜底** | 90% 的分类由路径规则 + 扩展名 + 关键词完成，轻量模型只处理疑难件 |
| **安全执行** | 复制 → 校验 → 删除，每步写操作日志，支持一键回滚 |
| **只读扫描 + 受控写入** | 扫描挂载点和执行挂载点分离，分类引擎本身无写权限 |

## 功能

- **增量索引**：扫描全部共享文件夹，记录路径、大小、mtime/atime、哈希，存进 SQLite
- **三级分类**：归属域（个人/工作/生活/资源）× 类型（音乐/视频/图书/文档/图片/软件/压缩包）× 温度（热/温/冷）
- **重复检测**：大小分组 → 头尾哈希 → 全量哈希三级漏斗，快速定位重复文件
- **迁移计划**：根据分类结果生成"应该移动到哪里"的计划表，支持冲突重命名
- **热数据备份**：rsync 把最近 90 天活跃文件备份到指定卷
- **Web 控制台**：预览计划、批量确认、查看操作日志、一键回滚
- **CLI**：所有能力都有命令行入口，方便脚本化

## 快速开始

### 1. 在 NAS 上部署（Docker Compose）

```yaml
services:
  nas-organizer:
    image: nas-organizer:latest
    container_name: nas-organizer
    restart: unless-stopped
    ports:
      - "8765:8765"
    volumes:
      # 只读挂载：扫描引擎用
      - /volume1/8T1:/nas/8T1:ro
      - /volume2/16t1:/nas/16t1:ro
      - /volume3/8t2:/nas/8t2:ro
      - /volume5/16t2:/nas/16t2:ro
      # 读写挂载：仅执行器用（移动/备份）
      - /volume5/16t2:/write/16t2:rw
      - /volume4/1T-ssd:/write/ssd:rw
      # 配置与数据
      - ./config:/app/config:ro
      - ./data:/app/data:rw
      - ./logs:/app/logs:rw
    environment:
      - TZ=Asia/Shanghai
```

### 2. 本机开发

```bash
pip install -r requirements.txt
cp config/config.example.yaml config/config.yaml
python -m nas_organizer.cli scan          # 扫描并建索引
python -m nas_organizer.cli classify      # 分类
python -m nas_organizer.cli dedupe        # 查重
python -m nas_organizer.cli plan          # 生成迁移计划
python -m nas_organizer.cli serve         # 启动 Web 控制台
```

## 文档

- [技术方案](docs/技术方案.md) — 架构、模块、数据库表设计
- [分类规则](docs/分类规则.md) — 规则表、判定优先级、置信度
- [部署指南](docs/部署指南.md) — NAS 部署、挂载、备份策略
- [数据库设计](docs/数据库设计.md) — 表结构、索引、查询示例

## WorkBuddy Skill

`skills/nas-organizer/SKILL.md` 是配套的 WorkBuddy 技能，安装后可以直接用自然语言驱动：

```
帮我扫描一下 NAS 上的重复文件
把 16t1/待整理 里的电子书归类到 volume5
生成一个迁移计划，先别执行
```

安装方式：把 `skills/nas-organizer` 目录复制到 `~/.workbuddy/skills/` 下。

## 项目状态

- [x] 技术方案与目录结构设计
- [x] 核心模块（配置、数据库、扫描器、分类器、查重、计划、执行器）
- [x] FastAPI Web 控制台
- [x] CLI 入口
- [x] WorkBuddy Skill
- [ ] 模型兜底（fastText 训练与集成）
- [ ] 真机 NAS 联调

## License

MIT
