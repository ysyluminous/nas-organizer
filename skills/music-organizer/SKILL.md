---
name: music-organizer
description: NAS 音乐库整理。按音乐类型与归属域归档，区分无损/有损、识别散落在下载目录的专辑，生成不覆盖原文件的迁移计划。ID3 标签（Artist/Album/Track）解析在进度计划 D4 上线后自动启用。当用户说"整理音乐""音乐归类""无损音乐在哪"时使用。
---

# 音乐整理

## 调用链

1. `count_files(ftype=音乐)` 与 `get_stats_by_type` — 规模与格式
2. `list_files(ftype=音乐, limit=50)` — 抽样路径
3. `search_files(keyword=flac|ape|mp3)` — 分格式查看（当前按扩展名匹配路径/文件名）
4. `plan_create(ftype=音乐, domain=生活, limit=N)` → `preview_plan`
5. 用户确认 → `plan_execute(confirm=true)`

## 边界

- 不修改音频文件本身（不改 ID3 标签内容），只移动与重命名路径
- 不拆散完整的专辑目录（一个专辑文件夹内多首歌应整体迁移；当前工具按文件生成计划，抽样时须特别检查）
- 同名不同专辑的文件不要合并，依赖冲突重命名
- 无损与有损分开归档，便于后续选择
- ID3 未上线前，只能按文件名与目录名推断，报告中必须说明

## 报告

`音乐文件数 / 格式分布 / 本次计划数 / 抽样 old→new / 是否使用 ID3 / 回滚方式`
