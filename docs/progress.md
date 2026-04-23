# 开发进度

Claude Code 的 `/daily-standup` 命令会自动维护此文件。最新条目在最上。

## Day 2 (2026-04-23)

### 完成

**Module 11 model_gateway（完整实现）**

- [x] schema.py + errors.py 异常家族（7 类：LLMError 基类 + 6 子类，含 W6 今日新增的 LLMFeatureNotImplementedError）
- [x] capabilities.py
  - MODEL_CAPABILITIES（7 model，保守标注 + 官方文档源注释）
  - PURPOSE_REQUIREMENTS（7 LLMPurpose 的 capability 要求）
  - validate_routing_capabilities fail-fast 启动校验
- [x] router.py
  - load_routing_table 7 层 fail-fast YAML 加载
  - strict getter（未 initialized 即 raise）
  - select(purpose) 按 purpose 查 RoutingEntry
- [x] cost_calculator.py
  - _QWEN_PRICES_CNY 原币种保留 + USD_PER_CNY 派生结构（W4 后用 `_FX_CONTEXT = Context(prec=28)` 固定精度）
  - 3 种 warn 分支（unknown_model / unverified_model / cost_estimate_above_tier1）
  - _TIERED_MODELS 只对 Qwen 阶梯定价触发守卫
- [x] gateway.py
  - initialize() 幂等，4 步顺序（load → env sync → LiteLLM globals → flag）
  - call_llm() primary → fallback 切换（LLMAuthenticationError 不 fallback）
  - _attempt_call() 单层 asyncio.wait_for 超时 + Langfuse metadata 注入
  - LLMAllModelsFailedError.primary_error 跨 scope 捕获（W2）
- [x] config/purpose_routing.yaml（7 个 LLMPurpose 全覆盖 + total-budget 语义说明，W1）
- [x] 108 个单元测试 + 1 个 integration 测试
- [x] 真实调用验证：qwen-turbo 19+2 tokens $9.58e-7 USD 1391ms + Langfuse trace 自动断言通过

**基础设施集成**

- [x] main.py lifespan 接入 init_model_gateway()（model_gateway first, DB second）
- [x] tests/test_config.py autouse fixture（遍历 Settings.model_fields 清 env，防 .env 渗透）
- [x] pyproject.toml markers: integration（默认 pytest 跳过真实调用）
- [x] pyproject.toml dev dep: types-pyyaml（mypy strict 所需）

### 验收指标

- 152 tests pass (单元) + 1 integration deselected by default
- model_gateway/gateway.py 覆盖率 99%（1 miss = _messages_to_litellm defense-in-depth 分支）
- ruff + mypy --strict 全绿（31 source files）
- 真实调用：qwen-turbo, 19+2 tokens, $9.58e-7 USD, 1391ms latency
- Langfuse trace 自动断言：name=llm.chitchat, session_id matched, tag=purpose:CHITCHAT
- 启动时序：model_gateway_initialized → model_gateway_ready → db_engine_ready → app_starting

### 过程中的关键决策

1. **WebFetch 查证 qwen-vl-max/plus 支持 JSON Mode**（help.aliyun.com 文档确认"固定版别名 + 非思考模式"，-latest 和 snapshot 变体被文档明确排除；caveat 钉在 capabilities.py 注释里）
2. **CHITCHAT primary 切回 qwen-turbo**：qwen-flash 在短问短答场景实际贵 25%（input cheap / output expensive 阶梯结构不利于短问短答）
3. **清理 Zhipu（YAGNI）**：当前 YAML 没路由到，从 MODEL_CAPABILITIES 和 MODEL_PRICES 一起删。加 test_no_zhipu_models 防腐蚀
4. **Langfuse 集成走 LiteLLM 自动上报** + metadata 注入（trace_name / session_id / tags），不手动用 Langfuse SDK 开 span
5. **超时只用 asyncio.wait_for 外层**：不给 LiteLLM 传 timeout 参数（避免双层超时让 Langfuse callback 失灵）
6. **LLMAuthenticationError 不 fallback**：401/403 时 fallback 常和 primary 同 key 池，try fallback 是浪费；key 轮换 vs vendor outage 分不同 runbook
7. **超时不记 cost**：asyncio.TimeoutError 路径不构造 LLMResponse，也不 increment cost metric；只日志记 duration_ms 供排查
8. **drop_params = False 显式设**：避免 LiteLLM 静默吞掉厂商不支持的参数（否则 JSON Mode 在某些 model 上静默失效）
9. **images 检查前移到 call_llm 入口**：避免原版 NotImplementedError 被 fallback 吞导致错误分类为 LLMAllModelsFailedError
10. **timeout_ms 保留名字不改为 total_timeout_budget_ms**：改名会引入新歧义（"谁的总预算？"），选择通过 YAML 注释 + schema description + docstring 三处澄清语义（W1）
11. **Langfuse 查询用 session_id 而非 trace_id**：LiteLLM 对 session_id 映射是文档级契约，对 trace_id 可能内部另生成 id
12. **integration test 成本透明**：$9.58e-7 per run，addopts 默认排除，手动 pytest -m integration 才跑
13. **LLMFeatureNotImplementedError 独立于 stdlib NotImplementedError**（W6）：`except LLMError:` 的调用方能 catch vision-rejection，不用额外 except 子句
14. **W5 `_AsyncTimeout` 别名集中 noqa**：module-level 1 个 noqa 替代 7 处 per-line noqa，测试语义和 production except 精确一致

### code-reviewer 结果

Verdict: APPROVE with minor follow-ups
- 0 Critical
- 8 Warning → **6 today fixed**（W1 W2 W4 W5 W6 W8）+ **2 记 TODO**（W3 W7）
- 5 Minor → **1 today fixed**（keys alignment smoke test）+ **4 记 TODO**

### 遗留 TODO

**短期（Week 2-3 可做）**：
1. **Qwen3-VL 升级评估**：Qwen3-VL-Plus/Flash 已确认支持 JSON Mode（文档引用在 capabilities.py 注释里）。Week 2 做 IMAGE_UNDERSTANDING 性能/成本调优时评估升级路由
2. **W3: cost_usd_str 日志双字段**（Decimal + string），Week 3+ Grafana dashboard 接入时做
3. **W7: Langfuse integration test 用 polling 替代 3s 固定 sleep**，CI 上线时做

**中长期**：
4. **思考模式 capability 拆分**：未来如支持 thinking mode 请求（reasoning），需把 Capability.JSON_MODE 拆为 JSON_MODE_NORMAL vs JSON_MODE_THINKING
5. **Zhipu 接入流程**：未来如为 fallback 多样化接入 Zhipu，完整查证 capability + pricing 后加回 MODEL_CAPABILITIES + MODEL_PRICES
6. **USD_PER_CNY 从 .env 读**：Week 4+，quarterly 更新汇率更方便
7. **_messages_to_litellm vision 格式实现**：Blocked on Week 2 IMAGE_UNDERSTANDING 集成测试
8. **Minor 1: LLMTimeoutError message 加 duration_ms**（让 exception 本身携带信息，不只日志字段）
9. **Minor 2: Message.name validator**（当前 docstring 说"tool 专用"但无约束）
10. **Minor 4: router.py defense-in-depth 分支加 `# pragma: no cover` 或 assert 语句**
11. **Minor 5: cost_calculator 结果 .quantize() 到小数点后 8 位**，dashboard 字符串化更友好

### Day 3 计划

**上午（3-4h）Module 12 可观测**
- `backend/app/shared/redis_keys.py`：规格 12.4 节所有 Redis 键模板 + RedisKeys 辅助类
- `backend/app/shared/metrics.py`：Prometheus 指标定义 + `/metrics` 端点
- `backend/app/shared/tracing.py`：Langfuse trace_span + log_llm_cost + mask_pii helper

**下午（3-4h）Module 01 第 1 阶段**
- `backend/app/modules/session/schema.py`：SellerContext 等
- `backend/app/modules/session/adapters.py`：MessagingAdapter 抽象基类
- `backend/app/modules/session/mock_adapter.py`：测试用 mock
- `backend/app/modules/session/cookie_vault.py`：Fernet 加密
- Alembic 迁移：sellers 表

前置检查：
- Docker postgres/redis/langfuse 仍 healthy
- git 干净（Day 2 commits 推上去）
- prometheus_client 需安装（`uv add prometheus-client`）

---

## Day 1 (2026-04-20)

### 完成

- [x] **共享类型层 `app/shared/`**
  - 19 StrEnum（涵盖所有跨模块共享枚举）
  - 4 BaseModel: `InboundMessage`, `OutboundMessage`, `ConversationTurn`, `SubgraphOutput`
  - datetime ↔ ms int 双向序列化（`field_validator` + `field_serializer`，`mode='before'`）
  - 27 个 `test_types` 覆盖 happy path / 双向 roundtrip / 跨时区归一化 / naive 拒绝 / `extra='forbid'`
- [x] **Alembic 异步初始化**
  - `migrations/env.py` 改写为 `create_async_engine` + `async with` + `run_sync` 桥接
  - 第一条迁移 `1ed5026011d2_init_extensions`（启用 pgvector 和 pg_trgm 扩展）
  - 验证：`alembic_version` 表已写入，`pg_extension` 三行齐（`pg_trgm 1.6` / `plpgsql 1.0` / `vector 0.8.2`）
- [x] **`db/session.py` 异步连接层**
  - async engine + sessionmaker 单例（`get_engine` / `get_session_maker` / `get_session` / `dispose_engine`）
  - `pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`, `expire_on_commit=False`
  - 7 tests 覆盖单例 / commit / rollback / dispose reset
- [x] **`config.py` 完整 Settings（18 字段）**
  - Database / Redis / LLM APIs / Feishu / Langfuse / Application / Workspace auth / Encryption 八段
  - `APP_ENV` + `APP_LOG_LEVEL` 用 `Literal` 类型
  - 5 tests 覆盖加载 / 必填缺失 / Literal 非法值
- [x] **`main.py` 生产级 healthcheck**
  - `lifespan`（取代废弃的 `on_event`）
  - `GET /health`（保留）+ `GET /health/db`（新增，3s 超时，503 for timeout / 500 for other errors）
  - structlog 启动/关停日志（`app_starting` / `engine_initialized` / `app_stopped`）
  - 错误响应脱敏（不暴露密码 / 内部 URL）
  - 4 tests 覆盖
- [x] **收工 3 修掉 code-reviewer 6 条 Warning**
  - W1 删死代码 `tests/test_health.py`
  - W2 `/health/db` 加超时 `asyncio.wait_for`
  - W3 `lifespan` 预热 engine 避免 TOCTOU
  - W4 `migrations/env.py` `try/finally` 防连接泄漏
  - W5 `test_db_session` fixture 双端清理
  - W6 删 `pyproject.toml` 的 `pythonpath` 反模式

### 验收指标

- 44 tests pass (0.96s)
- 总覆盖 96%
- `ruff check` 全绿
- `mypy --strict` 全绿
- 手动 curl `/health` + `/health/db` 返回 200
- `alembic_version = 1ed5026011d2`

### 遗留 TODO（不阻塞 Day 2）

1. **`db/session.py` 覆盖率 73%**：`get_session()` 异步生成器路径未覆盖。这是设计选择（单元测试走 maker，HTTP endpoint 现阶段不用 `Depends(get_session)`）。Week 4 做首个 Workspace API endpoint 时通过集成测试覆盖，目标 90%+。

2. **负时间戳接受**：`InboundMessage.timestamp` / `OutboundMessage.send_at` 的 int 反序列化允许 `< 0`（1970 前）。当前行为是接受；未来考虑在 validator 里拒绝负值或 `< 某个业务 epoch`（闲鱼 2014 年上线可作参考 epoch）。

3. **`.gitattributes` 缺失**：Windows 开发时 `.env.example` 等文件 git 报 LF→CRLF warning。项目根加 `.gitattributes`（`* text=auto eol=lf`）让仓库统一 LF。Day 2 或某个 buffer day 做。

4. **`expire_on_commit=False` 行为测试**：当前只断言配置项（`session.sync_session.expire_on_commit is False`）。等 Day 3 有第一个 ORM 模型（`sellers` 表）后，补一个真实验证 detached instance access 的测试。

5. **per-file-ignores for migrations**：每次 `alembic revision` 生成的新迁移文件带旧式模板语法（UP007 / UP035 / I001），每次 ruff auto-fix 一遍烦。Day 2-3 做第二次迁移时顺手加配置（只忽略 UP007 / UP035 / I001，F401 保留）。

### Day 2 计划

实施模块 11 模型网关：

- LiteLLM 封装 `call_llm(purpose, ...)` 统一入口
- `purpose_routing.yaml` 配置
- Primary + Fallback（`qwen-max` → `deepseek`）
- 超时、重试、JSON Mode、多模态
- 成本估算（`cost_calculator`）
- Langfuse trace 接入（LiteLLM `success_callback`）
- 基准测试：一次真实调用（`qwen-turbo`）验证 Langfuse trace 落库

前置检查：

- `.env` 里 `DASHSCOPE_API_KEY` 是真 key（不是 `sk-your-xxx` 模板）
- `.env` 里 `LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` 有值（需要浏览器登 `http://localhost:3100` 创建 project）

---

## 初始状态

- [x] 项目骨架创建
- [x] Claude Code 配置（CLAUDE.md、agents、commands、hooks、MCP）
- [x] 基础设施 Docker Compose
- [x] 后端最简入口（`backend/app/main.py` 仅 `/health`）
- [x] 13 个模块规格填充（已扩写 ~4135 行，未提交）
- [x] 4 周实施作战手册（`docs/playbook/`，未提交）
- [ ] 前端脚手架初始化
- [ ] 模块 1 实现（XianYuApis 适配器）
- [ ] 模块 2 实现（消息总线 / Redis Streams）
- [ ] 模块 3 实现
- [ ] 模块 4 实现（四段式检索）
- [ ] 模块 5 实现
- [ ] 模块 6 实现
- [ ] 模块 7a / 7b 实现
- [ ] 模块 8-12 实现

## 当前快照（2026-04-20）

- Git: 仅 1 个 commit（`ecfa516` 初始骨架）；分支 main，与 origin/main 同步
- 工作树：13 个模块 spec 修改未 commit；`docs/playbook/`、`backend/uv.lock` 未跟踪
- 代码：`backend/app/` 只有 `main.py`（FastAPI hello-world）；`frontend/` 无 TS 文件
- 测试：仅 `backend/tests/test_health.py`

## 今天的计划（2026-04-20）

按 `docs/playbook/week1/day01.md` 启动 Day 1 工作：

1. **先收尾昨日产物**
   - Review 13 个 spec 的 diff，确认无误后提交：`docs(modules): expand module specs`
   - 提交 playbook：`docs(playbook): add 4-week implementation playbook`
   - 提交 `backend/uv.lock`
2. **开工前检查（Day 1）**
   - 确认 `infra/docker/docker-compose.dev.yml` 起的 Postgres/Redis/Langfuse healthy
   - 确认 `.env` 真实密钥已填（`DASHSCOPE_API_KEY`、`LANGFUSE_*`、`COOKIE_ENCRYPTION_KEY`）
3. **Day 1 实现目标**
   - `backend/app/shared/`：共享类型/枚举/异常基类
   - `backend/app/config.py`：Pydantic Settings
   - `backend/app/db/`：async engine + session + base
   - Alembic 初始化 + 启用 pgvector / pg_trgm 扩展
4. **验证**
   - `uv run ruff check .` + `uv run mypy app/`
   - `uv run pytest backend/tests/`

## 阻塞 / 待用户确认

- 是否同意将 13 个 spec 改动 + playbook 一次性 commit？还是分批 review？
- `.env` 中所需密钥（DashScope、Langfuse、Fernet）是否都已就绪？
- Docker 基础设施当前是否在运行？
