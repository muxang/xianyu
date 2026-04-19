# 模块 4：精准检索引擎（四段式 + Selector + Q2Q）

## 4.1 目标与边界

**做什么**：
- 对 StructuredContext，从**合适的知识库**找出**最相关的 1 条知识**（或明确返回"找不到"）
- 实现**四段式**：意图路由 → 结构化硬过滤 → 候选召回 → LLM Selector 精选
- 针对四种知识库采用**不同策略**
- 提供明确的**置信度和拒答信号**

**不做什么**：
- 不做意图识别（模块 3 已做）
- 不做答案生成（生成层）
- 不做知识库增删改（管理后台）
- 不做向量实时生成（离线算好）

**核心哲学**：
> **宁可正确地拒答，不要错误地回答。**

## 4.2 外部契约

**上游**：LangGraph 子图

**下游**：
- Postgres + pgvector
- LiteLLM（Qwen-Flash 做 Selector）
- BGE-M3 embedding

**核心 API**：

```python
async def retrieve(
    context: StructuredContext,
    retrieval_hint: RetrievalHint | None = None
) -> RetrievalResult
```

**RetrievalHint**（可选）：

```python
{
    "force_kb": KnowledgeBase | None,
    "filters_override": dict | None,
    "min_confidence": float | None,   # 默认 0.7
    "allow_none": bool,                # 默认 True
}
```

**RetrievalResult**：

```python
{
    "found": bool,

    # found=True
    "selected_unit": KnowledgeUnit | None,
    "confidence": float,

    # found=False
    "reason": NoneReason,
    "alternative_suggestion": ClarifyHint | None,

    # 诊断
    "kb_used": KnowledgeBase | None,
    "strategy_used": RetrievalStrategy,
    "candidates_count": int,
    "filter_chain": [FilterStep],
    "latency_breakdown": dict,
    "trace_id": str,
}
```

**枚举**：

```python
class NoneReason(Enum):
    KB_EMPTY = "KB_EMPTY"
    SELECTOR_REJECTED = "SELECTOR_REJECTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    AMBIGUOUS = "AMBIGUOUS"
    NO_KB_MATCH = "NO_KB_MATCH"

class KnowledgeBase(Enum):
    PRODUCT = "PRODUCT"
    FAQ = "FAQ"
    SCRIPT = "SCRIPT"
    POLICY = "POLICY"

class RetrievalStrategy(Enum):
    STRUCTURED_ONLY = "STRUCTURED_ONLY"
    VECTOR_Q2Q = "VECTOR_Q2Q"
    LLM_FULL_LIST = "LLM_FULL_LIST"
    VECTOR_SEMANTIC = "VECTOR_SEMANTIC"
    HYBRID = "HYBRID"
```

## 4.3 四段式流程

```
Stage 1: KB Router
     ↓
Stage 2: Hard Filter
     ↓
Stage 3: Candidate Recall
     ↓
Stage 4: LLM Selector
```

### Stage 1: KB Router

**规则配置**（YAML）：

```yaml
intent_to_kb:
  FAQ:              [FAQ]
  PRODUCT_INQUIRY:  [PRODUCT, FAQ]
  NEGOTIATION:      [SCRIPT]
  AFTER_SALES:      []
  COMPLAINT:        []
  ORDER_STATUS:     [SCRIPT]
  CHITCHAT:         []
  INTENT_UNCLEAR:   []
  OTHER:            []
```

空 KB 列表：返回 `found=False, reason=NO_KB_MATCH`

多 KB：按顺序尝试，第一个 `found=True` 就用

### Stage 2: Hard Filter

**每个 KB 的过滤**：

```
PRODUCT：
  seller_id = current_seller
  item_id = entities.product_refs[0].item_id (优先)
  status = 'on_sale' (兜底)

FAQ：
  seller_id IN (current_seller, 'GLOBAL')
  category IN intent.related_categories
  valid_from <= now() <= valid_to
  is_active = true

SCRIPT：
  seller_id IN (current_seller, 'GLOBAL')
  scenario = intent.primary
  tags 匹配 context

POLICY：
  scope = 'platform' OR seller_id = current_seller
  topic IN intent.topic_hints
  valid_at 包含现在
```

**实现**：
- 原生 SQL WHERE
- tags 用 `@>`
- **必带 LIMIT 100** 防全表扫描
- 候选 < 1 → KB_EMPTY

**降级**：候选空 ≠ 用户无解 → 去掉 category 约束再查

### Stage 3: Candidate Recall

**决策表**：

```
PRODUCT → STRUCTURED_ONLY（不做向量）
FAQ：
  候选 ≤ 20 → LLM_FULL_LIST
  候选 > 20 → VECTOR_Q2Q (top-8)
SCRIPT → 总是 LLM_FULL_LIST（库小）
POLICY → 总是 VECTOR_SEMANTIC (top-5)
```

**Q2Q 召回**：
1. 用户 query 的 embedding
2. 查 `faq_variants`：
   ```sql
   SELECT faq_id, variant_question, 1 - (embedding <=> query_emb) AS score
   FROM faq_variants
   WHERE faq_id IN (<stage2 过滤出的 faq_id>)
   ORDER BY embedding <=> query_emb
   LIMIT 8
   ```
3. 去重：同 faq 多变体只保留 best
4. 最多 8 个不同 FAQ

**核心**：Q2Q 匹配"问题 vs 问题变体"，**不是**"问题 vs 答案"

**政策语义检索**：
1. 政策入库时按语义分块（chunk_size ~ 300 字）
2. 每 chunk 独立 embedding
3. top-5 by cosine
4. 同文档多 chunk → 保留 best score

**pgvector 配置**：
- HNSW 索引（不用 IVFFlat）
- `m=16, ef_construction=64`
- 查询 `SET LOCAL hnsw.ef_search = 40`

### Stage 4: LLM Selector

**任务**：从 N 个候选选 1 个 或 返回"都不合适"

**模板 A：带上下文选择（FAQ/POLICY/SCRIPT）**

```
你是闲鱼客服知识选择器。

买家消息：{query}
最近对话：{last_2_turns}

候选：
[1] {content_1}
[2] {content_2}
...

规则：
1. 只有候选能直接、准确回答时才选
2. 只是"部分相关"，选 0
3. 买家消息含糊（如"这个呢"），选 0
4. 不强行拼凑

输出 JSON：
{"selected_id": <编号或0>, "confidence": <0-1>, "reason": "..."}
```

**模板 B：商品咨询（PRODUCT）**

商品库结构化查询本身 1 个候选，走相关性校验：

```
买家消息：{msg}
当前商品：{product_info}

判断这条商品信息能否直接回答问题？

输出 JSON：
{"is_relevant": true/false, "confidence": <0-1>, "reason": "..."}
```

**关键设计要求**：

1. **必须允许拒答**：prompt 强调"选 0"合法
2. **confidence 自报**：小模型加引导后挺准
3. **reason 必须要求**：观测用，强制"边思考边选"提准
4. **模型**：Qwen-Flash（便宜快够）
5. **参数**：`temperature=0.1`, `max_tokens=100`, `response_format=json_object`, 超时 3 秒

### 置信度阈值链

```python
if result.selected_id == 0:
    return NoneReason.SELECTOR_REJECTED

if result.confidence < hint.min_confidence (default 0.7):
    return NoneReason.LOW_CONFIDENCE

if len(candidates) >= 2:
    score_gap = top1.vector_score - top2.vector_score
    if score_gap < 0.05:
        if result.confidence < 0.85:
            return NoneReason.AMBIGUOUS

return found=True
```

**阈值配置**：

```yaml
selector_config:
  min_confidence_default: 0.70
  min_confidence_ambiguous: 0.85
  vector_score_gap_threshold: 0.05
```

### 降级链

```
尝试 1：按 Stage 1 路由 + 完整 Stage 2 过滤
尝试 2：放宽 Stage 2（去 category/tags）
尝试 3：换次要 KB 重试
最终：found=False
```

**预算**：
- 最多 2 次 LLM 调用
- 总时长 < 3s
- 避免无限降级

### 缓存

```
Query embedding: hash(query_text) → TTL 1h
Selector 结果：一般不缓存
```

**缓存失效**：知识库变更时 `DELETE FROM redis_keys LIKE 'retrieval_cache:*'`

## 4.4 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| Rerank | 不做（有 Selector） | Selector 更强 |
| 向量库 | pgvector | 规模够、运维简单 |
| embedding | BGE-M3 | 中文短文本 SOTA |
| 商品库向量 | 不用 | 结构化更准 |
| 话术库向量 | 不用 | 库小 LLM 直选 |
| Selector 模型 | Qwen-Flash | 成本/准确率平衡 |
| 拒答信号 | 明确枚举 NoneReason | 下游精准路由 |

## 4.5 边界情况

**候选空（过滤后）**：Stage 2 返回 KB_EMPTY

**Selector 非法 JSON**：重试 1 次；仍失败视为 SELECTOR_REJECTED

**Selector 越界（返回 99 但只 5 个候选）**：视为 SELECTOR_REJECTED

**embedding 服务失败**：
- 降级 LLM_FULL_LIST（即使候选 > 20）
- 超 token 上限放弃 → found=False + 告警

**高频同 query**：1 秒 1 次缓存；10 次/分钟 → 监控

**过期知识未清理**：Stage 2 valid_at 保底；定期扫描提醒

**Selector 选中但内容损坏**：
- 检查 `selected_unit.content` 非空
- 空则 fallback 下个候选

**Q2Q 变体不足**：观测"变体数 < 5 的 FAQ 命中率"，提醒补充

## 4.6 测试要求

**单元**：
- 每个 Stage 独立（mock 下游）
- KB Router 所有意图
- Hard Filter SQL 正确性
- Selector（MockLLM 各种返回）
- 置信度链所有分支
- 降级链每级触发

**集成**（真实 Postgres + pgvector + MockLLM）：
- 端到端每种 intent 3 案例
- 知识库变化后缓存失效
- 性能 p99 < 2s

**精度基准测试（关键）**：
- 200 条 (query + context, 期望结果) 测试集
- 应选中哪条 OR 应拒答
- 意图类型均衡
- **目标**：
  - Precision > 90%
  - Recall > 80%
  - 拒答准确率 > 85%

**上线指标**：
- 各 KB 命中率
- NoneReason 分布
- Selector 置信度分布
- 降级路径使用率
- p99 延迟

## 4.7 数据库表

```sql
CREATE TABLE knowledge_units (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  seller_id TEXT NOT NULL,          -- 'GLOBAL' 表示全局
  type TEXT NOT NULL,               -- FAQ | SCRIPT | POLICY | PRODUCT_INFO
  content TEXT NOT NULL,
  category TEXT,
  sub_category TEXT,
  tags TEXT[] DEFAULT '{}',
  scenario TEXT,                    -- 话术专用
  priority INT DEFAULT 0,
  valid_from TIMESTAMPTZ DEFAULT NOW(),
  valid_to TIMESTAMPTZ,
  is_active BOOLEAN DEFAULT TRUE,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ku_seller_type ON knowledge_units(seller_id, type, is_active);
CREATE INDEX idx_ku_category ON knowledge_units(type, category);
CREATE INDEX idx_ku_tags ON knowledge_units USING GIN(tags);

CREATE TABLE faq_variants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  faq_id UUID NOT NULL REFERENCES knowledge_units(id) ON DELETE CASCADE,
  variant_question TEXT NOT NULL,
  embedding vector(1024),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_fv_embedding ON faq_variants
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_fv_faq_id ON faq_variants(faq_id);

CREATE TABLE policy_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  policy_doc_id UUID NOT NULL,
  chunk_index INT NOT NULL,
  content TEXT NOT NULL,
  embedding vector(1024),
  topic TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_pc_embedding ON policy_chunks
  USING hnsw (embedding vector_cosine_ops);
```

## 4.8 实施指引

**实施顺序**：
1. 定义所有 schema 和枚举
2. 建表（HNSW + GIN 索引）
3. 实现 KB Router
4. 实现 Hard Filter（每个 KB 一个函数）
5. 实现 Candidate Recall（分策略）
6. 实现 Selector（LLM 封装 + JSON 解析 + 重试）
7. 实现置信度检查链（独立函数）
8. 实现降级链
9. 测试（单元 → 集成 → 基准）

**容易踩坑**：
- pgvector 距离操作符：`<=>` cosine（BGE-M3 用这个），别搞反
- 相似度 = 1 - distance，不是 distance
- HNSW 的 `ef_search` 可调（查询时 SET LOCAL）
- JSON Mode 可能带 markdown → strip
- BGE-M3 已归一化，别重复
- `seller_id = 'GLOBAL'` 语义要 OR
- 读操作不用显式事务
- Selector 候选编号 **从 1 开始**（LLM 对 0 开始的表现差）

**不要做**：
- ❌ 不引入 Elasticsearch / Milvus
- ❌ 不搞 Cross-Encoder Rerank
- ❌ Selector 不选多条
- ❌ embedding 不放运行时链路（必离线）
- ❌ 本层不做 LLM 答案生成
- ❌ 不用 LIKE 中文模糊
- ❌ 不盲目用 Selector confidence（必过阈值链）

**输出物**：
- `backend/app/modules/retrieval/` 目录
- `schema.py`、`retriever.py`
- `kb_router.py`、`hard_filter.py`、`candidate_recall.py`
- `selector.py`、`confidence_gate.py`
- `prompts/`、`config/kb_mapping.yaml`、`config/thresholds.yaml`
- `db/migrations/`、`tests/retrieval/`

**审计重点**：
- 所有 SQL 都有 LIMIT
- HNSW ef_search 按查询类型调
- JSON Mode 返回有容错
- 拒答信号清晰到达上游（不被异常吞）
- 降级链无死循环
- 基准测试集覆盖"应拒答"case
