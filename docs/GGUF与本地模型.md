# GGUF 与本地模型（可选，默认关闭）

> 对应进度计划 #36 / #21 / #37。本项目**不内置 GGUF 运行时**——Python 侧只通过
> Ollama 的 HTTP API 调用本地模型，数据不出 NAS、不新增 pip 依赖。是否启用由用户决定。

## 为什么不直接加载 GGUF

| 方案 | 优点 | 为什么不选 |
|---|---|---|
| llama.cpp Python 绑定 | 直接读 GGUF | 需编译原生扩展，x86/ARM 多品牌 NAS 部署困难 |
| 内置 GGUF 推理 | 无需外部服务 | 镜像体积暴涨、CPU 推理拖慢主流程 |
| **Ollama HTTP API** | 一条命令装好、自动管理 GGUF、跨平台 | ✅ 选它：默认关闭、失败降级，主流程零依赖 |

## 三步启用

### 1. 安装 Ollama（在 NAS 或局域网内任一机器）

通用 Docker 部署（x86_64，群晖/威联通/飞牛/铁威马/绿联均可）：

```yaml
# docker-compose.yml 追加一个服务
services:
  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    volumes:
      - /volume1/docker/ollama:/root/.ollama   # 按你的 NAS 实际路径改
    ports:
      - "11434:11434"
    restart: unless-stopped
```

或直接在 x86 主机：`curl -fsSL https://ollama.com/install.sh | sh`

### 2. 拉取一个 GGUF 模型

Ollama 会自动下载并管理 GGUF（无需手动找文件）：

```bash
docker exec -it ollama ollama pull qwen2.5:3b      # 中文友好、CPU 可跑
# 或小一点：ollama pull qwen2.5:0.5b
```

想用自定义 GGUF（例如自己量化的模型）：

```bash
# 写一个 Modelfile
FROM ./你的模型.gguf
PARAMETER temperature 0

ollama create my-sorter -f Modelfile
```

### 3. 配置 NAS Organizer

在 `config.yaml`：

```yaml
ai:
  enabled: true
  provider: ollama
  base_url: http://ollama:11434   # 同 compose 网络用服务名；跨机用 http://IP:11434
  model: qwen2.5:3b               # 或 my-sorter
  timeout: 15
  threshold: 0.8                  # 只处理置信度 < 0.8 的疑难件
  domains: [个人, 工作, 生活, 资源]
```

重启容器后验证：

```bash
python -m nas_organizer --config config/config.yaml ... # CLI
# 或看 MCP 工具 ai_status：enabled=true / available=true
```

## 行为边界（重要）

1. **默认关闭**：`ai.enabled=false` 时 `build_provider()` 返回 None，零依赖加载，行为与不装 Ollama 完全一致。
2. **只兜底疑难件**：规则（路径/别名/扩展名/关键词/反馈）全部判不出来、置信度仍低于 0.8 时才调用模型。
3. **失败静默降级**：断网、Ollama 没起、模型没拉、返回非法 JSON、超时——一律返回 None，文件保留「资源/低置信度」结果并进入复核台，**主流程绝不被 AI 拖垮**。
4. **数据不出 NAS**：请求只发往你配置的 `base_url`（建议局域网地址）；文件名与目录片段会作为 prompt 发送，敏感场景请保持内网部署。
5. **服务端不存密钥**：Ollama 无 API Key；服务端也不接任何 LLM 密钥，语义解析交给客户端 Agent。

## OCR 可选插件（extractor 接口）

D7 提供稳定的 `Extractor` 协议（`src/nas_organizer/extractors.py`）：

```python
class Extractor(Protocol):
    name: str
    def available(self) -> bool: ...
    def extract(self, path) -> str | None: ...
```

- 默认 `NullExtractor`（`extractor.enabled=false`），零行为变化。
- 系统装有 `tesseract` 时可启用 TesseractExtractor 抽取图片文字（中文需 `chi_sim` 语言包）：

```yaml
extractor:
  enabled: true
  provider: tesseract
  lang: chi_sim+eng
```

- OCR 只出文本，**不做 LLM 分类**；分类仍由规则/Agent 完成，符合"服务端零密钥"底线。
- 可用 `plugin_capability` MCP 工具或 `python -m nas_organizer plugins` 查看所有可选能力。

## 性能建议

- CPU -only NAS 用 `qwen2.5:0.5b` / `3b`，别上 7b 以上；
- `threshold` 调到 0.85 可进一步减少模型调用量；
- 疑难件本来就少，批量整理时模型调用是长尾，不会成为瓶颈。
