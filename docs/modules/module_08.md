# 模块 8：自动化分档 + 风控合规

## 8.1 目标与边界

**做什么**：
- **自动化分档**：基于 intent、confidence、风险信号决定 L1/L2/L3/L4
- **风控合规**：回复发送前的最后一道闸门
- **承诺检测**：识别"包退、保真、假一赔X"等刚性承诺
- **数字核对**：生成回复里的价格与议价状态一致
- **违禁词**：平台禁用、引流词、敏感话题

**不做什么**：
- 不做意图识别（用理解层结果）
- 不做拟人化（出站调节层）
- 不直接和外界通信

**本质**：**AI 输出的安全闸门 + 决策分层器**。系统安全性底线。

## 8.2 外部契约

**上游**：Agent 子图输出（reply, level, confidence 等）

**下游**：
- 出站调节层（通过检查的消息）
- review_queue（L2/L3 走这里）
- 告警系统（L1 或高危触发）

**核心 API**：

```python
async def classify_automation(
    subgraph_output: SubgraphOutput,
    context: StructuredContext,
) -> AutomationDecision

async def compliance_check(
    reply_text: str,
    context: StructuredContext,
    state_snapshot: dict | None = None,
) -> ComplianceResult

AutomationDecision {
    level: AutomationLevel   # L1/L2/L3/L4
    reason: str              # 为什么这个级别
    override_reply: str | None  # 某些 L1 下换默认话术
}

ComplianceResult {
    passed: bool
    violations: [Violation]
    action: ComplianceAction  # BLOCK / DOWNGRADE / ALERT / PASS
    sanitized_text: str | None
}

Violation {
    type: ViolationType  # PROMISE / CONTACT_LEAK / FORBIDDEN_WORD / PRICE_MISMATCH / POLICY
    severity: Severity   # LOW / MEDIUM / HIGH / CRITICAL
    detail: str
    matched_text: str
}
```

## 8.3 自动化分档决策

按优先级判断（命中即终止）：

```
1. 强制 L1 意图：
   AFTER_SALES, COMPLAINT → L1

2. 高危风控信号：
   contact_info_detected, sentiment=ANGRY → L1

3. confidence < 0.6 或 retrieval 拒答：
   → L2

4. 议价关键轮：
   remaining_room < bottom_price * 0.1 → L2

5. ORDER_STATUS：
   → L2（AI 无数据源）

6. FAQ / CHITCHAT 且 confidence ≥ 0.85 且 seller full_auto：
   → L4

7. 默认：
   → L3（预发送 30s）
```

**每条规则理由明确**（便于审计和调整）：

```python
RULE_RATIONALES = {
    "AFTER_SALES_FORCE_L1": "售后涉及退款，AI 无法承诺，强制人工",
    "COMPLAINT_FORCE_L1": "投诉需真人同理回应",
    "CONTACT_LEAK_L1": "疑似引流，安全人工处理",
    "LOW_CONFIDENCE_L2": "AI 不确定，卖家决策",
    "NEGOTIATION_CRITICAL_L2": "议价接近底价，卖家亲自决策",
    "ORDER_STATUS_L2": "AI 无订单/物流数据源",
    "HIGH_CONFIDENCE_FAQ_L4": "高频低风险，全自动效率最高",
    "DEFAULT_L3": "预发送保留最后干预机会",
}
```

## 8.4 风控合规检查

### 8.4.1 承诺词检测

规则库（YAML）：

```yaml
promise_patterns:
  - type: WARRANTY
    severity: HIGH
    patterns:
      - "包退"
      - "包换"
      - "终身(保修|维修|质保)"
      - "永久质保"
    action: BLOCK

  - type: AUTHENTICITY
    severity: HIGH
    patterns:
      - "绝对正品"
      - "假一赔\\d+"
      - "百分百(正|真)品"
      - "保真"
    action: BLOCK

  - type: DELIVERY
    severity: MEDIUM
    patterns:
      - "保证.*(今天|明天).*发"
      - "秒发"
    action: DOWNGRADE
```

**实现**：
- Python `re` 编译成 regex 列表
- 扫描 reply_text
- 每命中生成 Violation

### 8.4.2 引流检测

```yaml
contact_leak:
  - phone_number: '1[3-9]\d{9}'
  - qq_with_context: '(QQ|扣|qq).*?\d{5,12}'
  - wechat_with_context: '(微信|vx|weixin|V信).*?[a-zA-Z0-9_-]{3,}'
  - contact_invite: '(加我|私聊|私信|加.*?号)'
```

**检测对象**：同时检查**买家原话和 AI 回复**

### 8.4.3 数字核对

议价场景下，AI 回复里的价格必须与议价状态一致。

**算法**：
1. 从 reply_text 抽出所有"像价格"的数字
2. 从 state_snapshot.current_seller_offer 取当前应报价
3. reply 里出现 current_seller_offer 以外的 offer 数字，且不在 ±5 → PRICE_MISMATCH
4. 特殊："包邮"等加值词不算价格数字

**关键**：防 LLM 幻觉出不一致价格的最后防线。

### 8.4.4 违禁词库

来源：
- 平台禁用词清单
- 敏感话题（政治、暴力、色情）
- 自定义黑名单（卖家配置）

**匹配**：直接字符串匹配 + AC 自动机（大规模）

### 8.4.5 综合决策

```python
for violation in violations:
    if severity == CRITICAL:
        action = BLOCK
        return
    if severity == HIGH and action != DOWNGRADE:
        action = BLOCK
    if severity == MEDIUM:
        candidate_action = DOWNGRADE

if all violations LOW:
    try auto-sanitize (删手机号、替承诺词)
    if sanitize 成功: action = PASS, sanitized_text = ...
    else: action = DOWNGRADE
```

## 8.5 关键决策

| 决策 | 选择 | 理由 |
|-----|------|-----|
| 规则引擎 | 正则 + YAML | 可维护性高 |
| 检测对象 | 双向（买家 + AI） | 买家引流也要处理 |
| 承诺 | 默认 BLOCK 不清洗 | 改字意思变 |
| 数字核对 | 强制一致 | 防幻觉关键 |
| 违禁词 | 平台 + 自定义 | 适应不同卖家 |

## 8.6 边界情况

- 买家自己说"加微信"，AI 没回 → 只记录不触发
- 承诺在引用中（"你保证包退吗"）→ 本版简化（仅 AI 回复算）
- 价格在商品标题（"iPhone 14 Pro 256"）→ 排除 product.title
- 规则误报过高 → "白名单短语"配置豁免

## 8.7 测试要求

- 每条规则正例 + 负例
- 覆盖率 > 95%（安全模块）
- 承诺词 50+ 变体
- 价格核对边界（包含/不包含/多价格并存）

## 8.8 数据库表

```sql
CREATE TABLE compliance_rules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rule_type TEXT NOT NULL,        -- PROMISE / CONTACT_LEAK / FORBIDDEN
  pattern TEXT NOT NULL,
  severity TEXT NOT NULL,          -- LOW/MEDIUM/HIGH/CRITICAL
  action TEXT NOT NULL,            -- BLOCK/DOWNGRADE/ALERT
  description TEXT,
  is_active BOOLEAN DEFAULT TRUE,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE compliance_violations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  trace_id TEXT NOT NULL,
  seller_id TEXT,
  conversation_id TEXT,
  rule_type TEXT NOT NULL,
  severity TEXT NOT NULL,
  matched_text TEXT,
  reply_text_snapshot TEXT,
  action_taken TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_cv_trace ON compliance_violations(trace_id);
CREATE INDEX idx_cv_seller ON compliance_violations(seller_id, created_at DESC);
```

## 8.9 实施指引

**实施顺序**：
1. 定义枚举和数据结构
2. 规则库 YAML（50 条种子规则）
3. 承诺检测 / 引流检测 / 违禁词 分别实现
4. 数字核对（与议价模块对齐数据结构）
5. 综合决策器
6. 测试（重点！规则易错）

**不要做**：
- ❌ 自动清洗承诺类（改字变语义）
- ❌ 规则写死代码（全配置化）
- ❌ 忽略"低危累积"（3 条 LOW 应升级）

**输出物**：
- `backend/app/modules/risk/`
- `automation_classifier.py`、`compliance_check.py`
- `rules/`（YAML 规则库）
- `detectors/promise.py`、`detectors/contact_leak.py`、`detectors/forbidden_words.py`、`detectors/price_match.py`
- `tests/risk/`
