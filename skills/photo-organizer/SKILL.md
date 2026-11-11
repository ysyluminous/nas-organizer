---
name: photo-organizer
description: NAS 照片整理。把图片类文件按归属域、年份/月份与冷热层归档，识别截图与陈旧照片，避免误动相册原图。当前按文件时间与分类规则归档；exif DateTimeOriginal 解析在进度计划 D4 上线后自动启用。当用户说"整理照片""照片按时间归档""截图太多"时使用。
---

# 照片整理

## 调用链

1. `count_files(ftype=图片)` — 先确认规模，不盲目动手
2. `list_files(ftype=图片, limit=50)` — 抽样看路径分布与命名
3. `find_old_files(days=365, limit=50)` — 找出长期未动的照片
4. `search_files(keyword=截图|screen|IMG_)` — 识别截图与相机原图
5. `plan_create(ftype=图片, temp=冷, limit=N)` — **小批量**生成计划（先 50 条试水）
6. `preview_plan` → 用户确认 → `plan_execute(confirm=true)`

## 重要边界

- **不直接操作 NAS 原生相册目录**（如群晖 Photo Station、飞牛相册的元数据目录），只处理用户指定的共享文件夹
- 先小批量试运行，确认命名与时间正确后再放量
- 截图 / 微信图片 / 相机原图分开处理，不要混在同一批计划里
- exif 尚未上线时，按 mtime 近似；报告中必须说明"按修改时间，非拍摄时间"
- 不覆盖同名文件，冲突走时间戳重命名

## 报告

`图片总数 / 本次计划数 / 冷热分布 / 抽样路径 / 是否使用 exif / 回滚方式`
