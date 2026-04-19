# 模块 4：精准检索引擎（四段式）

> **状态**: 规格待填充
>
> 核心要点：
> - 四段式：KB Router → Hard Filter → Candidate Recall → LLM Selector
> - 四库不同策略：商品结构化 / FAQ Q2Q 向量 / 话术 LLM 直选 / 政策语义向量
> - Selector 允许拒答（NoneReason 枚举）
> - 置信度阈值链、降级链
> - pgvector + HNSW 索引
> - 目标：Precision > 90%、Recall > 80%、拒答准确率 > 85%
