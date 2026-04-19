# 模块 2：消息流水线（Redis Streams）

## 2.1 目标与边界

**做什么**：
- 把"入站消息事件"和"业务处理"解耦
- 把"业务生成的回复"和"真实发送"解耦，支持**延迟投递**（模拟打字节奏）
- 支持"待卖家审核"的消息中转（L2 辅助模式）
- 提供可靠的消息持久化、重试、死信能力

**不做什么**：
- 不做业务判断
- 不做速率控制（预处理层的事）
- 不直接和 WebSocket 交互（Session 层的事）

**本模块的本质**：为本系统定制的轻量消息总线。

## 2.2 外部契约

**上游生产者**：
- `SellerSession._listen_loop` → 发布 `InboundMessage` 到 `in_queue`
- `LangGraph Agent` → 发布 `OutboundMessage` 到 `out_queue` 或 `review_queue`
- `飞书工作台回调` → 发布 `OutboundMessage` 到 `out_queue`

**下游消费者**：
- 预处理层 ← 消费 `in_queue`
- `SellerSession._send_loop` ← 消费 `out_queue` 的到期消息
- 飞书推送服务 ← 消费 `review_queue`

**核心 API**：

```python
# 入站
async def publish_inbound(msg: InboundMessage) -> str  # stream_id
async def consume_inbound(seller_id: str, consumer_name: str) -> AsyncIterator[StreamMessage[InboundMessage]]
async def ack_inbound(seller_id: str, stream_id: str) -> None

# 出站（带延迟）
async def publish_outbound(msg: OutboundMessage, send_at: datetime) -> str
async def consume_due_outbound(seller_id: str) -> AsyncIterator[StreamMessage[OutboundMessage]]
async def ack_outbound(seller_id: str, stream_id: str) -> None

# 审核
async def publish_review(msg: ReviewMessage) -> str
async def consume_review(seller_id: str) -> AsyncIterator[StreamMessage[ReviewMessage]]
async def resolve_review(review_id: str, action: ReviewAction, modified_text: str | None = None) -> None
```

**约束**：
- 消息必须可 JSON 序列化
- 所有队列按 `seller_id` 隔离
- 消息不丢失（至少一次语义）；重复交付由消费方幂等

## 2.3 内部核心设计

### 2.3.1 Redis Streams 模式选型

**为什么用 Streams 而不是 Pub/Sub 或 List**：
- Pub/Sub 不持久化
- List (LPUSH/BRPOP) 无消费者组、无回放
- Streams 有消费者组、消息 ACK、持久化、回放

**键命名规范**（常量文件）：

```
in_queue:{seller_id}                    # 入站流
out_queue:{seller_id}                   # 出站流
out_queue:delayed:{seller_id}           # 延迟待发（Sorted Set）
review_queue:{seller_id}                # 审核流
review_state:{review_id}                # 审核状态 KV (Hash)
msg_dedup:{seller_id}:{message_id}      # 去重 SET
dlq:{original_stream_key}               # 死信
```

**消费者组**：
- `in_queue:{seller_id}` → `preprocessor_group`
- `out_queue:{seller_id}` → `sender_group`
- `review_queue:{seller_id}` → `feishu_group`

### 2.3.2 延迟投递实现

**方案：Sorted Set + 调度器**

1. `publish_outbound(msg, send_at)`：
   - 序列化为 JSON，以 UUID 为 member
   - `ZADD out_queue:delayed:{seller_id} {send_at_ts} {payload}`

2. `DelayedDispatcher` 协程（每秒）：
   - 对每个 active seller 执行 `ZRANGEBYSCORE ... 0 now()`
   - 拿到已到期消息，批量 `XADD out_queue:{seller_id}`
   - `ZREM` 从延迟集合删除

3. 消费侧从 Stream 读，不关心延迟机制

**权衡**：
- 1 秒轮询 → 最差延迟 1 秒
- 不用 Redis keyspace notification（配置复杂不可靠）
- 不用外部调度器（增加依赖）

### 2.3.3 审核队列状态机

**生命周期**：

```
CREATED → PUSHED → {approved | modified | rejected | silenced | expired}
```

**`review_state:{review_id}` 数据**：

```python
{
    "seller_id": str,
    "conversation_id": str,
    "original_suggestion": str,     # AI 原文
    "context_summary": str,         # 给飞书展示的上下文摘要
    "intent": str,
    "automation_level": "L2" | "L3",
    "expire_at": int,                # 毫秒时间戳
    "status": str,                   # 见上
    "resolved_at": int | None,
    "final_text": str | None,        # 最终发出的文本
    "resolved_by": str | None,       # 解决者 ID
    "created_at": int,
}
```

**TTL**：7 天（审计回溯），之后自动清理

**超时处理**：
- L3 倒计时（默认 30s）到期 → 默认 approved
- L2 辅助（默认 10 min）到期 → 默认 rejected

### 2.3.4 流量保护

**积压监控**（每 10s）：
- `in_queue` > 100 → 告警（处理不过来）
- `review_queue` > 20 → 告警（卖家响应慢）

**账号级熔断**：
- 单 seller `in_queue` 1 分钟内 > 50 条 → 熔断
- 停止 Agent 处理该账号
- 飞书告警"可能是恶意刷屏或风控异常"

### 2.3.5 幂等性

消费者自己保证：
- 入站：`message_id` 已在 Session 层去重
- 出站：`outbound_id`（UUID）作为幂等键
- 审核：`review_id` 作为幂等键

## 2.4 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 中间件 | Redis Streams | 零新增依赖 |
| 延迟 | Sorted Set + 1s 轮询 | 简单可靠 |
| 账号隔离 | 键按 seller_id 分片 | 天然隔离 |
| 消费语义 | 至少一次 + 消费方幂等 | 不丢消息优先 |
| 审核超时 | 后台扫描兜底 | Redis TTL 无回调 |

## 2.5 边界情况

**Redis 连接断开**：
- 所有 publish 必须有重试（3 次指数退避）
- 上游缓冲（有限队列），不直接丢

**消息体过大**：
- 单条 > 256KB → 不入 Redis，只存图片 URL 或 MinIO key
- 严禁 base64 图片塞进 OutboundMessage

**PEL 堆积**：
- 消费者挂了没 ACK → PEL 无限增长
- 启动时 XCLAIM 超过 1 小时未 ACK 的消息
- 超过 3 次失败 → 转死信 `dlq:{stream_key}`

**时钟漂移**：
- Sorted Set 用 Unix 时间戳
- 偏移 > 5s 告警（NTP 同步必须）

**seller 删除但队列有残留**：
- `purge_queues(seller_id)` 清理函数

## 2.6 测试要求

**单元测试**：
- 所有 publish/consume/ack 基础行为
- 延迟消息：5s 后消息可消费
- 审核状态机：所有转移路径
- 幂等性：重复 publish 只收一次

**集成测试**（testcontainers 起真实 Redis）：
- 并发顺序（同 seller 按 timestamp 有序）
- 消费者崩溃后 PEL 回收
- 死信队列手动回放

**混沌测试**：
- 随机 kill Redis 验证恢复
- 灌入 1000 条验证无丢失

**验收**：
- 端到端延迟 p99 < 100ms
- 延迟消息准时性 p99 < 1.5s
- 零丢失（Redis 可用前提）

## 2.7 实施指引

**实施顺序**：
1. 定义 schema：`InboundMessage`、`OutboundMessage`、`ReviewMessage`、`StreamMessage`、异常、常量
2. 实现 `InboundStream`
3. 实现 `OutboundStream` + 延迟调度（先实现立即投递路径跑通测试，再加 DelayedDispatcher）
4. 实现 `ReviewStream` + 状态机（先 CRUD，再超时兜底）
5. 实现 `QueueMonitor`（独立服务，周期扫描）
6. 写集成测试（testcontainers）

**容易踩坑**：
- **XGROUP CREATE 已存在会报错**：用 `MKSTREAM` 参数或先判断
- **redis.asyncio 必须用**（不是 redis.Redis）
- **datetime 序列化**：统一用毫秒时间戳跨序列化边界
- **PEL 回收**：XCLAIM 的 min-idle-time 设合理（3600000ms）
- **Sorted Set member 必须唯一**：用 UUID，不要用内容哈希

**不要做**：
- ❌ 不引入 Celery/RQ
- ❌ 不在这一层做业务判断
- ❌ 不在 publish 里做耗时操作
- ❌ 不让消费方直连 Redis

**输出物**：
- `backend/app/modules/queue/` 目录
- `interfaces.py`、`inbound_stream.py`、`outbound_stream.py`、`review_stream.py`
- `delayed_dispatcher.py`、`queue_monitor.py`、`constants.py`
- `tests/queue/` 单元+集成测试
