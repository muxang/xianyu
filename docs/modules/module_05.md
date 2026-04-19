# 模块 5：议价子图（Negotiation Subgraph）

## 5.1 目标与边界

**做什么**：
- 当意图为 NEGOTIATION 时，接管对话
- 维护**议价状态机**：轮次、出价、距底价、让步节奏
- 基于**卖家配置**生成反价建议
- 明确标注"关键轮"，触发 HITL 让卖家决策
- 适时达成"成交"或"放弃"终态

**不做什么**：
- 不做平台级价格修改（XianYuApis 不支持）
- 不实际"接受/拒绝"（卖家的事，AI 只产建议）
- 不处理议价中夹带的其他问题（主图路由）

**本质**：**带状态的多轮对话控制器**，把议价建模为"有边界、有策略、有节奏"的博弈。

## 5.2 外部契约

**上游**：LangGraph 主图 Router，intent=NEGOTIATION 时路由到此

**下游**：
- 精准检索（4）：查议价话术
- 生成层：拼 prompt
- Redis/Postgres：议价状态

**核心接口**：

```python
async def negotiation_subgraph(
    context: StructuredContext
) -> SubgraphOutput

SubgraphOutput {
    reply: str
    automation_level: AutomationLevel
    confidence: float
    rationale: str
    state_transitions: NegotiationState
    next_hint: NegotiationHint
}
```

## 5.3 核心数据结构

### NegotiationState（持久化 Postgres）

```python
NegotiationState {
    # 标识
    seller_id, buyer_id, item_id      # 复合主键

    # 价格
    original_price: Decimal
    bottom_price: Decimal              # 卖家底价，不告诉买家
    current_seller_offer: Decimal
    current_buyer_offer: Decimal | None

    # 让步历史
    offer_history: [
        {"role": "seller" | "buyer", "price": Decimal, "timestamp": int, "reason": str}
    ]

    # 轮次
    round: int
    buyer_initial_offer: Decimal | None
    max_concession: Decimal

    # 策略
    remaining_room: Decimal            # current_seller_offer - bottom_price
    concession_pace: ConcessionPace    # slow / normal / aggressive

    # 状态机
    status: NegotiationStatus

    # 时间
    started_at, updated_at, expires_at  # 24h 不活跃过期
}

class NegotiationStatus(Enum):
    ACTIVE = "ACTIVE"
    AT_BOTTOM = "AT_BOTTOM"
    PENDING_BUYER = "PENDING_BUYER"
    PENDING_SELLER = "PENDING_SELLER"
    DEAL_REACHED = "DEAL_REACHED"
    ABANDONED = "ABANDONED"
    ESCALATED = "ESCALATED"

class ConcessionPace(Enum):
    slow = "slow"        # 每轮让 0-5 元
    normal = "normal"    # 每轮让 5-15 元
    aggressive = "aggressive"  # 每轮让 15+ 元
```

### NegotiationPolicy（卖家配置）

每个商品/卖家一份：

```python
NegotiationPolicy {
    bottom_price: Decimal
    ideal_price: Decimal
    max_rounds: int                    # 默认 5

    # 让步参数
    max_concession_per_round: Decimal
    diminishing_factor: float          # 每轮递减

    # 组合让步
    value_add_options: [
        {"type": "free_shipping", "value": "包顺丰"},
        {"type": "gift", "value": "送贴膜"},
    ]

    # 升级阈值
    escalate_when:
      remaining_room_below: Decimal
      buyer_offer_below_bottom: bool
      rounds_exceed: int

    initial_response_style: str  # "firm" / "friendly" / "vague"
}
```

## 5.4 核心算法

### 5.4.1 状态转移

**进入时**：
1. 加载已有 state（key: seller_id+buyer_id+item_id）
2. 没有 → 新建（round=1，policy 默认）
3. 有 → round+1，更新最新买家出价

**转移规则**：

```
Case A：首次砍价 (round=1)
  → friendly_pushback 策略
  → 象征性让一小步 + 强调价值
  → L3 预发送

Case B：持续砍价 (round=2~3)
  → 按 policy.pace 计算让步
  → 让步 + 组合 value add
  → L3 预发送

Case C：接近底价
  → 关键轮 → L2 辅助
  → status = PENDING_SELLER

Case D：买家出价 ≤ 底价
  → "真的不能再低"
  → status = AT_BOTTOM，L2

Case E：买家出价 ≥ current_seller_offer
  → 接受或加价 → 疑似成交
  → status = DEAL_REACHED，L2 确认

Case F：超过 max_rounds
  → status = ESCALATED，L1

Case G：长时间不回应 (> 6h)
  → status = PENDING_BUYER，不主动
```

### 5.4.2 让步金额计算

```python
def calculate_concession(state, policy) -> Decimal:
    if state.round == 1:
        # 首轮小让，保持气势
        return min(
            policy.max_concession_per_round * Decimal("0.3"),
            state.remaining_room * Decimal("0.2")
        )

    # 递减让步
    base = policy.max_concession_per_round
    decay = Decimal(str(policy.diminishing_factor ** (state.round - 1)))
    proposed = base * decay

    # 不突破剩余空间一半
    max_allowed = state.remaining_room * Decimal("0.5")

    return min(proposed, max_allowed)
```

**原则**：
- **前紧后松的反面**：前期紧，后期有节奏
- **让步递减**：第一次 10 元，第二次 5 元，第三次 2 元，给买家"到底了"暗示
- **永远留余地**：关键轮前绝不碰底价

### 5.4.3 组合让步策略

**何时用**：
- 剩余空间 < 20 元
- 买家持续砍价 (round ≥ 3)
- 商品本便宜 (< 100 元)

**实现**：
- 每轮选一个未用过的 `value_add_options`
- 生成话术："价格真的不能降了～ 这样吧我给你包顺丰 你出 850 行不？"

## 5.5 Prompt 设计

**议价专用 Prompt**（不和 FAQ 共用）：

```
[System]
你是卖家 {persona.nickname}，正在和买家议价。
人设风格：{persona.style_tags}

[Context]
商品：{product.title}
标价：{original_price}
本轮买家出价：{current_buyer_offer}
上一轮你报价：{previous_seller_offer}
本轮你的决策：
  - 让步金额：{concession}
  - 新报价：{new_offer}
  - 组合 add：{value_add or None}
  - 态度：{attitude}   # 温和/坚定/为难

[议价原则]
- 不说"底价""成本"等暴露底牌
- 态度像真人：有时为难、有时坚定
- 口语化
- 不承诺"包退、保真"

[Few-shot]
{3 条金牌议价示例}

[对话历史]
{最近 3 轮}

请生成回复，不超过 50 字。
```

**为什么这样设计**：
- **策略由代码算**（5.4.2 & 5.4.3），LLM 不算让步
- LLM 只做自然语言包装
- **态度显式**：防止语气不一致（忽冷忽热大忌）
- **禁忌词明确**：防模型暴露底牌

## 5.6 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 让步计算 | 代码规则 | 可控、可解释、可回滚 |
| LLM 职责 | 只包装语言 | 策略 LLM 不可靠 |
| 关键轮阈值 | 剩余 < 10% → L2 | 底价不妥协 |
| 状态持久化 | Postgres + Redis 缓存 | 中断可恢复 |
| 多商品并行 | (buyer, item) 独立状态 | 互不干扰 |
| 过期 | 24h 不活跃归档 | 避免状态膨胀 |
| 跳出议价 | 主图 Router 决定 | 子图专注议价 |

## 5.7 边界情况

**多轮拉锯**（> max_rounds）：强制 L1

**一上来极低价**（标价 500 报 100）：
- 不走常规让步
- "兄弟这个价差太多啦 不太好谈哦～"
- L3，卖家可中断

**买家出价高于卖家**（误操作/主动加价）：
- 视为接受
- "哎那就这个价 咱成交了哈"
- L2 让卖家确认

**状态数据损坏**（底价 NULL）：
- Fail-safe：退出议价 → L2 卖家处理

**同一买家不同商品**：
- 主键 (seller_id, buyer_id, item_id)
- 互不干扰

**买家切换商品**："那这个呢"指另一个：
- 主图 ContextBuilder 更新 current_item
- 子图检测 item_id 变化 → 为新商品建 state

**商品已售/下架但仍在砍**：
- 查 products.status
- "这个已经卖出去了哈" → L2

**无 NegotiationPolicy**：
- 系统默认（温和、保守）
- 告警提示卖家配置

## 5.8 测试要求

**单元**：
- 让步计算：round=1,2,3...，不同 remaining_room
- 状态转移：每种路径
- Policy 加载兜底

**集成**（MockLLM）：
- 完整 5 轮议价，每轮输出合理
- 中途接近底价自动 L2
- 买家长不回应自动归档

**仿真测试（最有价值）**：
- 写"模拟买家" agent（LLM 扮演砍价买家）
- 议价子图 vs 模拟买家对打 50 局
- 统计：平均成交价、成交率、平均轮数
- **目标**：成交价 ∈ [ideal_price, bottom_price] 的比例 > 80%

## 5.9 实施指引

**实施顺序**：
1. 定义 schema（枚举、dataclass）
2. NegotiationPolicy 加载 + 默认
3. NegotiationState CRUD（PG + Redis）
4. 让步计算 + 状态转移规则（纯函数易测）
5. Prompt 构造 + LLM 调用
6. 主入口 negotiation_subgraph
7. **仿真测试（最重要）**

**容易踩坑**：
- 价格统一 `Decimal`，不 float
- 状态更新用事务（读 → 算 → 写）
- 买家出价从 entities.price_mentions 提取时过滤无关数字（"3 个"、"2024"）
- 组合让步用过的 add 要记录，别重复

**不要做**：
- ❌ 让 LLM 决定让步金额
- ❌ 底价写进 prompt
- ❌ 一套 policy 应对所有商品

**输出物**：
- `backend/app/modules/negotiation/`
- `schema.py`、`state_repo.py`、`policy.py`
- `engine.py`（让步计算 + 状态转移）
- `subgraph.py`（主入口）
- `prompts/negotiation.md`
- `tests/negotiation/`（含 simulation）
