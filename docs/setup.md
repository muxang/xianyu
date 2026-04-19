# 开发环境搭建指南

面向 Windows 11 + Claude Code CLI + RTX 4090 的完整搭建步骤。

## 1. 前置工具安装

### Git for Windows
https://git-scm.com/download/win

安装时勾选：
- Use Git from the command line and also from 3rd-party software
- Checkout as-is, commit Unix-style line endings
- Use MinTTY

安装后配置：
```bash
git config --global user.name "你的名字"
git config --global user.email "你的邮箱"
git config --global core.autocrlf input
git config --global init.defaultBranch main
```

### Node.js
装 LTS 20.x：https://nodejs.org/

安装 pnpm：
```bash
npm install -g pnpm
```

### Python 3.11 + uv
下载 Python 3.11：https://www.python.org/downloads/
（安装时勾选 Add Python to PATH）

安装 uv：
```bash
pip install uv
```

### Docker Desktop
https://www.docker.com/products/docker-desktop/

Settings → General：启用 Use the WSL 2 based engine
Settings → Resources：CPU >= 4 核，内存 >= 8G

### Claude Code CLI
```bash
npm install -g @anthropic-ai/claude-code
claude login
```

## 2. 项目启动

### 启动基础设施
```bash
cp .env.example .env
# 编辑 .env 填入你的 API keys

cd infra/docker
docker compose -f docker-compose.dev.yml up -d
docker compose -f docker-compose.dev.yml ps
```

验证：
- Postgres: `docker exec -it xianyu-postgres psql -U xianyu -c "SELECT version();"`
- Redis: `docker exec -it xianyu-redis redis-cli ping`
- Langfuse: 浏览器访问 http://localhost:3100

### Langfuse 首次配置
1. 访问 http://localhost:3100 注册本地账号
2. 创建 organization + project
3. Settings → API Keys → Create new API keys
4. 把 `pk-lf-...` 和 `sk-lf-...` 填到 `.env`

### 启动后端
```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

访问 http://localhost:8000/health 验证。

### 启动前端

前端工程还未初始化。首次启动时运行：

```bash
cd frontend
pnpm create next-app@latest . --typescript --tailwind --app --use-pnpm --no-src-dir --import-alias "@/*"
# 目录非空时选择 Yes 覆盖

pnpm dlx shadcn@latest init
# Style: Default, Base color: Slate, CSS variables: Yes

pnpm dlx shadcn@latest add button card dialog input label form table tabs sheet dropdown-menu toast

pnpm add @tanstack/react-query @tanstack/react-virtual zustand date-fns recharts axios reconnecting-websocket
pnpm add -D @types/node

pnpm dev
```

## 3. API Key 申请

### 阿里云百炼
https://bailian.console.aliyun.com/

开通后创建 API Key，填入 `.env` 的 `DASHSCOPE_API_KEY`。

### DeepSeek（备用）
https://platform.deepseek.com/

创建 API Key，填入 `.env` 的 `DEEPSEEK_API_KEY`。

### 飞书
https://open.feishu.cn/

1. 开通开发者身份
2. 创建自建应用
3. 记录 App ID 和 App Secret
4. 权限管理：申请 im:message、im:message.group_at_msg、im:card、im:chat、im:resource

### 生成 Cookie 加密密钥
```bash
cd backend
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

输出填入 `.env` 的 `COOKIE_ENCRYPTION_KEY`。

## 4. Claude Code 首次验证

```bash
cd <项目根目录>
claude
```

测试：
- "介绍一下这个项目"（应正确描述项目）
- `/daily-standup`（应按命令定义执行）
- "调用 code-reviewer subagent 审计 backend/app/main.py"（应进入 subagent 模式）

全部通过即环境就绪。
