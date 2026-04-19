# Frontend

Web 工作台（模块 7-B）的前端代码目录。

## 初始化

目录目前为空。首次启动时请按 `docs/setup.md` 里的"启动前端"章节初始化 Next.js 项目。

初始化命令：
```bash
cd frontend
pnpm create next-app@latest . --typescript --tailwind --app --use-pnpm --no-src-dir --import-alias "@/*"
pnpm dlx shadcn@latest init
pnpm dlx shadcn@latest add button card dialog input label form table tabs sheet dropdown-menu toast
pnpm add @tanstack/react-query @tanstack/react-virtual zustand date-fns recharts axios reconnecting-websocket
```

## 相关模块规格

见 `../docs/modules/module_07b.md`。
