"""Cross-module shared data classes (used by 3+ modules).

Module-private types belong in their owning module's schema file:

  - StructuredContext  -> module_03/schema.py  (M3 only)
  - NegotiationState   -> module_05/schema.py  (M5 only)
  - KnowledgeUnit, RetrievalResult -> module_04/schema.py
  - ReviewCardData, AlertPayload, FeishuCallbackEvent -> module_07a
  - WsEvent             -> module_07b
  - MainGraphState      -> module_10/state.py

Time-field convention
---------------------
In memory, every datetime field is timezone-aware UTC.

On the wire (JSON / Redis Streams) datetimes serialize to integer
milliseconds since the Unix epoch.

Inputs accept any of:
  - tz-aware ``datetime`` (converted to UTC if in another zone)
  - ``int`` interpreted as ms since epoch
  - ISO 8601 ``str`` with explicit tzinfo (trailing ``Z`` allowed)

Naive datetimes and tz-less ISO strings raise ``ValueError`` (wrapped
in Pydantic's ``ValidationError``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.shared.enums import AutomationLevel, IntentType, MessageType

_NAIVE_DATETIME_ERROR = (
    "datetime must be timezone-aware; got naive datetime. "
    "Use datetime.now(UTC) or attach tzinfo explicitly."
)
_NAIVE_ISO_ERROR = (
    "ISO datetime string must include timezone "
    "(e.g. '2026-04-20T12:00:00+00:00' or '2026-04-20T12:00:00Z')."
)


def _to_utc_datetime(value: Any) -> datetime:
    """Coerce supported inputs into a tz-aware UTC ``datetime``.

    Accepts: aware ``datetime``, ``int`` (ms since epoch), ISO 8601
    ``str`` with tzinfo. Rejects ``bool`` (an int subclass) so a stray
    ``True`` cannot be silently interpreted as ``1`` ms past epoch.
    """
    if isinstance(value, bool):
        raise ValueError(f"Cannot interpret bool as datetime: {value!r}")
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(_NAIVE_DATETIME_ERROR)
        return value.astimezone(UTC)
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, tz=UTC)
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError(_NAIVE_ISO_ERROR)
        return dt.astimezone(UTC)
    raise ValueError(f"Cannot interpret value as datetime: {value!r}")


def _datetime_to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class InboundMessage(BaseModel):
    """Standardized inbound platform message.

    Produced by module 1 (XianyuAdapter) and consumed by module 2
    (Redis Streams) and module 3 (understanding layer). Field set is
    the minimum common contract; module 3 wraps it inside
    ``StructuredContext.current_message`` rather than mutating it.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    message_id: str
    """Platform-side message id; combined with seller_id for dedup (M1)."""

    seller_id: UUID
    """Internal seller UUID (sellers.id)."""

    buyer_id: str
    """Platform buyer id (not a UUID)."""

    conversation_id: str
    """Platform conversation id; primary correlation key downstream."""

    item_id: str | None = None
    """Platform item id, if the conversation is bound to a product."""

    type: MessageType
    text: str | None = None
    image_url: str | None = None
    """URL not bytes; M1 keeps the URL even if download later fails."""

    timestamp: datetime
    """Message arrival time (tz-aware UTC); serialized to int ms."""

    raw: dict[str, Any] = Field(default_factory=dict)
    """Original platform payload, kept for debugging."""

    @field_validator("timestamp", mode="before")
    @classmethod
    def _validate_timestamp(cls, value: Any) -> datetime:
        return _to_utc_datetime(value)

    @field_serializer("timestamp")
    def _serialize_timestamp(self, value: datetime) -> int:
        return _datetime_to_ms(value)


class OutboundMessage(BaseModel):
    """Standardized outbound message produced by module 9 (after shaping).

    Consumed by module 2 (out_queue / delayed dispatcher) and module 1
    (``adapter.send``). ``send_at=None`` means send immediately;
    otherwise M2 schedules into the delayed sorted set.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    outbound_id: UUID = Field(default_factory=uuid4)
    """Auto-generated internal id; M2 uses it as idempotency key."""

    seller_id: UUID
    conversation_id: str
    buyer_id: str

    type: MessageType
    text: str | None = None
    image_bytes: bytes | None = None

    send_at: datetime | None = None
    """When to send (tz-aware UTC); ``None`` means immediately."""

    @field_validator("send_at", mode="before")
    @classmethod
    def _validate_send_at(cls, value: Any) -> datetime | None:
        if value is None:
            return None
        return _to_utc_datetime(value)

    @field_serializer("send_at")
    def _serialize_send_at(self, value: datetime | None) -> int | None:
        if value is None:
            return None
        return _datetime_to_ms(value)


class ConversationTurn(BaseModel):
    """A single past turn in a buyer<->seller conversation.

    Produced by module 3 (history loader) and consumed wherever past
    turns are threaded into prompts: module 4 selector context,
    module 5 negotiation prompt, module 6 few-shot context.

    ``role`` uses ``Literal`` rather than a new ``Enum`` because the
    three values appear only here; promoting to a top-level enum would
    add surface area without a second consumer.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    role: Literal["buyer", "seller", "ai_suggestion"]
    text: str
    image_summary: str | None = None

    timestamp: datetime
    """Turn timestamp (tz-aware UTC); serialized to int ms."""

    intent: IntentType | None = None
    was_ai_generated: bool = False
    was_modified: bool = False

    @field_validator("timestamp", mode="before")
    @classmethod
    def _validate_timestamp(cls, value: Any) -> datetime:
        return _to_utc_datetime(value)

    @field_serializer("timestamp")
    def _serialize_timestamp(self, value: datetime) -> int:
        return _datetime_to_ms(value)


class SubgraphOutput(BaseModel):
    """Common contract every LangGraph subgraph returns.

    Produced by every subgraph branch in module 10 (negotiation, FAQ,
    chitchat, fallback, L1, clarify); consumed by module 8's automation
    classifier and compliance check, and by module 10 itself for
    downstream routing.

    Subgraph-specific extensions (e.g. negotiation state transitions
    from M5) belong inside ``metadata`` rather than as typed fields
    here, so this contract stays neutral across all subgraphs.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    reply: str
    """Candidate reply text fed into compliance check."""

    automation_level: AutomationLevel
    """Subgraph's initial level recommendation; M8 may downgrade."""

    confidence: float = Field(ge=0.0, le=1.0)
    """0.0-1.0; M8 routes confidence < 0.6 to L2."""

    rationale: str
    """Free-form reason, used as Langfuse span attribute."""

    metadata: dict[str, Any] = Field(default_factory=dict)
    """Subgraph-specific extension payload (e.g. negotiation state)."""
