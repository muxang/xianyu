# 模块 7-B：Web 工作台

## 7-B.1 目标与边界

**做什么**：
- 提供**单用户 Web 应用**，承载飞书无法处理的复杂交互
- 核心：**多会话管理、会话详情、配置中心、金牌示例库、数据看板**
- REST API + WebSocket 实时交互
- 支持飞书深链无感登录

**不做什么**：
- 不是面向买家的界面
- 不做多租户权限（单用户）
- 不做原生 App（响应式 + PWA）
- 不做复杂 BI（基础指标即可）

**本质**：**你的"闲鱼指挥中心"**。飞书是"对讲机"，Web 是"作战指挥部"。互补而非替代。

## 7-B.2 外部契约

**上游**：
- 你本人浏览器
- 飞书深链（short_token）

**下游**：
- FastAPI（新增 `/api/workspace/*`）
- WebSocket 端点（实时推送）
- Postgres/Redis（通过后端）

**API 命名空间**：

```
GET  /api/workspace/conversations
GET  /api/workspace/conversations/:id
GET  /api/workspace/sellers
PUT  /api/workspace/sellers/:id/config
WS   /ws/workspace
...（见 7-B.5）
```

## 7-B.3 整体架构

### 7-B.3.1 技术栈

**前端**：
- Next.js 14 (App Router)
- TypeScript
- Tailwind + shadcn/ui
- TanStack Query（服务端状态）
- Zustand（UI 状态）
- WebSocket 原生 + reconnecting-websocket
- date-fns
- Recharts

**后端（复用）**：
- FastAPI 新增 `/api/workspace/*`
- WebSocket 端点 `/ws/workspace`
- Pydantic schema 共享类型

**鉴权**：
- 单用户 JWT
- 登录方式：
  - 飞书深链（short_token 换 JWT）
  - 用户名密码（配置文件写死）

**部署**：
- Next.js `next build && next start`
- Caddy 反向代理：
  - `/api/*, /ws/*` → FastAPI (8000)
  - `/*` → Next.js (3000)
- 移动端浏览器或 PWA

### 7-B.3.2 页面结构

```
/login                          登录页
/dashboard                      总览（默认首页）
/inbox                          会话工作区（主）
/inbox/:conversation_id         定位到会话
/sellers                        账号管理
/sellers/:seller_id             单账号详情
/knowledge                      知识库
  /knowledge/faq
  /knowledge/scripts
  /knowledge/policies
  /knowledge/examples           金牌示例库（核心）
/analytics                      数据看板
/settings                       系统设置
```

**核心页面 `/inbox`**：你 80% 时间在这里。

## 7-B.4 核心页面：Inbox

**三栏响应式布局**：

```
┌──────────────────────────────────────────────────────────┐
│ Topbar: [Logo] [账号切换▼] [搜索]    [🔔][⚙️][头像▼]       │
├──────────────────────────────────────────────────────────┤
│          │                                  │              │
│ 左栏     │       中栏（对话视图）             │  右栏        │
│ 会话列表 │       Timeline Style              │ 上下文面板   │
│          │                                  │              │
│  250px   │       flex: 1                     │  340px       │
│          │                                  │              │
└──────────────────────────────────────────────────────────┘
```

### 左栏：会话列表

每项：
```
┌───────────────────────────────────┐
│ 🟡 [买家昵称]          2 分钟前   │
│ 商品：iPhone 14 Pro               │
│ 最新："能再便宜点吗"               │
│ [议价][第3轮][L2待处理]  未读 2    │
└───────────────────────────────────┘
```

**特性**：
- **过滤器**：状态（全部/待处理/进行中/已归档）、账号、标签
- **排序**：最后活动时间倒序，可切"未处理紧急度"
- **搜索**：买家昵称/商品名/对话内容
- **虚拟滚动**：`@tanstack/react-virtual`，1000+ 会话流畅
- **实时更新**：WS 推新消息，对应会话高亮 + 置顶

**状态颜色**：
- 🔴 L1 必须人工
- 🟡 L2 等待决策
- 🟠 L3 倒计时
- 🟢 L4 自动 / 正常
- ⚫ 已归档/静默

### 中栏：对话视图

时间线样式，类似 iMessage：

```
────────── 今天 14:23 ──────────

[买家] 这个还在吗？
       14:23

                  [AI→你] 在的～ 兄弟要的话可以优惠一点
                                         14:24 🤖自动

[买家] 能便宜多少？能 800 吗？
       14:25  🖼️ 图片

                  [AI建议] 800 真走不了 850 包顺丰行不
                  置信度 85% · 议价第3轮 · L2 等待决策
                  [发送] [修改] [拒绝] [转静默]
                                         待处理
```

**消息块**：
- 买家：左对齐，灰底白字
- AI 自动：右对齐，浅绿底，🤖
- AI 建议（未发）：右对齐，浅黄底 + 操作按钮
- 人工发送：右对齐，白底 + 边框
- 图片：缩略图，点击放大 + AI 理解结果
- 消息下小字：时间 + 状态

**中栏顶部栏**：
```
┌────────────────────────────────────────────┐
│ ← 买家昵称 · iPhone 14 Pro       [⋮更多]   │
│   首次接触 15 天前 · 历史议价 2 次          │
└────────────────────────────────────────────┘
```

**中栏底部：消息输入框**
```
┌────────────────────────────────────────────┐
│ [输入消息...]                  [Enter发送]  │
│ [📎图片] [💬话术模板] [⏸暂停自动]           │
└────────────────────────────────────────────┘
```

- 卖家直接在 Web 打字发消息（通过出站队列发到闲鱼）
- 「话术模板」一键插入
- 「暂停自动」：切换 L1 直到手动恢复

### 右栏：上下文面板

**商品卡片**：
```
┌────────────────────────────┐
│ [商品图]                   │
│ iPhone 14 Pro 256G         │
│ ¥6500 · 9成新               │
│ 底价: ¥6200（仅你可见）      │
│ [📝 编辑策略]                │
└────────────────────────────┘
```

**买家画像**：
```
┌────────────────────────────┐
│ 👤 小张                    │
│ 首次接触：15 天前            │
│ 总会话：3 次                 │
│ 议价记录：2 次失败、0 成交    │
│ 标签：[议价型] [爱砍价]       │
│ ⚠️ 历史议价多未成交           │
└────────────────────────────┘
```

**议价状态**（仅议价场景）：
```
┌────────────────────────────┐
│ 🤝 议价进行中                │
│ 轮次：第 3 轮                │
│ 标价 6500                   │
│ 让价：6500 → 6400 → 6300    │
│ 当前报价：6300              │
│ 剩余空间：100                │
│ 买家出价：800                │
│ 🔴 接近底价                 │
└────────────────────────────┘
```

**快速操作**：
```
┌────────────────────────────┐
│ ⭐ 最后一条回复加入示例库     │
│ 🔕 这个买家后续全部人工        │
│ 📱 闲鱼 App 里打开           │
│ 🗑 归档此会话                │
└────────────────────────────┘
```

**移动端适配**：
- `< 768px`：三栏变单栏 Tab（会话/对话/上下文）
- `768-1280px`：隐藏右栏，按钮弹出
- `> 1280px`：三栏完整

### 其他页面

**Dashboard**：简洁首页（今日数据 + 账号状态 + 待处理 + 快捷操作）

**账号管理**：列表 + 详情（多 Tab：基本信息、人设、自动化、议价、商品列表）

**知识库管理**：FAQ / Scripts / Policies / **金牌示例（核心）**

金牌示例库页面：
- 列表视图（按场景/来源/质量筛选，搜索）
- 详情编辑（场景、文本、标签、质量分、**测试区**）
- 飞书最近沉淀 Tab（批量审核）

**数据看板**：消息量趋势、采纳率分布、意图饼图、账号健康、成本分解

**设置**：用户、通知偏好、API Keys、系统信息

## 7-B.5 API 设计

### REST API

```
# 会话
GET  /api/workspace/conversations?seller_id=&status=&tag=&search=&page=&limit=
GET  /api/workspace/conversations/:id
GET  /api/workspace/conversations/:id/context
POST /api/workspace/conversations/:id/messages     # 手动发消息
POST /api/workspace/conversations/:id/actions      # archive/silence/pause_auto
POST /api/workspace/conversations/:id/reviews/:review_id  # 审核 AI 建议

# 账号
GET    /api/workspace/sellers
GET    /api/workspace/sellers/:id
PUT    /api/workspace/sellers/:id/config
POST   /api/workspace/sellers/:id/cookie
POST   /api/workspace/sellers/:id/pause
POST   /api/workspace/sellers/:id/resume
POST   /api/workspace/sellers/:id/sync-products

# 知识库
GET    /api/workspace/knowledge/:kb_type
POST   /api/workspace/knowledge/:kb_type
PUT    /api/workspace/knowledge/:kb_type/:id
DELETE /api/workspace/knowledge/:kb_type/:id
POST   /api/workspace/knowledge/:kb_type/:id/generate-variants  # FAQ Q2Q
POST   /api/workspace/knowledge/test-retrieval                   # 调试

# 金牌示例
GET    /api/workspace/examples
POST   /api/workspace/examples
PUT    /api/workspace/examples/:id
DELETE /api/workspace/examples/:id
POST   /api/workspace/examples/batch-import
GET    /api/workspace/examples/recent-from-feishu

# 看板
GET /api/workspace/analytics/overview?date_range=today|week|month
GET /api/workspace/analytics/timeseries?metric=&date_range=
GET /api/workspace/analytics/cost-breakdown

# 鉴权
POST /api/workspace/auth/login
POST /api/workspace/auth/exchange-token  # short_token → JWT
GET  /api/workspace/auth/me
```

### WebSocket

端点：`/ws/workspace?token=<jwt>`

**服务端事件**：

```typescript
type WsEvent =
  | { type: 'new_inbound_message', conversation_id, message }
  | { type: 'ai_suggestion', conversation_id, review_id, suggestion, level }
  | { type: 'outbound_sent', conversation_id, message }
  | { type: 'review_resolved', review_id, action, resolved_by }
  | { type: 'seller_status_changed', seller_id, new_status }
  | { type: 'alert', severity, title, detail }
  | { type: 'queue_update', seller_id, queue_name, depth }
```

**客户端事件**：
```typescript
| { type: 'subscribe', topics: ['all' | 'seller:xxx'] }
| { type: 'heartbeat' }
```

**约定**：
- 客户端 30s 心跳，服务端 45s 无心跳断连
- 断线重连 1/2/4/8/30s 指数退避
- 每事件带 `event_id`，前端去重

## 7-B.6 前端细节

### 目录结构

```
web-workspace/
├─ src/
│  ├─ app/
│  │  ├─ (auth)/login/
│  │  ├─ (main)/
│  │  │  ├─ dashboard/
│  │  │  ├─ inbox/
│  │  │  ├─ sellers/
│  │  │  ├─ knowledge/
│  │  │  ├─ analytics/
│  │  │  └─ settings/
│  │  └─ layout.tsx
│  ├─ components/
│  │  ├─ ui/          # shadcn
│  │  ├─ conversation/
│  │  ├─ knowledge/
│  │  ├─ sellers/
│  │  └─ common/
│  ├─ hooks/
│  ├─ lib/
│  │  ├─ api-client.ts
│  │  ├─ ws-client.ts
│  │  └─ query-client.ts
│  ├─ stores/
│  │  ├─ auth-store.ts
│  │  └─ ui-store.ts
│  └─ types/
│     └─ api.ts       # 从 OpenAPI 生成
```

### 状态管理分层

- **服务端数据** → TanStack Query
- **UI 状态** → Zustand（当前选中会话、筛选、侧栏等）
- **实时事件** → WebSocket → 更新 TanStack Query 缓存

```typescript
// WS 收到 new_inbound_message
queryClient.invalidateQueries(['conversation', conversation_id])
// 或更精细
queryClient.setQueryData(['conversation', id], (old) => ({
  ...old, messages: [...old.messages, new_msg]
}))
```

### 类型共享

**流程**：后端 Pydantic → OpenAPI schema → 前端 TS 类型

- FastAPI 自动生成 `/openapi.json`
- 前端 `openapi-typescript` 生成 `types/api.ts`
- Makefile：`make gen-types`

后端改 schema → 前端立即类型错误 → 不会上线才发现

## 7-B.7 鉴权与安全

### 登录流程

**飞书深链（主要）**：
```
卡片链接：https://workspace.com/auth/feishu?token=abc123
  ↓ 前端 /auth/feishu 页面
  ↓ POST /api/workspace/auth/exchange-token { short_token: 'abc123' }
  ↓ 后端验证 → 签发 JWT
  ↓ 前端存 localStorage + 跳目标页
```

**用户名密码（备用）**：
```
/login → POST /api/workspace/auth/login → JWT
```

### JWT

- 有效期 7 天
- Payload: `{user_id, role: 'admin', seller_ids, iat, exp}`
- 刷新：剩余 < 1 天时响应头静默下发新 token

### API 保护

- 所有 `/api/workspace/*` 需 JWT Bearer
- WS URL 带 token
- 401 → 跳登录页

### 其他安全

- HTTPS 生产必须（Caddy 自动证书）
- CORS 只允许前端域名
- 速率限制（slowapi，100 req/min）
- 敏感配置（LLM key）不暴露前端

## 7-B.8 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 前端框架 | Next.js 14 | 生态成熟 |
| 状态管理 | TanStack Query + Zustand | 分离清晰 |
| 样式 | Tailwind + shadcn | 快速+专业 |
| 实时 | WebSocket | 双向 |
| 鉴权 | 简单 JWT 单用户 | 快速落地 |
| 类型共享 | OpenAPI 自动生成 | 后端驱动 |
| 移动端 | PWA | 成本可控 |
| 部署 | Caddy | 自动 HTTPS |

## 7-B.9 边界情况

**会话列表超大（> 1000）**：虚拟滚动 + 默认只拉最近 30 天 + 待处理

**消息历史超长（> 500）**：初始 50，向上滚动分页加载（iMessage 式）

**多浏览器 Tab**：每 Tab 独立 WS；服务端真源保证一致

**飞书和 Web 重复操作**：review_state 幂等 + toast "已处理过"

**长时间停留**：JWT 过期跳登录（安全优先）

**深链 token 失效**：30s TTL → "链接已过期，请重新跳转或直接登录"

## 7-B.10 测试要求

**单元**：
- 核心组件（ConversationList、MessageBubble、ReviewActions）
- API client 封装
- WebSocket 重连
- Zustand store

**集成**（Playwright）：
- 登录流程（两种）
- 会话列表加载 + 筛选
- 选中 → 查看 → 点发送
- 修改并发送
- WS 实时到达

**后端 API 测试**：所有 endpoints + WS 事件广播

**E2E 验收**：
- 登录到处理首条消息 < 10s
- 新消息到 UI 延迟 < 1s
- 切换会话 < 500ms
- 响应式 375/768/1440

## 7-B.11 Phase 化

**Phase 1（必须，2 周）**：
- 登录页
- Inbox（核心）：会话列表 + 详情 + 审核
- WebSocket 实时
- 基础账号管理

**Phase 2（高价值，1-2 周）**：
- 完整账号管理
- 知识库（FAQ、话术、示例库）
- 手动发送
- 归档/静默

**Phase 3（锦上添花，1-2 周）**：
- 数据看板
- 政策文档
- 批量操作
- 搜索增强

**Phase 1 交付后系统可用**。Phase 2/3 可边用边迭代。

## 7-B.12 实施指引

**实施顺序**：

1. **后端（1-2 天）**：
   - FastAPI `workspace` 路由模块
   - Pydantic schema（按 7-B.5）
   - JWT 中间件
   - WebSocket 基础

2. **前端脚手架（半天）**：
   - `pnpm create next-app@latest . --typescript --tailwind --app --use-pnpm --no-src-dir --import-alias "@/*"`
   - shadcn init
   - 装依赖：tanstack-query, zustand, date-fns, recharts

3. **类型生成（半天）**：
   - 后端 `/openapi.json`
   - `openapi-typescript`
   - Makefile `make gen-types`

4. **登录页（1 天）**：
   - `/login`、`/auth/feishu`
   - Zustand auth-store
   - api-client（自动带 JWT）

5. **Inbox 基础（3-4 天）**：
   - 三栏响应式布局
   - 会话列表（虚拟滚动）
   - 对话视图
   - 上下文面板
   - 路由联动

6. **WebSocket 集成（1-2 天）**：
   - ws-client 封装
   - useWebSocket hook
   - 事件 → TanStack Query 缓存

7. **审核交互（1-2 天）**：
   - 按钮（发送/修改/拒绝）
   - 修改弹窗
   - 乐观更新 + 回滚

8. **账号管理（2 天）**

9. **知识库管理（3 天）**：FAQ、话术、示例库

10. **数据看板（1 天）**

11. **Dashboard（半天）**

12. **设置打磨（1-2 天）**：PWA manifest、UX 细节

13. **部署（1 天）**：Caddy、Docker、env

**总计约 4 周全功能，2 周 Phase 1 可用**。

**容易踩坑**：
- Next.js App Router 服务端/客户端边界：交互性组件都 `"use client"`
- WS 在 React StrictMode 双连：useEffect 清理或标志位
- TanStack Query key 要含所有 filter（否则缓存污染）
- Zustand 在 Next.js 下水合：仅客户端
- Tailwind 动态值用 style，不拼类名
- shadcn 不是 npm 包是复制代码，手动 sync
- OpenAPI 生成类型和响应不符 → 检查后端
- 飞书深链 token URL 编码（base64 url-safe）

**不要做**：
- ❌ 通用权限系统
- ❌ 复杂 BI
- ❌ 原生 App
- ❌ 多语言 i18n
- ❌ 复杂主题切换（浅色就好）
- ❌ 前端存业务数据

**输出物**：
- `frontend/` Next.js 项目
- `backend/app/workspace/` 后端路由模块
- OpenAPI schema 自动生成
- Caddyfile
- `docs/deployment.md`
- E2E 测试
- README

## 7-B.13 与其他模块关系

- **7-A 飞书**：互补协作；short_token 深链；review_state 幂等
- **2 消息流水线**：发送/修改/拒绝调 resolve_review
- **6 金牌示例库**：完整 UI；飞书沉淀人工复核
- **12 可观测**：看板从 Prometheus；日志跳 Langfuse
