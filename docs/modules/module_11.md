# 模块 11：模型网关（LiteLLM 封装）

## 11.1 目标与边界

**做什么**：
- 统一 LLM 调用入口：**所有模块通过本模块调模型**
- 支持多模型分层路由（按用途自动选模型）
- 统一重试、超时、降级
- 统一计费和日志
- 支持 JSON Mode、Function Calling、Vision 等模态

**不做什么**：
- 不缓存 LLM 响应（缓存在业务层）
- 不做 Prompt 管理（Prompt 放各业务模块）

**本质**：**LLM 统一入口**。所有业务代码不出现厂商 SDK。

## 11.2 核心 API

```python
async def call_llm(
    purpose: LLMPurpose,
    messages: list[Message],
    response_format: str | None = None,
    tools: list[Tool] | None = None,
    images: list[str] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    seller_id: str | None = None,
    trace_id: str | None = None,
) -> LLMResponse

class LLMPurpose(Enum):
    INTENT_CLASSIFICATION = "INTENT_CLASSIFICATION"   # Qwen-Flash
    RETRIEVAL_SELECTOR = "RETRIEVAL_SELECTOR"         # Qwen-Flash
    MAIN_GENERATION = "MAIN_GENERATION"               # Qwen3.5-Max / GLM-5
    NEGOTIATION_GENERATION = "NEGOTIATION_GENERATION" # Qwen3.5-Max
    STYLE_REWRITE = "STYLE_REWRITE"                   # Qwen-Flash
    IMAGE_UNDERSTANDING = "IMAGE_UNDERSTANDING"       # Qwen-VL-Max
    CHITCHAT = "CHITCHAT"                             # Qwen-Flash (便宜)
```

**路由规则**（YAML 配置）：

```yaml
purpose_routing:
  INTENT_CLASSIFICATION:
    primary: qwen/qwen-flash
    fallback: qwen/qwen-plus
    timeout_ms: 3000
    max_retries: 2

  MAIN_GENERATION:
    primary: qwen/qwen-max
    fallback: zhipu/glm-5
    timeout_ms: 10000
    max_retries: 2

  IMAGE_UNDERSTANDING:
    primary: qwen/qwen-vl-max
    fallback: zhipu/glm-4v
    timeout_ms: 15000
    max_retries: 1

  STYLE_REWRITE:
    primary: qwen/qwen-flash
    fallback: deepseek/deepseek-chat
    timeout_ms: 5000
    max_retries: 1
```

## 11.3 核心能力

### 自动 Fallback

主模型超时/失败 → 备用模型
- 记录哪个模型实际响应
- 超过阈值触发告警（主模型持续失败）

### 结构化日志

每次调用记录：
- `purpose`、`model`、`latency_ms`、`prompt_tokens`、`completion_tokens`
- `cost_estimate`（按模型单价）
- `seller_id`、`trace_id`
- `error` (如失败)

### Langfuse Trace

通过 LiteLLM 的 Langfuse 集成自动上报 trace，或手动封装。

**trace span 名**：`llm.{purpose.lower()}`

### 成本统计

- 每次调用估算 cost（按 model 单价）
- 按 purpose、seller_id、日期聚合
- Prometheus 指标 + Grafana 看板
- 月度成本报告

## 11.4 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 统一入口 | call_llm(purpose, ...) | 模型无关 |
| 路由 | YAML 配置 | 不改代码切换 |
| Fallback | 自动 | 容错 |
| 超时 | 按 purpose 配置 | 不同任务要求不同 |
| JSON Mode | 参数控制 | 结构化输出 |
| 追踪 | Langfuse | 开源自部署 |

## 11.5 边界情况

**所有模型都挂**：
- 返回空响应 + 异常
- 上游按需降级（如改写失败用原文）
- 告警（持续 1 分钟）

**rate limit**：
- 按厂商 header 提示 backoff
- 用 tenacity 指数退避

**超大 prompt**：
- 超 token 上限 → 提前拒绝并告警
- 不让模型静默截断

**API Key 失效**：
- 401 立即告警
- 降级到 fallback 模型（不同厂商）

## 11.6 测试要求

- 每种 purpose 能正确路由到对应模型
- Fallback 触发条件（超时、错误）
- JSON Mode 解析
- 成本统计准确性

## 11.7 实施指引

**实施顺序**：
1. LiteLLM 基础配置（API key 从环境变量）
2. 封装 call_llm 函数
3. 路由逻辑 + fallback
4. 接入 Langfuse
5. 成本统计
6. 测试（真实小请求走各 purpose）

**容易踩坑**：
- LiteLLM 的 model 命名格式：`厂商/模型名`（如 `qwen/qwen-max`）
- Qwen 在 LiteLLM 里走 OpenAI 兼容 API（DashScope）
- JSON Mode 不同厂商参数名不同，LiteLLM 帮你抹平但需要测
- timeout 要用 `asyncio.wait_for`，别光依赖 LiteLLM 自己的

**不要做**：
- ❌ 业务代码直接用 openai/dashscope SDK
- ❌ 本模块做业务 prompt 拼接
- ❌ 缓存响应（业务层决定）

**输出物**：
- `backend/app/modules/model_gateway/`
- `gateway.py`、`router.py`、`schema.py`
- `config/purpose_routing.yaml`
- `cost_calculator.py`
- `tests/model_gateway/`
