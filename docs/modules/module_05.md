# 模块 5：议价子图（Negotiation Subgraph）

> **状态**: 规格待填充
>
> 核心要点：
> - 议价状态机（ACTIVE / AT_BOTTOM / PENDING_BUYER / PENDING_SELLER / DEAL_REACHED / ABANDONED / ESCALATED）
> - 让步金额由代码规则计算，LLM 只做自然语言包装
> - 递减让步、组合 value add、关键轮触发 L2
> - NegotiationState 持久化到 Postgres
> - 仿真测试（模拟买家 Agent 对打）
