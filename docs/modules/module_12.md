# 模块 12：可观测与数据层

## 12.1 目标与边界

**做什么**：
- 所有 Agent 流程有 Langfuse trace
- Postgres schema 汇总管理（Alembic）
- Redis 键空间统一命名
- 关键业务指标监控（Prometheus）
- 关键告警配置（飞书）

**不做什么**：
- 不是独立服务（穿插在各模块中）
- 不做用户级数据分析（Web 看板的事）

**本质**：**系统透明层**。出问题能查、指标清晰、告警及时。

## 12.2 Langfuse 集成

### Trace 结构

```
trace: inbound_message(seller_id, buyer_id, trace_id)
  ├─ span: understanding
  │   ├─ span: image_understand
  │   ├─ span: intent_classify
  │   └─ span: context_load
  ├─ span: routing → subgraph_name
  ├─ span: subgraph_execution
  │   ├─ span: retrieval
  │   └─ span: generation
  ├─ span: automation_classify
  ├─ span: compliance_check
  └─ span: outbound_shaping
```

每 span 记录：
- 输入、输出
- latency
- cost（LLM 调用时）
- seller_id、conversation_id

### 接入方式

- LiteLLM 原生支持 Langfuse（配环境变量即可）
- LangGraph 通过 callback 手动接入
- 飞书/业务层通过 `langfuse.Client` 显式埋点

## 12.3 Postgres Schema 汇总

所有模块的表集中在 `backend/migrations/` 下，用 **Alembic** 管理。

**核心表清单**：

```
# 模块 1
sellers

# 模块 4 检索
knowledge_units
faq_variants
policy_chunks

# 模块 6 示例
gold_examples

# 模块 5 议价
negotiation_states

# 对话归档（跨模块）
conversations
buyer_profiles
products

# 模块 2 审核
# review_state 在 Redis，不在 PG

# 模块 8 风控
compliance_rules
compliance_violations

# 模块 7-A 飞书
notification_routing

# LangGraph checkpoint
langgraph_checkpoints  (PostgresSaver 自建)

# Langfuse 自己的表在单独库
```

**Alembic 命令**：
```bash
cd backend
uv run alembic init migrations    # 首次
uv run alembic revision --autogenerate -m "add sellers table"
uv run alembic upgrade head
uv run alembic downgrade -1
```

**关键表结构**：

### conversations（对话归档）

```sql
CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  seller_id TEXT NOT NULL,
  buyer_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  item_id TEXT,
  message_id TEXT UNIQUE,
  role TEXT NOT NULL,              -- buyer / seller / ai_suggestion
  content_text TEXT,
  content_images JSONB,
  intent TEXT,
  automation_level TEXT,
  ai_suggestion TEXT,
  final_sent TEXT,
  was_modified BOOLEAN DEFAULT FALSE,
  trace_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_conv_lookup ON conversations(seller_id, conversation_id, created_at DESC);
CREATE INDEX idx_conv_buyer ON conversations(seller_id, buyer_id);
CREATE INDEX idx_conv_trace ON conversations(trace_id);
```

### buyer_profiles（画像）

```sql
CREATE TABLE buyer_profiles (
  seller_id TEXT NOT NULL,
  buyer_id TEXT NOT NULL,
  first_seen_at TIMESTAMPTZ,
  last_seen_at TIMESTAMPTZ,
  total_messages INT DEFAULT 0,
  total_conversations INT DEFAULT 0,
  negotiation_count INT DEFAULT 0,
  negotiation_successes INT DEFAULT 0,
  deal_count INT DEFAULT 0,
  tags TEXT[] DEFAULT '{}',
  risk_flags TEXT[] DEFAULT '{}',
  metadata JSONB DEFAULT '{}',
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (seller_id, buyer_id)
);
```

### products（商品缓存）

```sql
CREATE TABLE products (
  seller_id TEXT NOT NULL,
  item_id TEXT NOT NULL,
  title TEXT NOT NULL,
  price NUMERIC,
  description TEXT,
  condition TEXT,
  images JSONB,
  bottom_price NUMERIC,
  negotiation_strategy JSONB,
  status TEXT DEFAULT 'on_sale',
  synced_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (seller_id, item_id)
);

CREATE INDEX idx_products_title ON products USING gin (to_tsvector('simple', title));
```

## 12.4 Redis 键空间

统一写在 `backend/app/shared/redis_keys.py` 常量：

```python
# 模块 1 去重
MSG_DEDUP = "msg_dedup:{seller_id}:{message_id}"  # TTL 24h

# 模块 2 队列
IN_QUEUE = "in_queue:{seller_id}"                  # Stream
OUT_QUEUE = "out_queue:{seller_id}"                # Stream
OUT_QUEUE_DELAYED = "out_queue:delayed:{seller_id}"  # Sorted Set
REVIEW_QUEUE = "review_queue:{seller_id}"          # Stream
REVIEW_STATE = "review_state:{review_id}"          # Hash, TTL 7d

# 模块 3 对话缓存
CONVERSATION_CACHE = "conversation_cache:{conv_id}"  # List, TTL 24h

# 模块 5 议价状态
NEGOTIATION_STATE_CACHE = "negotiation_cache:{seller_id}:{buyer_id}:{item_id}"  # TTL 48h

# 模块 9 频控
RATE_LIMIT_ACCOUNT = "rate_limit:account:{seller_id}"  # Sorted Set 滑动窗口
RATE_LIMIT_CONV = "rate_limit:conv:{conv_id}"

# 模块 7-A 深链
SHORT_TOKEN = "short_token:{token}"                # TTL 30s

# 模块 4 embedding 缓存
QUERY_EMBEDDING = "query_emb:{hash}"               # TTL 1h
```

## 12.5 Prometheus 业务指标

埋点位置说明：

```
# 模块 1 SellerSession
inbound_messages_total{seller_id}              # Counter
session_status{seller_id, status}              # Gauge
cookie_expired_alerts_total{seller_id}          # Counter

# 模块 2 队列
queue_depth{seller_id, queue_name}             # Gauge
queue_backlog_alerts_total{queue_name}         # Counter

# 模块 3 理解
intent_classification_total{intent, confidence_bucket}  # Counter
intent_classify_latency_seconds                # Histogram

# 模块 4 检索
retrieval_hit_rate{kb_type}                    # Gauge
retrieval_none_reason_total{reason}            # Counter
retrieval_latency_seconds{strategy}            # Histogram

# 模块 8 风控
automation_level_distribution{level}            # Counter
compliance_violations_total{type, severity}    # Counter

# 模块 9 出站
outbound_messages_total{seller_id, level}      # Counter
style_rewrite_failures_total                    # Counter

# 模块 11 模型
llm_calls_total{purpose, model, result}        # Counter
llm_latency_seconds{purpose}                   # Histogram
llm_cost_usd_total{purpose, model}             # Counter

# 模块 7 审核
review_resolution_total{action, automation_level}  # Counter
review_approval_rate{seller_id}                 # Gauge
review_latency_seconds                         # Histogram
```

## 12.6 飞书告警规则

**Cookie 失效**：模块 1 health_check 失败 → 立即告警

**队列积压**：
- `in_queue` > 100 持续 5 分钟
- `review_queue` > 20 持续 5 分钟

**LLM 调用失败**：
- 失败率 > 5% 持续 5 分钟
- 某 purpose 连续失败 > 3 次

**异常消息洪峰**：
- 某账号 1 分钟 > 50 条入站 → 疑似刷屏/风控

**高风险对话**：
- L1 消息产生 → 立即告警

**风控触发**：
- `RISK_CONTROLLED` 状态的 seller → 立即告警

## 12.7 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| Trace | Langfuse | 开源自部署 |
| Schema | Alembic | 版本化 |
| 指标 | Prometheus | 工业标准 |
| 告警 | 飞书机器人 | 已有载体 |

## 12.8 实施指引

**实施顺序**：
1. 所有模块 schema 汇总，写 Alembic 迁移
2. Redis 键常量文件（`shared/redis_keys.py`）
3. Langfuse 自部署（Docker，已在 infra 里）
4. LiteLLM → Langfuse 接入（配环境变量）
5. LangGraph → Langfuse 接入（callback）
6. Prometheus 指标导出器（`prometheus_client`）
7. 告警规则配置

**Langfuse 环境变量**：
```bash
LANGFUSE_PUBLIC_KEY=pk-lf-xxx
LANGFUSE_SECRET_KEY=sk-lf-xxx
LANGFUSE_HOST=http://localhost:3100
```

LiteLLM 会自动检测这些变量并启用 Langfuse。

**不要做**：
- ❌ 不埋点就上线（出问题查不了）
- ❌ 告警太敏感（会被忽略）
- ❌ 指标名乱起（Prometheus 约定 snake_case）
- ❌ trace 带 PII（买家个人信息要脱敏）

**输出物**：
- `backend/migrations/`（全部 Alembic 迁移）
- `backend/app/shared/redis_keys.py`
- `backend/app/shared/metrics.py`（Prometheus 指标定义）
- `backend/app/shared/tracing.py`（Langfuse 封装）
- `infra/docker/prometheus.yml`
- `infra/docker/alertmanager.yml`（可选）
- `docs/observability.md`（指标清单、告警规则说明）
