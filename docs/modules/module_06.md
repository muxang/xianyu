# 模块 6：金牌示例库 + 动态 Few-shot

## 6.1 目标与边界

**做什么**：
- 管理"金牌回复"的示例库：存储、检索、按场景动态选取
- 为生成层提供**上下文相关的 few-shot 示例**
- 支持"一键沉淀"：卖家在飞书/Web 确认/修改的回复，沉淀为未来示例
- 支持版本化和质量打分

**不做什么**：
- 不训练模型（不做蒸馏）
- 不直接生成回复（只提供示例）
- 不做人工打标工作流（只管存储/检索）

**本质**：**替代蒸馏的轻量方案** ——用高质量示例库 + 运行时动态选取，实现"持续学习金牌风格"。

## 6.2 外部契约

**上游**：
- 生成层：按 (scenario, context) 请求 few-shot
- 飞书工作台：卖家点"加入示例库"
- Web 工作台：人工批量导入、编辑

**核心 API**：

```python
async def get_few_shot(
    scenario: Scenario,
    buyer_text: str,
    seller_id: str,
    k: int = 3,
    tags_filter: list[str] | None = None,
) -> list[FewShotExample]

async def add_example(
    example: FewShotExampleInput,
    source: ExampleSource,
    added_by: str,
) -> str  # example_id

async def promote_example(example_id: str, to_global: bool) -> None
async def deactivate_example(example_id: str, reason: str) -> None
```

## 6.3 数据结构

```python
FewShotExample {
    id: UUID
    scenario: Scenario  # 枚举：
                        # NEGOTIATION_FIRST_ROUND,
                        # NEGOTIATION_BOTTOM,
                        # FAQ_GENERAL, FAQ_SHIPPING,
                        # CHITCHAT_GREETING, COMPLAINT_SOFT ...
    sub_scenario: str | None

    # 对话片段
    buyer_text: str
    seller_reply: str
    conversation_context: str  # 前 1-2 轮（可选）

    # 标签
    tags: [str]                 # ["亲切", "坚定", "幽默", "包邮"]
    product_category: str | None

    # 向量
    buyer_text_embedding: vector(1024)

    # 归属
    seller_id: str              # 'GLOBAL' 表示通用
    source: ExampleSource
    quality_score: float        # 0-1
    usage_count: int
    last_used_at: datetime

    # 状态
    is_active: bool
    created_at, updated_at

    # 审计
    added_by: str
    origin_conversation_id: str | None
}

class ExampleSource(Enum):
    MANUAL = "MANUAL"
    FEISHU_ONE_CLICK = "FEISHU_ONE_CLICK"
    IMPORT = "IMPORT"
    SYSTEM_SEED = "SYSTEM_SEED"
```

## 6.4 核心算法：动态选取

**三层筛选**：

**Layer 1：场景硬过滤**

```sql
WHERE scenario = :scenario
  AND is_active = true
  AND seller_id IN (:seller_id, 'GLOBAL')
  AND (:product_category IS NULL OR product_category = :product_category)
  AND (:tags_filter IS NULL OR tags && :tags_filter)
```

**Layer 2：向量相似度召回**

```sql
ORDER BY buyer_text_embedding <=> :query_embedding
LIMIT 20
```

**Layer 3：多样性选择（MMR）**

不是 top-k，而是 top-k + 多样性重排：

```
1. 召回 top-20 按相似度
2. 选 top-1
3. 剩 19 个按"与已选集合的最小相似度"排序，选最不相似的 → top-2
4. 重复直到 k
```

保证 few-shot 既相关又多样。

## 6.5 示例来源与质量

**源 1：系统初始种子**
- 上线前手工录入 30-50 条
- 场景均衡
- `quality_score = 0.8`, `source = SYSTEM_SEED`

**源 2：飞书一键沉淀（飞轮核心）**
- 卖家修改 AI 建议发送
- 点"加入示例库" → 调用 add_example
- 自动填 scenario（从 StructuredContext.intent）
- seller_reply = 修改后文本
- `quality_score = 0.9`, `source = FEISHU_ONE_CLICK`

**源 3：自动挖掘（Phase 2）**
- 定期扫对话归档
- 找"卖家改了 AI 建议并发送，24h 内成交"的 case
- 作为候选推送到管理后台，人工复核后入库

**质量衰减机制**：
- 被选中但对话未成交 → `quality_score -= 0.02`
- 被选中且成交 → `quality_score += 0.01`（缓慢上升）
- < 0.3 自动下线

## 6.6 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 检索维度 | buyer_text 的 embedding | 对"相似买家问题"最直接 |
| 示例粒度 | 单轮为主 | 多轮占 token |
| 共享范围 | seller_id + GLOBAL | 通用共享，特定隔离 |
| 多样性 | MMR 重排 | 避免 few-shot 单一 |
| 质量打分 | 人工 + 自动反馈 | 纯自动不准 |
| 数量 | k=3 默认 | 多干扰，少不足 |

## 6.7 数据库表

```sql
CREATE TABLE gold_examples (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scenario TEXT NOT NULL,
  sub_scenario TEXT,
  buyer_text TEXT NOT NULL,
  seller_reply TEXT NOT NULL,
  conversation_context TEXT,
  tags TEXT[] DEFAULT '{}',
  product_category TEXT,
  buyer_text_embedding vector(1024),
  seller_id TEXT NOT NULL,
  source TEXT NOT NULL,
  quality_score REAL DEFAULT 0.7,
  usage_count INT DEFAULT 0,
  last_used_at TIMESTAMPTZ,
  is_active BOOLEAN DEFAULT TRUE,
  added_by TEXT,
  origin_conversation_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ge_scenario ON gold_examples(scenario, seller_id, is_active);
CREATE INDEX idx_ge_tags ON gold_examples USING GIN(tags);
CREATE INDEX idx_ge_embedding ON gold_examples
  USING hnsw (buyer_text_embedding vector_cosine_ops);
```

## 6.8 测试要求

- 选取函数：query 返回示例都在正确 scenario
- 多样性：k=3 时三条 cosine 距离平均 > 0.3
- 空库兜底：seller 库空 → 回退 GLOBAL
- 一键沉淀后立即可选取

## 6.9 实施指引

**实施顺序**：
1. 建表（含 HNSW 索引）
2. 导入初始种子数据（YAML 格式，便于编辑）
3. 实现 CRUD
4. 实现 get_few_shot（含 MMR）
5. 实现质量打分更新
6. 写管理 CLI（批量操作）

**容易踩坑**：
- 刚插入时 embedding 可能 NULL（异步算）：查询 `WHERE embedding IS NOT NULL`
- MMR 不要重复算相似度（缓存已选集合向量）
- scenario 枚举和其他模块对齐（放 `shared/enums.py`）

**不要做**：
- ❌ 不训练模型
- ❌ 不直接生成回复
- ❌ 不 hardcode scenario 到代码（用配置）

**输出物**：
- `backend/app/modules/examples/`
- `schema.py`、`repo.py`、`service.py`（get_few_shot）
- `seed/`（初始种子 YAML）
- `tests/examples/`
