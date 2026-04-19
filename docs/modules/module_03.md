# 模块 3：理解层（Understanding）

## 3.1 目标与边界

**做什么**：
- 把一条原始买家消息"读懂"，产出**结构化的上下文对象**（StructuredContext）
- 包括四件事：**图片理解、意图分类、实体抽取、上下文组装**

**不做什么**：
- 不做检索（模块 4）
- 不做回复生成（生成层）
- 不做路由决策（Agent 编排层）
- 不修改任何知识库或状态

**本模块的本质**：一个**幂等的、无状态的、可并行的"消息理解器"**。

**为什么是精度之源**：后面所有决策都依赖本层输出。意图分错 = 整条链路废。

## 3.2 外部契约

**上游**：
- LangGraph 主图的 `ContextBuilder` 节点

**下游**：
- LiteLLM 网关（Qwen-Flash 做分类、Qwen-VL 做图片理解）
- Postgres（读卖家配置、商品缓存、买家画像、对话历史）
- Redis（读近期对话缓存）

**核心 API**：

```python
async def understand(
    inbound_msg: InboundMessage,
    seller_session_ctx: SellerContext,
) -> StructuredContext
```

**StructuredContext 完整 schema**：

```python
StructuredContext {
    # 本次消息
    current_message: {
        text: str | None
        image_url: str | None
        image_understanding: ImageUnderstanding | None
        combined_text: str  # 图片理解 + 原始文本拼接
        timestamp_ms: int
        message_id: str
    }

    # 意图识别
    intent: {
        primary: IntentType                 # 枚举，见下方
        secondary: IntentType | None
        confidence: float                    # 0-1
        sentiment: SentimentType
        urgency: UrgencyLevel
    }

    # 实体抽取
    entities: {
        product_refs: [{item_id, item_title_guess, confidence}]
        price_mentions: [float]
        quantity: int | None
        time_refs: [str]
        contact_info_detected: bool
        custom_fields: dict
    }

    # 对话上下文
    conversation: {
        conversation_id: str
        buyer_id: str
        history: [ConversationTurn]
        is_first_contact: bool
        last_seller_reply_ago_sec: int | None
        turn_count: int
    }

    # 商品上下文
    product_context: {
        current_item: ProductInfo | None
        candidate_items: [ProductInfo]
    }

    # 买家画像
    buyer_profile: {
        buyer_id: str
        first_seen_at: datetime | None
        total_conversations: int
        negotiation_history: {attempts, successes, avg_final_discount}
        deal_count: int
        tags: [str]
        risk_flags: [str]
    }

    # 卖家上下文
    seller_context: {
        seller_id: str
        persona: PersonaConfig
        business_hours: BusinessHoursConfig
        automation_level_default: AutomationLevel
        negotiation_policy: NegotiationPolicy
        silenced_topics: [str]
    }

    # 追踪
    trace_id: str
    built_at: datetime
    sub_step_latencies: dict
}
```

**IntentType 枚举**：

```python
class IntentType(Enum):
    FAQ = "FAQ"                         # 通用问题
    PRODUCT_INQUIRY = "PRODUCT_INQUIRY" # 具体商品咨询
    NEGOTIATION = "NEGOTIATION"         # 议价
    ORDER_STATUS = "ORDER_STATUS"       # 订单/物流
    AFTER_SALES = "AFTER_SALES"         # 售后
    COMPLAINT = "COMPLAINT"             # 投诉、纠纷
    CHITCHAT = "CHITCHAT"               # 闲聊
    INTENT_UNCLEAR = "INTENT_UNCLEAR"   # 需要澄清
    OTHER = "OTHER"
```

## 3.3 内部核心设计

### 3.3.1 处理流程

```
Step 1: 并行启动
  ├─ ImageUnderstand（仅当有图片）  ← LLM 调用
  └─ LoadContextData                 ← DB 调用
      ├─ 对话历史（Redis 优先，miss 走 DB）
      ├─ 买家画像
      └─ 卖家配置

Step 2: 图片理解完成 → 组装 combined_text

Step 3: 意图分类 + 实体抽取（单次 LLM 调用）

Step 4: 基于 entities 加载商品上下文

Step 5: 组装 StructuredContext，返回
```

**关键**：Step 1 并行，节省 200-500ms

### 3.3.2 图片理解

**需识别的图片类型**（按闲鱼场景）：
1. 商品展示图（买家指着商品问）
2. 实物照片（买家发请求鉴别）
3. 破损/瑕疵照（售后 → L1）
4. 聊天截图
5. 二维码、联系方式（→ 风控）
6. 表情包/梗图（忽略）

**VL Prompt 结构**（单次 JSON 输出）：

```
请分析图片：
1. image_type: 商品展示 / 实物 / 破损 / 聊天截图 / 二维码 / 表情包 / 其他
2. description: 一句话描述
3. key_info: 关键信息列表
4. has_contact_info: bool
5. has_damage_signal: bool
6. text_in_image: OCR 文字
输出 JSON。
```

**模型**：
- 主用 Qwen-VL-Max
- 备用 GLM-4V

**不让 VL 做意图判断**，只报告图片内容；意图判断交给文本模型。

### 3.3.3 意图分类 + 实体抽取（单次调用）

**合并的理由**：同样输入、Qwen-Flash 支持 JSON 模式、省一次调用。

**Prompt 要点**：
- **Few-shot**：6-8 条真实闲鱼对话 (消息, 历史) → 分类示例
- **历史上下文**：只给最近 2-3 轮
- **confidence 自省**："不确定时 < 0.7"
- **JSON 模式强制**

**实体抽取清单**：
- `product_refs`：显式 item_id / 商品名提示 / 数字代号
- `price_mentions`：数字 + "块/元/¥" 上下文
- `contact_info`：微信/QQ/手机号 — **用正则不用 LLM**
- 量词、时间词

**contact_info 正则**：
- 手机号：`1[3-9]\d{9}`
- QQ：纯数字 5-12 位 + 上下文关键词
- 微信号：英文/数字开头 + "加我"等关键词
- 避免误伤（iPhone 12 Pro Max 256）

### 3.3.4 对话历史加载

**分级加载**：
- 最近 1 轮：用于意图分类（省 token）
- 最近 5 轮：用于实体抽取
- 最近 10-20 轮：用于生成时（不在本层决定）

**缓存**：
- `conversation_cache:{conv_id}` → Redis LIST, TTL 24h
- Miss → DB，回填缓存

**ConversationTurn 数据结构**：

```python
{
    "role": "buyer" | "seller" | "ai_suggestion",
    "text": str,
    "image_summary": str | None,
    "timestamp": int,
    "intent": IntentType | None,
    "was_ai_generated": bool,
    "was_modified": bool,
}
```

### 3.3.5 商品上下文解析

**情况 1：明确引用**
- 闲鱼会话本身绑定 item_id
- 直接查 products 表

**情况 2：模糊引用**
- 看 entities.product_refs 提示
- 从卖家 products 按标题关键词匹配
- 1 个 → current_item；多个 → candidate_items

**情况 3：无引用**
- 继承对话历史里最近的 item_id
- 都没有 → current_item = None

**实现**：
- 商品表建 GIN 索引 `(seller_id, to_tsvector(title))`
- 用 pg_trgm 或分词，不用 LIKE

### 3.3.6 买家画像

**加载**：从 `buyer_profiles` 表直接读，主键 `(seller_id, buyer_id)`

**更新**：**不在本模块做**（对话归档模块异步更新）

**字段计算口径**：
- `avg_final_discount`：成交价 / 原始标价，跨成功议价会话平均
- `tags`：规则生成（总会话 > 5 → "熟客"；议价 3+ 次 0 成交 → "议价型"）
- `risk_flags`：人工标记或规则触发

**冷启动**：新买家画像全空，下游按 `is_first_contact=True` 保守策略

## 3.4 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 意图+实体合并 | 单次 JSON 输出 | 省钱、减延迟 |
| 图片理解独立 | 单次 VL + 结构化输出 | 边界清晰 |
| 分类粒度 | 9 个类别 | 细则混乱，粗则分不开 |
| contact_info | 正则不 LLM | 准确率高、成本 0 |
| 历史加载 | Redis + DB 两级 | 高频操作必快 |
| 画像更新 | 异步，不本层 | 保持无状态 |

## 3.5 边界情况

**图片下载失败**：
- `image_understanding = None`，不阻塞
- combined_text 只有原始文本
- 日志 WARN 不 Error

**VL 超时或不可用**：
- 5 秒超时放弃
- 降级处理文本
- 下游识别"有图无理解" → L2

**分类低置信度 < 0.6**：
- `primary = INTENT_UNCLEAR`
- 下游走澄清子图或 L2

**JSON 解析失败**：
- 重试 1 次，prompt 强调"仅 JSON 无 markdown"
- 仍失败 → 默认 StructuredContext (intent=UNCLEAR, confidence=0) → L2

**DB/Redis 不可用**：
- DB 挂：抛 Error
- Redis 挂：绕过，直查 DB（降级）

**对话历史加载超时**：
- 500ms 内未返回 → 空历史兜底
- 理由：1 秒延迟不可接受

**空消息（只有图）**：
- combined_text = image description
- 图片理解也失败 → intent = OTHER → L2

## 3.6 测试要求

**单元测试**：
- 每种意图典型样本（5-10 个/类），准确率 > 85%
- contact_info 正则所有变形
- 模糊引用解析
- JSON 解析失败降级

**集成测试**：
- 完整 understand() 端到端（MockLLM）
- VL + 文本并行加速（耗时 < 串行 * 0.7）
- Redis miss 回填正确性

**准确率基准测试（必须建）**：
- 人工标注 100-200 条真实闲鱼对话（脱敏）
- 覆盖所有意图类型 + 边界样本
- **整体准确率 > 88%，每类 precision > 85%**

**LLM Judge 评估**：
- 用 Qwen-Max / Claude 对 understand() 输出打"合理性分"
- 低分案例人工 review

## 3.7 实施指引

**实施顺序**：
1. 定义所有 schema（枚举、dataclass、TypedDict）
2. 写 prompt 模板（单独文件 `prompts/`）
3. 实现正则部分（contact_info）
4. 实现 LLM 调用层（LiteLLM、重试、降级、超时）
5. 实现上下文加载（Redis + DB 两级缓存）
6. 实现主流程 understand()
7. 写测试

**Prompt 迭代方式**：
- 所有 prompt 存 `prompts/`，文件名版本化（`intent_classify_v1.md`, `v2.md`）
- 配置指定当前版本
- 每次迭代跑准确率基准
- 记录版本指标，便于回滚

**容易踩坑**：
- **JSON Mode 带 markdown 围栏**：先 `strip('`')`
- **枚举值大小写**：`.upper()` 或 pydantic `use_enum_values`
- **历史 token 超限**：硬上限（最近 5 轮 + 2000 token）
- **Postgres 中文分词**：`zhparser` 或 jieba 预分词，不用默认
- **并行异常**：`asyncio.gather(return_exceptions=True)`
- **Few-shot 外部化**：YAML，便于编辑

**不要做**：
- ❌ 不用一个大 LLM 调用做所有事
- ❌ 不调用主生成模型（只能用 Qwen-Flash 级）
- ❌ 不做任何写操作（纯函数）
- ❌ 不依赖 Redis 必须命中
- ❌ 失败不能中断流程，必有降级

**输出物**：
- `backend/app/modules/understanding/` 目录
- `schema.py`、`understand.py`
- `image_understanding.py`、`intent_classifier.py`
- `context_loader.py`、`entity_rules.py`
- `prompts/`、`test_cases/`
- `tests/understanding/`

**审计重点**：
- 所有 LLM 调用都有超时
- 所有 LLM 调用都有降级路径
- 并行异常隔离正确
- Few-shot 覆盖所有意图
- 测试集覆盖 INTENT_UNCLEAR、COMPLAINT、OTHER 边界
- 本层无副作用（不写 DB、不改 Redis 除缓存回填）
