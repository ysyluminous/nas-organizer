---
name: file-sorter
description: NAS 混合文件堆的通用归类。按归属域（个人/工作/生活/资源）× 文件类型（音乐/视频/图书/文档/图片/软件/压缩包）× 温度层（热/温/冷）生成 old→new 迁移计划，规则优先、低置信度进人工复核。当用户说"整理文件堆""按类型归类""把视频和文档分开""冷热分层"时使用。
---

# 通用文件归类

## 判定优先级（规则优先，模型兜底）

1. 路径规则 → 2. 扩展名 → 3. 文件名关键词 → 4. 轻量模型（默认关闭）

**不要试图用模型替代规则**。分类不准时改 `config/domain_rules.yaml` 与 `config/type_rules.yaml`，不改代码。

## 调用链

1. `get_stats_by_domain` / `get_stats_by_type` / `get_stats_by_temperature` — 现状
2. `find_low_confidence(threshold=0.8)` — 列出疑难件，先让用户裁决
3. `plan_create(domain=, ftype=, temp=, limit=)` — 按筛选生成计划
4. `preview_plan(plan_id, limit=30)` — 展示 old→new 样例
5. 用户确认 → `plan_execute(plan_id, confirm=true)`
6. 复查 `get_stats` 与 `list_op_logs`

## 目标路径

`{write_target}/{volume}/{domain}/{ftype}/{temp}/{filename}`
冲突时按时间戳重命名，**不覆盖**。

## 边界

- 默认 dry-run：只出计划不执行是合法结果
- 低置信度（<0.8）不得自动迁移
- 不拆散已有的项目目录（如含多个文件的项目文件夹）
- 不处理系统目录：`#recycle`、`@eaDir`、`.git`、`node_modules`
