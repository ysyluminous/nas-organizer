---
name: nas-report
description: NAS 数据体检与路由入口。用 nas-organizer 生成整盘画像（容量、文件类型、归属域、冷热层、重复占用、大文件、陈旧文件），指出风险并推荐下一步使用哪个专项 Skill。当用户说"看看 NAS 里有什么""分析一下磁盘""做个数据体检"" NAS 好乱从哪开始"时使用。
---

# NAS 数据体检

**只读 Skill**。只查询索引，不改动任何文件，不需要确认。

## 适用

- 首次接触一套 NAS，需要先摸清家底
- 想知道哪些盘快满了、哪些文件类型占空间、重复文件浪费多少
- 不知道该用哪个专项 Skill，先来这里路由

## 调用链（全部只读）

按项目 `AGENTS.md` 约束，通过 MCP 或 CLI 依次调用：

1. `get_stats` — 总数、重复汇总、各维度分布
2. `get_stats_by_volume` — 找出接近满载的卷
3. `get_stats_by_type` / `get_stats_by_domain` / `get_stats_by_temperature` — 三维画像
4. `get_duplicate_summary` + `list_duplicates` — 重复文件占用
5. `find_large_files`、`find_old_files`、`find_low_confidence` — 风险与待复核项
6. `list_scan_states` — 索引是否最新；若过期，先 `scan_start` 再重查

## 输出格式

```
容量：N 卷 / 总计 X 文件 / 重复占用 Y GB
分布：类型 Top5 / 归属域 / 冷热层
风险：接近满载的卷、超大文件、超期未访问文件、低置信度分类
建议路由：
  - 重复多 → dedup-finder
  - 分类乱 → file-sorter
  - 照片多 → photo-organizer
  - 音乐乱 → music-organizer
  - 工作文件散落 → work-organizer
  - 下载堆积 → download-cleaner
  - 备份状态不明 → backup-auditor
  - 已有计划待处理 → migration-review
```

## 边界

- 不执行 `plan_execute`、`rollback`、`backup_hot`
- 不读取 `data/auth.yaml`、`.auth_secret`、`INITIAL_PASSWORD.txt`
- 所有路径必须落在 `security.allowed_paths` 内；越界即停止
- 报告数字保留整数或一位小数，注明单位是文件数还是字节
