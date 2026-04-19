# 开发进度

Claude Code 的 `/daily-standup` 命令会自动维护此文件。最新条目在最上。

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
