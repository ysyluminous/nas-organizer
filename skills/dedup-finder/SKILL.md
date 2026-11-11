---
name: dedup-finder
description: NAS 重复文件发现与清理计划。用 nas-organizer 的三级哈希漏斗（大小→头尾哈希→全量 SHA-256）找出重复组、计算可释放空间、生成待删除候选计划，经用户确认后安全执行。当用户说"找重复文件""哪些文件重复了""清理重复""释放空间"时使用。
---

# 重复文件发现

发现阶段只读；生成计划写数据库但不碰文件；执行必须用户确认。

## 调用链

1. `dedupe_start` — 运行查重（需 Token；只读扫描，不删文件）
2. `get_duplicate_summary` — 重复组数与浪费空间
3. `list_duplicates(limit=50)` — 按浪费空间降序的重复组
4. `get_duplicate_group(hash)` — 查看某组全部成员与路径
5. 向用户展示：**组数 / 可释放空间 / 前 5 组示例（保留哪份、删哪几份）**
6. 用户确认后：`plan_create(only_duplicates=true)` → `preview_plan` → 再次确认
7. `plan_execute(plan_id, confirm=true)` — 执行迁移到待删除候选区

## 保留策略

默认保留：路径最短 → 层级最浅 → mtime 最新 → 文件名排序靠前。
其余成员标记为"可删除候选"，**只移动到候选目录，不物理删除**。

## 边界

- 绝不直接 `rm` / 物理删除；删除一律走工具的回收站与 `op_logs`
- 执行前必须展示计划 ID 与条目数，并取得用户明确同意
- 执行后报告 `done` / `failed` / `skipped`，提示可用 `rollback_last(confirm=true)` 回滚
- 不处理白名单外路径，不绕过 `confirm=true`
