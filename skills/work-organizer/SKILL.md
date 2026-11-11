---
name: work-organizer
description: NAS 工作文件整理。针对合同、报表、项目资料等，按归属域=工作生成迁移计划，优先保守处理：不移动正在使用的文件、不拆散项目目录、低置信度一律进复核。当用户说"整理工作文件""合同和报表在哪""项目资料归档"时使用。
---

# 工作文件整理

工作文件通常不可再生，是本工具链里**最需要保守**的一类。

## 调用链

1. `count_files(domain=工作)` 与 `list_files(domain=工作, limit=50)` — 先看规模与样例
2. `find_low_confidence(threshold=0.8)` — 工作类误判代价高，疑难件先人工裁决
3. `find_old_files(days=365)` — 识别可归档的陈旧资料
4. `plan_create(domain=工作, temp=冷, limit=N)` — 只归档明确陈旧的，**不碰近期文件**
5. `preview_plan` → 逐条核对 src/dst → 用户确认 → `plan_execute(confirm=true)`
6. 执行后 `list_op_logs` 核对，必要时 `rollback_last(confirm=true)`

## 边界（比通用 Skill 更严）

- 不使用大 limit 批量操作；单次建议 ≤50 条
- 热数据（近 90 天修改）默认不迁移
- 含多个文件的项目目录保持完整，不拆散
- 不把工作文件标为"重复候选"后直接清理——同名不同版本可能是有意保留
- 任何路径越界立即停止，不尝试换路径

## 报告

`工作文件数 / 冷热分布 / 本次迁移数 / 抽样 old→new / 低置信度件数 / 回滚方式`
