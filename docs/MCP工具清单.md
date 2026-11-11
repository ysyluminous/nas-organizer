# MCP 工具清单（72 个原子工具）

> `src/nas_organizer/mcp_server.py` 实现，纯标准库 JSON-RPC 2.0 over stdio，无额外依赖。
> 行为契约见仓库根 [AGENTS.md](../AGENTS.md)。同类对比中 Nasio 26 个、zspace-cli 24 个，本项目 **72 个**（D4→54，D5→64，D6→72）。

## 1. 启动与认证

```bash
# 本机
export PYTHONPATH=src
export NAS_ORGANIZER_CONFIG=config/config.yaml
export NAS_ORGANIZER_TOKEN="<Web 登录后返回的 Bearer Token>"
python -m nas_organizer.mcp_server

# 容器内
docker compose run --rm \
  -e NAS_ORGANIZER_TOKEN="$TOKEN" \
  nas-organizer python -m nas_organizer.mcp_server
```

| 场景 | 行为 |
|---|---|
| 不设 Token | **只读模式**：54 个只读工具可用，18 个写工具返回"需要授权" |
| Token 无效 / 过期 | 服务启动即失败，不降级为只读 |
| Token 有效 | 全部 72 个工具可用 |
| `execute` / `rollback` / `backup_hot` / `feedback_reset` | 除有效 Token 外，还必须在参数里带 `confirm=true` |

Token 复用 Web 登录体系（PBKDF2 + HMAC 签名），改密后旧 Token 立即失效。

## 2. 只读工具（54 个，无需 Token）

### 基础与安全（7）

| 工具 | 用途 |
|---|---|
| `health` | 健康检查：版本、认证状态、工具数量、白名单模式 |
| `get_agent_policy` | 返回 AGENTS.md 全文，供 Agent 读取行为契约 |
| `get_config_summary` | 脱敏配置摘要（卷数、白名单数、模型开关），不含密钥 |
| `list_volumes` | 列出扫描卷及角色 hot/warm/cold |
| `list_write_targets` | 列出受控写入目标 |
| `security_status` | 白名单根目录、严格模式、越界事件计数 |
| `security_check_path` | 检查一个路径是否在白名单内（防 `..` 穿越） |

### 统计与发现（12）

| 工具 | 用途 |
|---|---|
| `get_stats` | 全量统计：总数、重复汇总、各维度分布 |
| `get_stats_by_volume` | 按卷统计文件数与字节 |
| `get_stats_by_domain` | 按个人/工作/生活/资源统计 |
| `get_stats_by_type` | 按音乐/视频/图书/文档等统计 |
| `get_stats_by_temperature` | 按热/温/冷统计 |
| `get_duplicate_summary` | 重复组数、可释放空间 |
| `backup_verify` | 冷数据完整性校验（只读） |
| `list_files` | 按域/类型/温度分页查询 |
| `get_file` | 按 ID 取单个文件记录 |
| `count_files` | 条件计数，不返回明细 |
| `search_files` | 路径/文件名关键词搜索 |
| `find_low_confidence` | 低置信度分类，供人工复核 |

### 风险扫描（3）

| 工具 | 用途 |
|---|---|
| `find_large_files` | 默认 >2GB 的大文件 |
| `find_old_files` | 超过 N 天未修改的陈旧文件 |
| `list_rules` | 分类规则文件存在性与路径 |

### 重复与计划（9）

| 工具 | 用途 |
|---|---|
| `list_duplicates` | 重复文件组，按浪费空间降序 |
| `get_duplicate_group` | 某重复组的全部成员 |
| `list_plans` | 迁移计划列表 |
| `get_plan` | 计划详情与明细 |
| `preview_plan` | 预览前 N 条 old→new |
| `list_plan_items` | 按状态筛选计划条目 |
| `list_op_logs` | 操作审计日志（回滚追溯） |
| `list_scan_states` | 各卷最近扫描状态 |
| `get_scan_state` | 单卷扫描状态 |

### 清理 / 审计 / 回收站（4）

| 工具 | 用途 |
|---|---|
| `cleanup_discover` | 只读扫描 12 类清理候选（空文件/临时/种子/陈旧压缩包等），不删除 |
| `backup_audit` | 备份审计四项检查（存在性/版本/陈旧/冲突），只读 |
| `recycle_list` | 列出两级回收站项目 |
| `recycle_stats` | 回收站各阶段数量与占用字节 |

### D4 反馈 / AI / 媒体 / 感知哈希（6）

| 工具 | 用途 |
|---|---|
| `feedback_list` | 查看人工反馈条目（关键词→域/类型权重） |
| `ai_status` | 查看可选 AI 兜底开关、provider、依赖可用性 |
| `media_capability` | 查看 exif / ID3 / pHash 可选依赖是否可用 |
| `photo_plan_suggest` | 按 exif 拍摄日期生成照片归档建议（只读，不移动） |
| `music_plan_suggest` | 按 ID3 标签生成音乐归档建议（只读，不移动） |
| `phash_similar` | 感知哈希查找缩放/压缩后的相似图片，只读 |

### D5 虚拟目录 / 进度 / 任务监控（6）

| 工具 | 用途 |
|---|---|
| `view_list` | 列出所有虚拟目录视图（标签分组），不移动文件 |
| `view_get` | 查看一个虚拟视图下的文件（按标签检索） |
| `plan_progress` | 查看计划执行进度（断点续传状态） |
| `csv_undo_preview` | 从 CSV 预览回滚条目数量（只读） |
| `season_suggest` | 剧集季数识别归档建议（S01E02 / 第N集 / EP） |
| `task_list` | 任务监控：运行中进度与调度/操作历史（只读） |

### D6 复核台 / 意图 / 外部工具（7）

| 工具 | 用途 |
|---|---|
| `review_queue` | 待复核队列：低置信度分类与冲突计划（只读） |
| `review_stats` | 复核统计：待复核数量与已裁决数（只读） |
| `review_history` | 查看人工裁决历史（只读） |
| `intent_schema` | 自然语言意图→结构化工具映射说明（服务端不接 LLM） |
| `external_capability` | 查看 FFmpeg / rclone 可选外部工具是否可用 |
| `video_similar` | FFmpeg 关键帧比对相似视频（缺 FFmpeg 优雅降级） |
| `remote_lsjson` | rclone lsjson 只读预览远端（不下载/不同步/不删除） |

## 3. 受控写工具（18 个，需 Token）

| 工具 | 是否需 confirm | 说明 |
|---|:---:|---|
| `scan_start` | 否 | 扫描建索引，只读扫描不改文件 |
| `classify_start` | 否 | 重新分类，只读 |
| `dedupe_start` | 否 | 三级哈希查重，只读 |
| `plan_create` | 否 | 生成迁移计划，写数据库但不碰文件 |
| `plan_execute` | **是** | 执行迁移，必须 `confirm=true` |
| `rollback_last` | **是** | 回滚最近操作 |
| `backup_hot` | **是** | 热数据备份 |
| `plan_batch` | 否 | 批量归类生成多目录计划，不执行移动 |
| `recycle_promote` | 否 | stage1 超时项提升到 stage2，不删除 |
| `recycle_restore` | **是** | 还原回收站项目 |
| `recycle_purge` | **是** | 物理清除 stage2（不可逆），仅 stage2 允许 |
| `feedback_add` | 否 | 记录一次人工纠正（关键词→域/类型），只写索引库 |
| `feedback_reset` | **是** | 清空全部人工反馈，必须 `confirm=true` |
| `view_tag` | 否 | 给文件打标签（虚拟归类，不移动文件） |
| `view_untag` | 否 | 移除文件标签（不移动文件） |
| `view_auto_tag` | 否 | 按域/类型/温度/年份自动打标（幂等） |
| `csv_undo_import` | 否 | 从 CSV 生成回滚计划（只建计划，不执行文件操作） |
| `review_decide` | **是** | 人工裁决：修正分类并写回反馈，必须 `confirm=true` |

> 注意：`scan_start` 等虽标为"写工具"，是因为它们会更新索引库；它们**不改动用户文件**。清除回收站只能清 stage2，stage1 必须先提升。反馈写操作同样只改索引库，AI 默认关闭且失败静默降级。虚拟目录用标签实现，绝不移动文件；CSV undo 只生成计划不自动执行。复核裁决是受控写（需 Token + confirm），只改索引与反馈、不动用户文件。

## 4. JSON-RPC 示例

```jsonc
// 初始化
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}

// 列出工具
{"jsonrpc":"2.0","id":2,"method":"tools/list"}

// 只读调用
{"jsonrpc":"2.0","id":3,"method":"tools/call",
 "params":{"name":"get_stats","arguments":{}}}

// 危险调用（缺 confirm 会被拒绝）
{"jsonrpc":"2.0","id":4,"method":"tools/call",
 "params":{"name":"plan_execute","arguments":{"plan_id":7,"confirm":true}}}
```

响应中的工具结果统一为：
```json
{"content":[{"type":"text","text":"..."}],"isError":false}
```

## 5. 安全边界

1. **路径校验**：所有路径类工具经 `PathGuard` 归一化后比对白名单，`..` 与软链接逃逸被拦截
2. **审计留痕**：越界尝试写入 `data/security_audit.jsonl`；文件变更写入 `op_logs`
3. **无服务端 LLM**：工具只接受结构化参数，语义解析交给客户端 Agent，服务端不存任何 LLM 密钥
4. **不暴露敏感信息**：无读取认证文件、Token、密钥的工具
5. **只读模式可用**：不配 Token 也能安全体检，方便审阅代码后决定是否授权

## 6. 与 Skill 的关系

- **MCP** 提供原子能力（72 个工具），适合 Agent 编排与跨客户端复用
- **Skill** 提供工作流（9 个专项 + 1 路由），把工具串成有安全关卡的流程
- 两者共享同一份 `AGENTS.md` 行为契约与 `PathGuard` 路径边界
