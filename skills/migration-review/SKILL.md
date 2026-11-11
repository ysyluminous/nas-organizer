---
name: migration-review
description: NAS 迁移计划的人工复核关卡。预览 old→new 计划、逐条检查源与目标、核对审计日志，确认后才允许执行或回滚；发现异常可拒绝执行。当用户说"看看计划""这个计划能执行吗""检查迁移去向""回滚上一次"时使用。
---

# 迁移计划复核

这是**人工关卡 Skill**：核心职责是阻止错误计划被执行。

## 调用链

1. `list_plans(limit=50)` — 列出计划与状态
2. `get_plan(plan_id)` / `preview_plan(plan_id, limit=30)` — 查看明细
3. `list_plan_items(plan_id, status=failed)` — 检查是否有失败项
4. `security_check_path` — 抽样核对 src / dst 是否在白名单内
5. `list_op_logs` — 避免与近期变更冲突

## 复核清单（逐项打勾）

- [ ] 源路径都在允许的扫描卷内
- [ ] 目标路径都在允许的写入目标内
- [ ] 没有把工作/个人不可再生数据标为"重复候选"
- [ ] 目标无覆盖风险（冲突策略为 rename）
- [ ] 条目数与用户预期一致
- [ ] 已展示至少 3 条 old→new 样例给用户

## 执行与回滚

只有全部通过且用户明确确认后：

```
plan_execute(plan_id, confirm=true)
rollback_last(confirm=true)   # 仅在用户要求撤销时
```

## 边界

- 任一清单项不通过 → **拒绝执行**，说明原因，不"先执行再说"
- 不修改计划明细（没有此类工具，也不应绕过）
- 不删除 op_logs
- 执行后必须报告 done/failed/skipped 与回滚方式
