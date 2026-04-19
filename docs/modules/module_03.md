# 模块 3：理解层（Understanding）

> **状态**: 规格待填充
>
> 核心要点：
> - 产出 StructuredContext 对象（意图 + 实体 + 对话 + 商品 + 画像 + 卖家配置）
> - 五子步骤：图片理解 / 上下文加载（并行）→ 意图+实体合并调用 → 商品解析 → 组装
> - 意图 + 实体抽取合并为单次 Qwen-Flash 调用（JSON Mode）
> - contact_info 检测用正则（不用 LLM）
> - 对话历史 Redis 缓存 + DB 兜底
> - 准确率目标：> 88%
