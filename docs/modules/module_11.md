# 模块 11：模型网关（LiteLLM 封装）

> **状态**: 规格待填充
>
> 核心要点：
> - 统一 LLM 入口 call_llm(purpose, messages, ...)
> - LLMPurpose 枚举驱动模型路由
> - purpose_routing YAML 配置（primary + fallback + timeout）
> - 自动 fallback、结构化日志、Langfuse trace 接入
> - 成本统计
