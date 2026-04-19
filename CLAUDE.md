# 闲鱼 AI 客服项目 · Claude Code 工作指南

你正在一个**单用户、单人开发**的项目里工作。项目负责人要求：**全自动交付、高质量代码、严谨测试、主动审计**。

## 项目概览

这是一个闲鱼卖家智能客服系统，核心架构：

- **接入层**：XianYuApis 适配器（闲鱼 WebSocket 逆向）
- **编排层**：LangGraph（主图 + 多个子图）
- **检索层**：四段式精准检索（KB 路由 + 硬过滤 + 召回 + LLM Selector）
- **生成层**：Qwen3.5-Max（主）+ Qwen-Flash（辅）+ Qwen-VL-Max（视觉）
- **人机协同**：飞书机器人（推送）+ Web 工作台（管理）
- **可观测**：Langfuse + Prometheus

详细架构见 `docs/architecture.md`，模块清单见 `docs/modules/`。

## 技术栈（不要随意更换）

- Python 3.11（不用 3.12+）
- FastAPI、LangGraph、SQLAlchemy 2.0 (async)、asyncpg、redis (async)
- LiteLLM 统一 LLM 入口
- Pytest + pytest-asyncio
- Next.js 14 (App Router) + TypeScript + shadcn/ui + TanStack Query + Zustand
- Postgres 16 + pgvector
- Docker Compose

## 严格原则

### 1. 模块规格是最高优先级
在实现任何模块前，**必读** `docs/modules/{模块名}.md`。规格文件定义了目标、契约、边界情况、测试要求。实现要严格符合规格。

如果规格里某处不清楚或与实际代码冲突，**停下来问用户**，不要自行猜测。

### 2. 禁止过度工程化
- 不引入规格未要求的依赖
- 不做"我觉得以后可能用到"的抽象
- 不自作主张重写无关代码
- 不改动其他模块的代码除非规格要求

### 3. 测试必须真实
- 不写 `assert True` 之类的假测试
- 不用 `mocker.patch` 屏蔽所有实际逻辑（mock 外部依赖，不 mock 业务代码）
- 每个模块的测试必须能跑通并有意义的断言
- 测试覆盖率目标 > 80%（对风控、流水线这种关键模块要 > 95%）

### 4. 写完代码必须验证
每完成一个子任务后必须：
1. 跑 `uv run ruff check .` 和 `uv run mypy app/`（backend）或 `pnpm lint` 和 `pnpm tsc --noEmit`（frontend）
2. 跑相关测试
3. 向用户报告"已完成 + 验证通过 + 遗留问题"

### 5. 在任何地方使用结构化日志
用 `structlog`，每个日志带上下文字段（trace_id、seller_id 等）。不要用 `print`。

### 6. 错误处理要具体
- 不要裸 `except:` 或 `except Exception:` 然后 pass
- 捕获具体异常、记录、决定是否重抛
- 外部调用（LLM、DB、Redis、飞书）必须有超时和重试

### 7. 不要碰 .env 和密钥
- 不在代码里硬编码任何 API key
- 新增配置项时同时更新 `.env.example`
- 不要把 `.env` 提交到 git

### 8. 目录结构约定
- 所有后端代码在 `backend/app/` 下，按模块分子目录
- 模块间依赖通过明确的接口（abstract base class 或 protocol），不互相 import 内部
- 共享类型放 `backend/app/shared/`
- 数据库模型放 `backend/app/db/models/`
- Alembic 迁移放 `backend/migrations/`

## Python 代码风格

- 100% type hints
- async/await 贯穿（除纯 CPU 任务）
- Pydantic V2 模型用于所有 API schema 和配置
- SQLAlchemy 用 async，每个模块的 DB 操作封装在 repository 层
- 所有 ID 用 UUID（`uuid.uuid4()`）
- 时间用 `datetime` + timezone-aware（`datetime.now(UTC)`）
- 金额用 `Decimal`，不用 float

## TypeScript 代码风格

- 严格模式（`strict: true`）
- 所有组件函数式 + `"use client"` 明确标注
- API 响应类型从后端 OpenAPI schema 生成，不手写
- 使用 TanStack Query 管理服务端状态
- Zustand 只存 UI 状态
- 错误边界和 loading 状态每个数据加载点都必须有

## 工作流程

### 接到任务时

1. 先读相关的模块规格（`docs/modules/*.md`）
2. 阅读相邻模块的代码（了解上下文）
3. **规划**：列出要做的事、要创建/修改的文件、可能的风险点
4. 向用户确认规划（如果任务 > 2 小时）
5. 执行
6. 跑 lint + 测试
7. 汇报结果

### 遇到歧义时

**停下来问**。不猜。

### 需要外部知识时

用 web_search 工具获取最新信息。阿里云百炼、LangGraph、pgvector、shadcn 这些都经常更新文档。

### 完成一个模块后

自动触发 `code-reviewer` subagent 自审。

## 禁止事项

- 禁止使用 print、console.log 做日志
- 禁止使用 emoji（代码注释、日志、commit message 都不要）
- 禁止使用中文变量名
- 禁止捕获异常后吞掉
- 禁止循环里 await 未批量的 DB 操作
- 禁止在测试里用 `time.sleep`（用 `asyncio.sleep` 或 mock 时间）
- 禁止在生产代码路径里调用 web_search 或任何交互式工具

## 提交 Git

commit message 用英文，conventional commits 格式：
- `feat(module_1): implement xianyu adapter`
- `fix(module_2): correct redis streams consumer group creation`
- `test(module_4): add retrieval benchmark dataset`
- `refactor(module_5): extract negotiation state machine`
- `docs: update architecture.md`
- `chore: update dependencies`

## 需要我帮助时

使用 `@docs/...`、`@backend/app/...` 引用文件，不要复制粘贴大段代码问我。
