# QUICKSTART

## 5 分钟启动

### Step 1. 填密钥
```bash
cp .env.example .env
# 编辑 .env，至少填：
# - DASHSCOPE_API_KEY（阿里云百炼）
# - FEISHU_APP_ID / FEISHU_APP_SECRET（飞书应用）
# - COOKIE_ENCRYPTION_KEY（见下方生成方法）
```

生成 Cookie 加密密钥（装好 Python 后）：
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### Step 2. 启动基础设施
```bash
cd infra/docker
docker compose -f docker-compose.dev.yml up -d
```

### Step 3. Langfuse 初始化
浏览器打开 http://localhost:3100
- 注册 → 创建 project → 生成 API Keys
- 把 pk-lf / sk-lf 填回 .env

### Step 4. 启动后端（可选验证）
```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
# 访问 http://localhost:8000/health
```

### Step 5. 填充模块规格
打开 `docs/modules/module_*.md`，把之前讨论的对应模块完整规格粘贴进去。

### Step 6. 启动 Claude Code
```bash
cd <项目根>
claude
```

常用命令：
- `/run-module module_01` - 实施某个模块
- `/review-module module_01` - 审计某个模块
- `/daily-standup` - 生成今日任务
- `/fix-ci` - 修所有 lint/test 问题

## 详细文档
- 完整搭建：`docs/setup.md`
- 架构概览：`docs/architecture.md`
- 模块规格：`docs/modules/`
- 工作指南：`CLAUDE.md`（Claude Code 自动读取）
