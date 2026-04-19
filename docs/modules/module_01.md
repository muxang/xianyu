# 模块 1：XianYuApis 适配器 + SellerSession

## 1.1 目标与边界

**做什么**：
- 把 XianYuApis 封装为系统可管理的服务层
- 支持多账号并行（每账号独立 WebSocket 长连接）
- 管理 Cookie 生命周期（加密存储、有效性探测、失效告警、热更新）
- 统一入站/出站消息的标准格式，与下游业务解耦

**不做什么**：
- 不做业务判断（不决定"该不该回"、"回什么"）
- 不做速率控制（那是预处理层的事）
- 不做 LLM 调用
- 不直接操作业务数据库表（除了 sellers 和 cookies 的管理）

**本模块的本质**：一个**事件驱动的通信基础设施**——把闲鱼的 WebSocket 消息流转换成系统内的标准事件流，同时把系统的标准出站消息转换回闲鱼协议。

## 1.2 外部契约

**上游生产者**：
- 系统启动时：从 Postgres 加载 sellers → 逐个启动 SellerSession

**下游消费者**：
- 消息流水线（模块 2）：接收标准化的 InboundMessage
- 飞书推送（模块 7-A）：Cookie 失效、风控告警等事件的上报

**核心抽象接口（本模块的对外契约）**：

```python
class MessagingAdapter(ABC):
    """平台通信适配器抽象基类，XianYuApis 是第一个实现"""

    @abstractmethod
    async def connect(self) -> None:
        """建立长连接，鉴权完成"""

    @abstractmethod
    async def disconnect(self) -> None:
        """优雅断开"""

    @abstractmethod
    async def listen(self) -> AsyncIterator[InboundMessage]:
        """异步迭代入站消息"""

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> bool:
        """发送出站消息，返回是否成功"""

    @abstractmethod
    async def fetch_history(self, buyer_id: str, limit: int = 20) -> list[InboundMessage]:
        """拉取历史消息"""

    @abstractmethod
    async def fetch_product(self, item_id: str) -> dict:
        """查商品详情"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查，返回 Cookie 是否仍然有效"""
```

**标准数据类**：

```python
class MessageType(Enum):
    TEXT = "text"
    IMAGE = "image"
    SYSTEM = "system"

@dataclass
class InboundMessage:
    message_id: str
    seller_id: str
    buyer_id: str
    conversation_id: str
    item_id: str | None
    type: MessageType
    text: str | None
    image_url: str | None
    timestamp: int  # 毫秒
    raw: dict  # 原始平台数据

@dataclass
class OutboundMessage:
    seller_id: str
    conversation_id: str
    buyer_id: str
    type: MessageType
    text: str | None = None
    image_bytes: bytes | None = None
    outbound_id: str = field(default_factory=lambda: str(uuid.uuid4()))
```

## 1.3 XianYuApis 实现

**核心实现要点**：

1. **WebSocket 握手**：
   - 复用 XianYuApis 的 sign 签名算法（静态 JS）
   - 带 Cookie 连接
   - 发送初始注册包
   - 处理 ping/pong

2. **消息解码**：
   - base64 解码 + Protobuf 反序列化
   - 复用 `XianYuApis/utils/goofish_utils.py` 的工具函数

3. **消息标准化**（`_normalize` 方法）：
   - 过滤无意义事件（输入状态、已读回执、系统提示）
   - 字段重命名对齐到 InboundMessage schema
   - 识别 text/image 类型

4. **发送封装**：
   - 构造 Protobuf 包 + 签名
   - 支持文字消息和图片消息
   - 图片需先上传得到 URL 再发文本 + URL

5. **断线重连**：
   - 指数退避：1s、2s、4s、8s、16s、32s、最多 60s
   - 加随机抖动（±20%）避免雪崩
   - 重连后自动重新订阅

6. **商品查询**：
   - 调用 XianYuApis 的 `get_product_detail` HTTP 接口
   - 带 Cookie 鉴权
   - 解析返回结构

7. **健康探测**：
   - 调用一个轻量 HTTP 接口（如 `getUserInfo`）验证 Cookie
   - 不要用 WebSocket 心跳，因为 WS 层可能还连着但业务层已失效

## 1.4 SellerSession：单账号的生命周期管理器

**状态机**：

```python
class SessionStatus(Enum):
    INITIALIZING = "initializing"
    ACTIVE = "active"
    PAUSED = "paused"              # 人为暂停
    COOKIE_EXPIRED = "cookie_expired"
    RISK_CONTROLLED = "risk_controlled"  # 平台风控
    ERROR = "error"
```

**状态转移规则**：
- `INITIALIZING → ACTIVE`：connect 成功
- `ACTIVE → {PAUSED, COOKIE_EXPIRED, RISK_CONTROLLED, ERROR}`：各种异常
- `PAUSED → ACTIVE`：手动 resume
- `COOKIE_EXPIRED → ACTIVE`：更新 Cookie 后
- `RISK_CONTROLLED`：**不能自动恢复**，必须人工判断

**三个并发循环**（asyncio.gather 启动）：

1. **监听循环 (_listen_loop)**：
   - `async for msg in adapter.listen()`
   - 去重检查（Redis SETNX）
   - 调用 `inbound_publisher.publish(msg)` 推到 Redis in_queue
   - 统计 `stats["inbound_count"]`

2. **发送循环 (_send_loop)**：
   - `async for outbound in outbound_consumer.consume(seller_id)`
   - 调用 `adapter.send(outbound)`
   - 失败 → 重试 3 次 → 仍失败报告 `_handle_send_failure`
   - 成功 → ACK + 统计 `stats["outbound_count"]`

3. **健康循环 (_health_loop)**：
   - 每 10 分钟调用一次 `adapter.health_check()`
   - 失败 → 状态变 COOKIE_EXPIRED + 飞书告警 + 退出所有循环
   - 记录 `last_heartbeat`

**关键方法**：

```python
async def run(self) -> None:
    """主入口，启动三个并发循环"""

async def pause(self, reason: str) -> None:
    """人为暂停（不断连，只停消费）"""

async def resume(self) -> None:
    """恢复"""

async def shutdown(self) -> None:
    """优雅下线"""
```

**故障隔离哲学**：每个 Session 是独立的 "actor"。任何异常都不应该传染到其他 Session 或主进程。

## 1.5 SessionManager：多账号编排

**职责**：
- 系统启动时：从 Postgres 加载所有 `status=active` 的 sellers，逐个启动 Session
- 运行时：提供 API 增删账号、暂停恢复、更新 Cookie
- Supervisor 模式：监督每个 Session，崩溃自动重启（次数限制）

**关键方法**：

```python
class SessionManager:
    sessions: dict[str, SellerSession]  # seller_id → session

    async def startup(self) -> None:
        """启动所有 active 账号"""

    async def start_session(self, seller_id: str) -> None:
        """启动单个账号"""

    async def _supervise(self, session: SellerSession) -> None:
        """
        监督协程：
        - 正常退出 → 结束
        - 异常崩溃 → 指数退避重启
        - 连续崩溃 3 次 → 标记 ERROR + 告警
        """

    async def stop_session(self, seller_id: str) -> None:
        """停止单个账号"""

    async def update_cookie(self, seller_id: str, new_cookie: str) -> None:
        """
        更新 Cookie：
        1. 停止当前 Session
        2. 写入加密的新 Cookie 到 DB
        3. 启动新 Session
        """

    def get_status(self) -> dict:
        """返回所有 Session 的状态快照"""
```

**进程内模型**：单进程 asyncio 管理所有账号。对于少量账号（< 50）完全够用，资源占用小。未来真要扩到数百账号再改 Celery Worker 模式。

## 1.6 Cookie 管理策略

**存储**：
- `cryptography.fernet.Fernet` 加密
- 加密 key 从环境变量 `COOKIE_ENCRYPTION_KEY` 读
- 表字段：`sellers.cookie_encrypted, cookie_updated_at`

**更新入口**：
- Web 工作台"更新 Cookie"表单
- 飞书命令 `/cookie seller_A update`（弹表单粘贴）
- API 内部调用 `SessionManager.update_cookie()`

**失效检测**：
- 周期：10 分钟
- 探测方法：调用 `fetch_product` 等轻量接口，HTTP 200 + 正常响应 = 有效
- 首次失效 → 标记 COOKIE_EXPIRED + 飞书告警（带"更新 Cookie"按钮）

**寿命估算**：闲鱼 Cookie 一般能维持 7-30 天。在告警卡片显示"Cookie 已用 X 天"。

## 1.7 去重机制

XianYuApis 的 WebSocket 在重连时会补推消息，必须去重。

**实现**（在 `_listen_loop` 里）：

```python
async def _is_duplicate(self, message_id: str) -> bool:
    key = f"msg_dedup:{self.seller_id}:{message_id}"
    is_new = await redis.setnx(key, "1")
    if is_new:
        await redis.expire(key, 86400)  # 24 小时
    return not is_new
```

**边界**：
- Redis 挂了 → 降级为不去重（宁可重复不可丢）
- `message_id` 不全局唯一 → 加 seller_id 前缀

## 1.8 数据库表

```sql
CREATE TABLE sellers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    xianyu_uid TEXT UNIQUE NOT NULL,
    cookie_encrypted TEXT,
    cookie_updated_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'initializing',
    feishu_open_id TEXT,
    config JSONB DEFAULT '{}',
    stats JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sellers_status ON sellers(status);
```

## 1.9 关键决策与权衡

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 抽象接口 | MessagingAdapter 基类 | 解耦平台、可 mock、易扩展 |
| 并发模型 | asyncio + 单进程 | 长连接吃协程不吃线程 |
| Session 粒度 | 每账号一个 Session | 故障隔离 |
| 崩溃恢复 | supervisor + 次数限制 | Erlang/OTP 思路 |
| Cookie 存储 | Fernet 加密 + Postgres | 安全 + 可更新 |
| 去重 | Redis SETNX + 24h TTL | 简单可靠 |
| 状态机 | 显式枚举 + 明确转移规则 | 可测试、可审计 |
| 业务解耦 | Session 只负责通信，业务走 Redis | 可独立测试和迭代 |

## 1.10 边界情况与失败模式

**WebSocket 重连后消息顺序混乱**：
- InboundMessage 带 timestamp 字段
- 下游按 timestamp 最终排序

**同一买家开多个会话**（不同商品）：
- 用 `conversation_id` 作为主键，不用 `buyer_id`

**图片下载失败**：
- `image_url` 字段保留，但标记下载状态
- 下游视觉理解模块自己处理失败情况

**Cookie 突然失效但 WS 连接还在**：
- health_check 失败 → 主动 disconnect + 标记失效
- 不依赖 WS 断开判断 Cookie 失效

**状态机死锁**：
- 所有状态转移必须明确日志
- 禁止非正常状态转移（如 ERROR → ACTIVE 没有 resume）

**多账号同 IP 出口导致风控关联**：
- 本模块不解决（代理池是扩展）
- 但记录 `stats["outbound_count"]` 等数据，上报风控模块

## 1.11 测试要求

**单元测试**：
- 状态机的所有合法/非法转移
- 去重逻辑（Redis miss / hit）
- supervisor 重启次数限制
- MessagingAdapter 的 mock 实现（用于上层测试）

**集成测试**：
- MockAdapter 模拟消息到达 → 验证入队
- 注入 Cookie 失效 → 验证状态变化 + 告警发出
- 并发多 Session 互相不干扰

**端到端测试**（可选，需要测试账号）：
- 用一个真实闲鱼小号
- 脚本自动发消息 → 验证系统能收到 InboundMessage

**测试覆盖率目标**：> 90%（这是基础模块，必须稳）

## 1.12 实施指引（给 Claude Code）

**实施顺序**：
1. 定义 schema：`MessageType`、`InboundMessage`、`OutboundMessage`、`SessionStatus`
2. 实现 `MessagingAdapter` 抽象类
3. 实现 `MockAdapter`（供上层测试用）
4. 实现 `XianyuAdapter`（调用 XianYuApis 原代码）
5. 实现 `SellerSession`（含三个循环）
6. 实现 `SessionManager`（含 supervisor）
7. 实现 Cookie 加密/解密工具
8. 实现 sellers 表和 Alembic 迁移
9. 写单元测试
10. 写集成测试

**容易踩坑**：
- WebSocket 的并发读写要加 Lock
- asyncio 任务的取消要正确处理 CancelledError
- Cookie 解密失败（key 不匹配）要明确错误提示
- Fernet key 必须 base64 urlsafe 32 字节，不要乱生成

**不要做**：
- 不要在 Session 里做业务判断
- 不要让 Session 直接调用 LLM
- 不要省略去重
- 不要忘记处理 `CancelledError`

**输出物**：
- `backend/app/modules/session/` 目录
- `adapters.py`、`session.py`、`manager.py`、`cookie_vault.py`
- `schema.py`（数据类）
- `tests/session/test_*.py`
