# 模块 7-A：飞书推送与快速决策

## 7-A.1 目标与边界

**做什么**：
- 通过飞书机器人实时触达卖家
- L2/L3 场景的**卡片级快速决策**（发送/修改/拒绝/静默）
- 系统告警（Cookie 失效、风控、队列积压、L1）
- 卖家的**管理命令**（暂停账号、查状态等）
- 提供**跳转 Web 工作台**的深链，承接复杂场景

**不做什么**：
- 不管理买家会话列表（Web 工作台负责）
- 不存对话历史、不提供历史回溯（Web 负责）
- 不创建买家专属群（已废弃方案 B）
- 不做 Web 级别 UI 和配置

**本质**：**通知 + 快捷操作层**。飞书只做它擅长的两件事——随时随地触达、按钮完成极简操作。复杂交互让路给 Web 工作台。

## 7-A.2 外部契约

**上游**：
- review_queue 消费者 → 飞书卡片
- 告警系统 → 飞书卡片
- Web 工作台：管理操作通知（"你在 Web 暂停了 A 账号"同步）

**下游**：
- 飞书开放平台 API
- 飞书回调 webhook
- 内部 API：resolve_review、pause_session、update_cookie 等

**核心 API**：

```python
async def push_review_card(
    review_id: str,
    seller_id: str,
    card_data: ReviewCardData,
) -> PushResult

async def push_alert(
    alert_type: AlertType,
    seller_id: str | None,
    payload: AlertPayload,
) -> PushResult

async def handle_callback(
    event: FeishuCallbackEvent,
) -> CallbackResponse

async def send_command_response(
    command: str,
    result: Any,
    to: str,  # open_id or chat_id
) -> None
```

## 7-A.3 架构：两个聊天载体

```
1. 主推送会话（机器人 ↔ 卖家私聊）
   用途：审核卡片、消息通知
   特点：按时间顺序消息流

2. 管理群（机器人 + 卖家 [+ 未来协作者]）
   用途：告警、命令、运维
   特点：严肃事件专用
```

**理由**：
- 主推送会话高频轻量
- 管理群低频重要（告警必须醒目）
- 未来多人协作时管理群自然扩展

## 7-A.4 卡片设计

### 审核卡片（核心）

```
┌──────────────────────────────────────────────┐
│ 🟡 L2 辅助 · 议价(第3轮) · 置信 85%           │
│ ──────────────────────────────────────    │
│ 📦 iPhone 14 Pro · 256G 深空黑                │
│ 👤 买家小张（熟客·议价型）                     │
│ ──────────────────────────────────────    │
│ 💬 买家说：                                   │
│   "800 真的不能卖吗 我是诚心要的"              │
│ ──────────────────────────────────────    │
│ 🤖 AI 建议：                                  │
│   "兄弟 800 真走不了 我亏本的"                │
│   "这样 850 我包顺丰给你发 你看行不？"         │
│ ──────────────────────────────────────    │
│ 💡 当前状态：剩余空间 30 元，接近底价          │
│    让价历史：899 → 880 → 860                  │
│ ──────────────────────────────────────    │
│ [✅ 发送] [✏️ 修改] [❌ 拒绝]                 │
│ [🔕 转静默] [⭐ 加示例库] [🔗 在工作台打开]    │
└──────────────────────────────────────────────┘
```

**关键字段**：
- 头部色条按 level：L2 黄、L3 橙、L4 绿、L1 红
- 买家标签：buyer_profile 摘 2-3 个
- 议价状态：仅议价场景
- 两排按钮：主 + 辅

**L3 倒计时**：
- 头部："🟠 L3 预发送 · 30 秒后自动发送"
- 每 10s 更新卡片
- 到期自动 approved，卡片更新为"✅ 已自动发送"
- 期间按钮保持可用

### 通知卡片（L4 轻量版）

```
┌──────────────────────────────────────────────┐
│ 🟢 已自动回复 · 买家小张                       │
│ "兄弟 这款还在哈 欢迎来聊"                     │
│                          [🔗 查看详情]        │
└──────────────────────────────────────────────┘
```

- 更小、无按钮
- 默认不触发消息通知（`notify: false`）

**通知分级**：
- L1/L2：强提醒（声音+震动）
- L3：普通提醒
- L4：静默

### 告警卡片

**Cookie 失效**：
```
🔴 Cookie 失效
账号：seller_A（阿水的二手店）
失效时间：刚刚
状态：已自动暂停
[📋 更新 Cookie] [🔗 管理后台]
```

**风控触发**：
```
⚠️ 风控告警
账号：seller_A
类型：消息频率异常（1 分钟 15 条）
建议：暂停 2 小时
[⏸ 暂停 2 小时] [⏸ 暂停 24 小时] [🔕 忽略]
```

**队列积压**：
```
⚠️ 消息积压
队列：in_queue · seller_A
积压：85
持续：10 分钟
[📊 查看监控] [🔕 忽略 1 小时]
```

**L1 必须处理**：
```
🔴 需要您处理
账号：seller_A · 买家小王
原因：疑似售后投诉
买家原话："东西到了是坏的"
[📱 去闲鱼处理] [🔗 在工作台查看]
```

### 命令响应卡片

```
📊 系统状态
━━━━━━━━━━━━━━━━━━━━━
账号 seller_A：🟢 正常
  · 今日消息：132 条
  · 采纳率：72%
  · Cookie 剩余：18 天
账号 seller_B：🟡 暂停中
  · 原因：风控告警
  · 暂停剩余：1h42m
━━━━━━━━━━━━━━━━━━━━━
```

## 7-A.5 按钮行为规范

统一 callback 格式：

```json
{
  "action": "review_action",
  "action_type": "approve | modify | reject | silence | promote_example | open_workspace",
  "review_id": "uuid",
  "seller_id": "seller_A",
  "metadata": { ... }
}
```

**处理流程**：

```
卖家点按钮
  ↓ 飞书 POST webhook
  ↓ 验签
  ↓ 幂等检查（review_state.status 是否终态）
    ├─ 终态 → 卡片更新"已处理过了"
    └─ PUSHED → 继续
  ↓
按 action_type 执行：
  ├─ approve    → resolve_review(approved)
  ├─ modify     → open_form 响应
  ├─ reject     → resolve_review(rejected)
  ├─ silence    → 更新 buyer_profile 静默 + resolve_review(silenced)
  ├─ promote    → add_gold_example（异步）
  └─ open_ws    → 返回跳转链接
  ↓
更新卡片 → 返回 200
```

**关键**：
- 飞书 webhook 3 秒响应 → 业务操作必须异步
- 先返回 200 + 占位响应"处理中..."，后台异步执行，完成再更新卡片
- 幂等靠 review_state.status

## 7-A.6 修改表单

卖家点「✏️ 修改」弹飞书原生表单：

```
修改回复内容
──────────────────────
原文：
"兄弟 800 真走不了..."

你的版本：
┌────────────────────┐
│ [多行文本框]         │
│                     │
└────────────────────┘

[ ] 发送后加入金牌示例库

[取消] [发送]
```

**关键设计**：
- 文本框**预填 AI 原文**（多数只改几个字）
- 复选框"加入示例库" → 一键沉淀
- 取消 → 回到卡片
- 发送 → resolve_review(modified, new_text)

## 7-A.7 路由

**审核/通知卡片**：主推送会话（机器人私聊 + 卖家 open_id）

**告警卡片**：管理群 chat_id；严重告警额外 `@卖家`

**命令响应**：原路返回

**配置表**：

```sql
CREATE TABLE notification_routing (
  seller_id TEXT PRIMARY KEY,
  feishu_open_id TEXT NOT NULL,
  admin_chat_id TEXT NOT NULL,
  mute_levels TEXT[] DEFAULT '{"L4"}',
  business_hours JSONB,
  created_at TIMESTAMPTZ
);
```

## 7-A.8 管理命令

**MVP 清单**：

```
/status              所有账号状态
/status seller_A     指定账号详细
/pause seller_A 2h   暂停 N 小时
/resume seller_A     恢复
/mute buyer_X        静默某买家
/unmute buyer_X      取消静默
/stats today         今日统计
/stats week          本周统计
/cookie seller_A     查 Cookie 状态
/help                帮助
```

**实现**：
- 正则匹配前缀 `/`
- 解析参数
- 调内部 API
- 返回命令响应卡片

**复杂命令跳 Web**：
- `/edit_persona seller_A` → "配置较复杂，[在工作台打开]"
- 不把复杂配置塞飞书

## 7-A.9 深度链接

卡片里「🔗 在工作台打开」→ Web 工作台

**链接格式**：
```
https://your-workspace.com/auth/feishu?token={short_token}&redirect={target_path}
```

**流程**：
```
卖家点按钮 → 飞书返回跳转 URL（带 short_token）
  ↓ 浏览器打开
  ↓ Web 验证 token：
    ├─ 有效 → 自动登录 + 跳 target_path
    └─ 无效 → 登录页
  ↓ 用户看到目标页
```

**short_token**：
- Redis 存 `{token: {seller_id, feishu_user_id, exp}}`，TTL 30 秒
- 一次性使用

## 7-A.10 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 会话载体 | 私聊 + 管理群 | 责任分离 |
| 买家级隔离 | 不做 | 方案 B 废弃 |
| 卡片内容 | 精简 + 深链 | 不承担历史 |
| 通知分级 | L1/L2 强 L4 静默 | 避免轰炸 |
| 修改方式 | 飞书原生表单 | 无需跳转 |
| 复杂操作 | 跳 Web | 各取所长 |
| 命令 | 斜杠命令 | 明确高效 |
| 幂等 | review_state.status | 防重复 |

## 7-A.11 边界情况

**飞书 API 不可用**：
- push_card 失败 → 重试 3 次
- 全失败 → 消息留 review_queue，定时扫描兜底
- 关键告警 → 降级邮件/短信（MVP 不做）

**卖家在卡片到达前已自己回复**：
- AI 建议在飞书 + 买家端未发
- 卖家看到点"拒绝"或忽略
- review_state 按 rejected/expired 处理

**长时间不处理 L2**：
- 默认 10 分钟超时 → rejected + 冷淡消息告知

**多卡片并发**：按到达顺序推，不合并，各自独立

**卖家同时 Web 和飞书操作**：靠 review_state 幂等；先到先得

**表单提交时已超时**：
- 提交后查 review_state.status
- 已超时 → "超时了哈，需要我重发吗？" + 重发按钮

**卡片更新频率过高触发限流**：
- 飞书卡片更新 ~50/s/bot
- L3 倒计时 10 秒更新一次，不要每秒
- 批量操作串行化

## 7-A.12 观测

**Prometheus**：
- `feishu_card_push_total{type, result}`
- `feishu_callback_total{action_type, result}`
- `feishu_api_latency_seconds`
- `feishu_api_errors_total{endpoint}`

**业务指标**：
- 推送到首次操作的平均时长（触达效率）
- 各 level 处理率（未超时比例）
- L2/L3 按钮点击分布
- 修改发送率（AI 质量信号）

**告警**：
- 飞书 API 错误率 > 5% 5 分钟
- 卡片推送积压 > 20
- 某类型回调连续失败

## 7-A.13 测试要求

**单元**：
- 每种卡片渲染（JSON 正确）
- 回调签名验证
- 幂等保护（重复回调只执行一次）
- 斜杠命令解析

**集成**（飞书测试机器人）：
- 端到端：发卡片 → 点按钮 → review_state 更新
- 修改表单：填写 → 提交 → 新文本入队
- 各类告警实际推送

**UX 验收**：
- 手机飞书推送 → 点击 → 决策 → 完成 < 20s
- 跳 Web → 自动登录 → 目标会话 < 3s
- 多卡片连续到达通知合理

## 7-A.14 实施指引

**实施顺序**：
1. 飞书开放平台注册（手动前置）：
   - 企业自建应用、机器人、webhook URL
   - 拿 app_id/app_secret
   - 申请权限（im:message、im:message.group_at_msg、im:card、im:chat、im:resource）
2. 飞书客户端封装（lark-oapi SDK）：
   - `send_card`、`update_card`、`send_text`、`open_form`
   - 统一错误、重试、限流
   - 签名验证工具
3. 卡片模板系统（`card_templates/`）：
   - 每种 JSON 模板（review.json、alert_cookie_expired.json 等）
   - Jinja2 或字符串替换
4. Webhook 路由（FastAPI）：
   - `/feishu/webhook` 统一端点
   - 按 event_type 分发
   - 签名验证第一步
5. 业务处理器：
   - review_action_handler
   - alert_action_handler
   - command_handler
   - form_handler
6. 推送服务：
   - push_review_card（review_queue 拉 → 渲染 → 发）
   - push_alert
   - 异步
7. short_token 机制
8. 命令系统（装饰器注册）
9. 配置管理（notification_routing CRUD）
10. 观测埋点

**容易踩坑**：
- 签名验证必做（否则被刷）
- 3 秒超时：长操作必须异步
- 卡片 JSON schema 经常变 → 按最新文档
- 用 `open_id`（应用内唯一稳定），不是 user_id
- 机器人进群才能发
- 单卡片 JSON < 30KB
- `uuid` 参数做幂等防重复
- 表单提交 5 分钟内有效
- 测试和生产用不同飞书应用

**不要做**：
- ❌ 同步调用飞书 API（阻塞 webhook）
- ❌ 卡片塞长对话历史
- ❌ 自己实现签名（用 lark-oapi）
- ❌ 复杂配置做成卡片
- ❌ 买家级群管理（方案 B 废弃）

**输出物**：
- `backend/app/modules/feishu_bot/`
- `client.py`、`card_templates/`、`handlers/`
- `push_service.py`、`commands/`、`auth.py`（short_token）
- `schema.sql`
- `tests/feishu/`
- `docs/feishu_setup.md`（飞书应用配置说明）
