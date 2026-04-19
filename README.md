# 闲鱼 AI 客服系统

基于 LangGraph 的多账号闲鱼智能客服，支持 L1-L4 自动化分级、人机协同、Web 工作台。

## 架构概览

- **接入层**：XianYuApis 适配器（闲鱼 WebSocket 逆向）
- **编排层**：LangGraph（主图 + 多个子图）
- **检索层**：四段式精准检索（KB 路由 + 硬过滤 + 召回 + LLM Selector）
- **生成层**：Qwen3.5-Max（主）+ Qwen-Flash（辅）+ Qwen-VL-Max（视觉）
- **人机协同**：飞书机器人（推送）+ Web 工作台（管理）
- **可观测**：Langfuse + Prometheus

## 技术栈

- **后端**: Python 3.11 + FastAPI + LangGraph + PostgreSQL + Redis
- **前端**: Next.js 14 + TypeScript + Tailwind + shadcn/ui
- **LLM**: 阿里云百炼 (Qwen3.5) + DeepSeek (备用)
- **通信**: XianYuApis (闲鱼逆向) + 飞书机器人

## 快速开始

详见 `docs/setup.md`。

## 模块列表

见 `docs/architecture.md` 和 `docs/modules/`。

## 开发指引

本项目使用 Claude Code CLI 进行全自动交付开发。所有模块规格在 `docs/modules/`，Claude Code 配置在 `.claude/`。

### 启动基础设施

```bash
cd infra/docker
docker compose -f docker-compose.dev.yml up -d
```

### 启动后端

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

### 启动前端

```bash
cd frontend
pnpm install
pnpm dev
```

### 使用 Claude Code

```bash
cd <项目根目录>
claude
```
