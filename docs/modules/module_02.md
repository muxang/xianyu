# 模块 2：消息流水线（Redis Streams）

> **状态**: 规格待填充
>
> 把之前讨论中"模块 2 完整规格"内容粘贴到这里。
>
> 核心要点：
> - Redis Streams 的 in_queue / out_queue / review_queue 三条流
> - 延迟投递（Sorted Set + DelayedDispatcher 每秒轮询）
> - 审核状态机（CREATED / PUSHED / approved / modified / rejected / silenced / expired）
> - 账号级隔离、PEL 回收、死信队列
> - 幂等性靠消费方保证
