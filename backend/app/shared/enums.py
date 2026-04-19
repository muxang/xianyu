"""Cross-module enumerations.

All enums inherit from `StrEnum` (Python 3.11+) so values are plain strings
that serialize cleanly to JSON and compare with string literals.
"""

from enum import StrEnum


class MessageType(StrEnum):
    """Inbound/outbound message payload type.

    Used by: module 1 (XianyuAdapter), module 2 (queue), module 3, module 9.
    """

    TEXT = "text"
    IMAGE = "image"
    SYSTEM = "system"


class SessionStatus(StrEnum):
    """SellerSession lifecycle state.

    Used by: module 1 (SellerSession state machine), module 7-A (status display),
    module 12 (Prometheus gauge).

    Transition rules live in module 1; RISK_CONTROLLED is non-recoverable
    without manual intervention.
    """

    INITIALIZING = "initializing"
    ACTIVE = "active"
    PAUSED = "paused"
    COOKIE_EXPIRED = "cookie_expired"
    RISK_CONTROLLED = "risk_controlled"
    ERROR = "error"


class IntentType(StrEnum):
    """Buyer message intent classification.

    Used by: module 3 (classifier output), module 4 (KB router input),
    module 8 (automation classifier), module 10 (router edges).
    """

    FAQ = "FAQ"
    PRODUCT_INQUIRY = "PRODUCT_INQUIRY"
    NEGOTIATION = "NEGOTIATION"
    ORDER_STATUS = "ORDER_STATUS"
    AFTER_SALES = "AFTER_SALES"
    COMPLAINT = "COMPLAINT"
    CHITCHAT = "CHITCHAT"
    INTENT_UNCLEAR = "INTENT_UNCLEAR"
    OTHER = "OTHER"


class SentimentType(StrEnum):
    """Buyer sentiment from understanding layer.

    Used by: module 3 (output), module 8 (ANGRY forces L1).
    """

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    ANGRY = "angry"


class UrgencyLevel(StrEnum):
    """Message urgency hint from understanding layer.

    Used by: module 3 (output), module 8 (urgency may influence level).
    """

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class AutomationLevel(StrEnum):
    """Send-side automation tier.

    L1: human-only (no AI suggestion sent).
    L2: human assists (AI suggestion queued for review, no auto-send).
    L3: pre-send with countdown (AI suggestion auto-sends if no veto).
    L4: full auto (AI sends without notification, low-risk only).

    Used by: module 7-A (card header), module 7-B (badge), module 8
    (classifier output), module 9 (path), module 10 (routing edges),
    module 12 (metrics label).
    """

    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class KnowledgeBase(StrEnum):
    """Knowledge base type for retrieval routing.

    Used by: module 4 (KB router output), module 7-B (knowledge admin pages).
    """

    PRODUCT = "PRODUCT"
    FAQ = "FAQ"
    SCRIPT = "SCRIPT"
    POLICY = "POLICY"


class RetrievalStrategy(StrEnum):
    """Stage-3 candidate recall strategy choice.

    Used by: module 4 (recall + diagnostics).
    """

    STRUCTURED_ONLY = "STRUCTURED_ONLY"
    VECTOR_Q2Q = "VECTOR_Q2Q"
    LLM_FULL_LIST = "LLM_FULL_LIST"
    VECTOR_SEMANTIC = "VECTOR_SEMANTIC"
    HYBRID = "HYBRID"


class NoneReason(StrEnum):
    """Why retrieval returned `found=False`.

    Used by: module 4 (output), module 8/10 (downstream routing on no-hit),
    module 12 (Prometheus counter).
    """

    KB_EMPTY = "KB_EMPTY"
    SELECTOR_REJECTED = "SELECTOR_REJECTED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    AMBIGUOUS = "AMBIGUOUS"
    NO_KB_MATCH = "NO_KB_MATCH"


class NegotiationStatus(StrEnum):
    """Per-conversation negotiation state-machine status.

    Used by: module 5 (state), module 7-A/7-B (status panel),
    module 8 (price-mismatch checker reads current_seller_offer).
    """

    ACTIVE = "ACTIVE"
    AT_BOTTOM = "AT_BOTTOM"
    PENDING_BUYER = "PENDING_BUYER"
    PENDING_SELLER = "PENDING_SELLER"
    DEAL_REACHED = "DEAL_REACHED"
    ABANDONED = "ABANDONED"
    ESCALATED = "ESCALATED"


class ConcessionPace(StrEnum):
    """Concession aggressiveness from NegotiationPolicy.

    Used by: module 5 (concession calculator).
    Spec uses lowercase values; member names are uppercase per Python convention.
    """

    SLOW = "slow"
    NORMAL = "normal"
    AGGRESSIVE = "aggressive"


class Scenario(StrEnum):
    """Few-shot example scenario tag.

    Used by: module 6 (gold examples), module 7-B (knowledge admin filter).

    Spec lists these 6 explicit values with `...` indicating the set will
    grow as more scenarios are seeded; new values must be added here
    centrally (not hardcoded by callers).
    """

    NEGOTIATION_FIRST_ROUND = "NEGOTIATION_FIRST_ROUND"
    NEGOTIATION_BOTTOM = "NEGOTIATION_BOTTOM"
    FAQ_GENERAL = "FAQ_GENERAL"
    FAQ_SHIPPING = "FAQ_SHIPPING"
    CHITCHAT_GREETING = "CHITCHAT_GREETING"
    COMPLAINT_SOFT = "COMPLAINT_SOFT"


class ExampleSource(StrEnum):
    """Provenance of a gold example.

    Used by: module 6 (storage + quality decay), module 7-B (admin display).
    """

    MANUAL = "MANUAL"
    FEISHU_ONE_CLICK = "FEISHU_ONE_CLICK"
    IMPORT = "IMPORT"
    SYSTEM_SEED = "SYSTEM_SEED"


class LLMPurpose(StrEnum):
    """Logical purpose for an LLM call; the model gateway routes by this key.

    Used by: every module that calls module 11 (model gateway).
    """

    INTENT_CLASSIFICATION = "INTENT_CLASSIFICATION"
    RETRIEVAL_SELECTOR = "RETRIEVAL_SELECTOR"
    MAIN_GENERATION = "MAIN_GENERATION"
    NEGOTIATION_GENERATION = "NEGOTIATION_GENERATION"
    STYLE_REWRITE = "STYLE_REWRITE"
    IMAGE_UNDERSTANDING = "IMAGE_UNDERSTANDING"
    CHITCHAT = "CHITCHAT"


class ViolationType(StrEnum):
    """Compliance rule category that fired.

    Used by: module 8 (detectors + violation log), module 12
    (compliance_violations_total counter).
    """

    PROMISE = "PROMISE"
    CONTACT_LEAK = "CONTACT_LEAK"
    FORBIDDEN_WORD = "FORBIDDEN_WORD"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    POLICY = "POLICY"


class Severity(StrEnum):
    """Generic severity scale for compliance violations and alerts.

    Used by: module 8 (Violation), module 7-A (alert color), module 12.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ComplianceAction(StrEnum):
    """Action taken on a reply that hit one or more violations.

    Used by: module 8 (output), module 10 (post-compliance routing).
    """

    PASS = "PASS"
    DOWNGRADE = "DOWNGRADE"
    BLOCK = "BLOCK"
    ALERT = "ALERT"


class ReviewAction(StrEnum):
    """Resolution outcome for a queued AI suggestion.

    Used by: module 2 (review_state status), module 7-A (callback handler),
    module 7-B (review POST), module 10 (resume payload).
    """

    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"
    SILENCED = "silenced"
    EXPIRED = "expired"


class AlertType(StrEnum):
    """Operational alert category routed to Feishu.

    Used by: module 7-A (push_alert dispatcher), module 12 (alerting rules).
    Values track the alert cards documented in module 7-A; extend centrally.
    """

    COOKIE_EXPIRED = "COOKIE_EXPIRED"
    RISK_CONTROL = "RISK_CONTROL"
    QUEUE_BACKLOG = "QUEUE_BACKLOG"
    L1_REQUIRED = "L1_REQUIRED"
    LLM_FAILURE = "LLM_FAILURE"
