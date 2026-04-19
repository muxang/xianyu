# 模块 10：LangGraph 主图编排

## 10.1 目标与边界

**做什么**：
- 实现主 StateGraph，把 ContextBuilder、Router、各子图、AutomationDecision、合规、出站调节串起来
- 配置 **PostgresSaver** checkpoint，支持中断恢复
- 实现 **HITL 的 `interrupt` 点**（L2 场景）
- 提供统一的"处理一条消息"入口

**不做什么**：
- 不实现子图内部逻辑（各子图模块的事）
- 不直接和通信层/DB 打交道（用注入的服务）

**本质**：**全局编排器**。把 12 个模块连成一条可执行的流水线。

## 10.2 图结构

```
START
  ↓
ContextBuilder (调理解层模块 3)
  ↓
Router (条件边，按 intent 分流)
  ├─ FAQ / PRODUCT_INQUIRY → faq_subgraph
  ├─ NEGOTIATION → negotiation_subgraph
  ├─ AFTER_SALES / COMPLAINT → l1_subgraph（直接产出 L1）
  ├─ ORDER_STATUS → fallback_subgraph（兜底话术）
  ├─ CHITCHAT → chitchat_subgraph
  ├─ INTENT_UNCLEAR → clarify_subgraph（反问）
  └─ OTHER → fallback_subgraph
  ↓
AutomationClassifier (模块 8)
  ↓
ComplianceCheck (模块 8)
  ├─ BLOCK → 换兜底话术或降级 L1
  ├─ DOWNGRADE → 降一级 automation_level
  └─ PASS → 继续
  ↓
条件边：
  ├─ L4 → OutboundShaper → ENQUEUE_DIRECT → END
  ├─ L3 → EnqueueWithCountdown → END
  ├─ L2 → InterruptForReview（HITL）
  └─ L1 → NotifyOnly → END
```

## 10.3 State 结构

```python
class MainGraphState(TypedDict):
    # 输入
    inbound_message: InboundMessage
    seller_session_ctx: SellerContext

    # 过程产物
    structured_context: StructuredContext | None
    subgraph_output: SubgraphOutput | None
    automation_decision: AutomationDecision | None
    compliance_result: ComplianceResult | None
    shaped_messages: list[OutboundMessage] | None

    # 控制
    current_node: str
    error: str | None
    trace_id: str
```

## 10.4 Checkpointer

- 使用 `langgraph.checkpoint.postgres.PostgresSaver`
- 每条买家消息一个 `thread_id = conversation_id`
- 议价子图等长对话依赖 checkpoint 恢复

**关键**：
- 绝不用 `MemorySaver`（会丢状态）
- Postgres 连接池统一用 `asyncpg`
- 事务边界：每个节点独立

## 10.5 Interrupt 机制

L2 场景使用 `interrupt`：
- 图暂停，状态持久化到 checkpoint
- 消息进入 review_queue
- 飞书回调触发 `resume` + 传入决策
- 图继续：OutboundShaper → 出站 或 直接结束

**关键实现**：
```python
from langgraph.types import interrupt

async def l2_review_node(state: MainGraphState):
    review_id = await create_review(state)
    # interrupt 会暂停图
    human_decision = interrupt({
        "review_id": review_id,
        "suggestion": state["subgraph_output"].reply,
    })
    # resume 后拿到 human_decision
    return {"final_text": human_decision["text"], "action": human_decision["action"]}
```

## 10.6 子图接线

每个子图实现为独立的 StateGraph，主图通过 `StateGraph.compile()` 后注入：

```python
main_graph = StateGraph(MainGraphState)
main_graph.add_node("context_builder", context_builder_node)
main_graph.add_node("faq_sub", faq_subgraph.compile())
main_graph.add_node("negotiation_sub", negotiation_subgraph.compile())
# ...
main_graph.add_conditional_edges(
    "router", route_by_intent,
    {"FAQ": "faq_sub", "NEGOTIATION": "negotiation_sub", ...}
)
```

**每个子图返回统一的 SubgraphOutput**，便于主图后续统一处理。

## 10.7 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 编排框架 | LangGraph | 原生支持 HITL + checkpoint |
| Checkpointer | PostgresSaver | 持久化 + 可查询 |
| 状态管理 | TypedDict | 显式、类型安全 |
| 子图隔离 | 独立 StateGraph | 独立测试和迭代 |
| HITL | interrupt/resume | 原生机制 |
| 错误处理 | 节点级 try/except | 失败不整图崩 |

## 10.8 边界情况

**节点异常**：
- 捕获 + 记录 + 降级到兜底子图 → L2 人工
- 不让异常冒泡导致图崩溃

**Checkpoint 丢失**：
- 查不到 → 视为新会话开始
- 告警但不阻塞

**Interrupt 超时**（人工不响应）：
- 消息流水线的 review_state 超时兜底
- 不依赖 LangGraph 自己处理超时

**并发同一 conversation_id**：
- Checkpointer 的乐观锁（Postgres 自带）
- 冲突 → 后到的重试

## 10.9 测试要求

- 每条路径至少一个端到端测试
- Mock 所有子图，验证编排正确
- 测试 interrupt/resume 流程
- Checkpoint 持久化验证

## 10.10 实施指引

**实施顺序**：
1. 定义 MainGraphState
2. 实现 ContextBuilder 节点（调模块 3）
3. 实现 Router 条件边
4. 接线所有子图（模块 5 等）
5. 实现 AutomationClassifier / ComplianceCheck 节点（调模块 8）
6. 实现 interrupt 机制
7. 配置 PostgresSaver
8. 端到端测试

**不要做**：
- ❌ 把业务逻辑写在主图里（只做编排）
- ❌ 用 MemorySaver
- ❌ 把子图的 State 和主图共享（每个子图独立 State）

**输出物**：
- `backend/app/modules/orchestrator/`
- `main_graph.py`、`state.py`
- `nodes/context_builder.py`、`nodes/router.py`、`nodes/automation.py`、`nodes/compliance.py`
- `checkpointer.py`（PostgresSaver 配置）
- `tests/orchestrator/`
