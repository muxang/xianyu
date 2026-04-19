"""Smoke tests for app.shared.types."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.shared.enums import AutomationLevel, IntentType, MessageType
from app.shared.types import (
    ConversationTurn,
    InboundMessage,
    OutboundMessage,
    SubgraphOutput,
)

# Datetime with no sub-millisecond precision so JSON round-trip is exact.
FIXED_DT = datetime(2026, 4, 20, 12, 0, 0, tzinfo=UTC)
FIXED_DT_MS = int(FIXED_DT.timestamp() * 1000)
SELLER_UUID = UUID("11111111-1111-1111-1111-111111111111")


# --- InboundMessage ---------------------------------------------------------


def test_inbound_message_happy_path() -> None:
    msg = InboundMessage(
        message_id="m-1",
        seller_id=SELLER_UUID,
        buyer_id="buyer-x",
        conversation_id="conv-1",
        item_id="item-1",
        type=MessageType.TEXT,
        text="hello",
        timestamp=FIXED_DT,
    )
    assert msg.timestamp == FIXED_DT
    assert msg.image_url is None
    assert msg.raw == {}


def test_inbound_message_serializes_timestamp_to_ms_int() -> None:
    msg = InboundMessage(
        message_id="m-1",
        seller_id=SELLER_UUID,
        buyer_id="b",
        conversation_id="c",
        type=MessageType.TEXT,
        text="hi",
        timestamp=FIXED_DT,
    )
    payload = json.loads(msg.model_dump_json())
    assert payload["timestamp"] == FIXED_DT_MS
    assert isinstance(payload["timestamp"], int)


def test_inbound_message_validates_from_ms_int_json() -> None:
    raw = json.dumps(
        {
            "message_id": "m-1",
            "seller_id": str(SELLER_UUID),
            "buyer_id": "b",
            "conversation_id": "c",
            "item_id": None,
            "type": "text",
            "text": "hi",
            "image_url": None,
            "timestamp": FIXED_DT_MS,
            "raw": {},
        }
    )
    msg = InboundMessage.model_validate_json(raw)
    assert msg.timestamp == FIXED_DT
    assert msg.timestamp.tzinfo is not None
    assert msg.timestamp.utcoffset() == timedelta(0)


def test_inbound_message_validates_from_iso_string_with_offset() -> None:
    raw = json.dumps(
        {
            "message_id": "m-1",
            "seller_id": str(SELLER_UUID),
            "buyer_id": "b",
            "conversation_id": "c",
            "type": "text",
            "text": "hi",
            "timestamp": FIXED_DT.isoformat(),
        }
    )
    msg = InboundMessage.model_validate_json(raw)
    assert msg.timestamp == FIXED_DT


def test_inbound_message_validates_from_iso_z_suffix() -> None:
    iso_z = FIXED_DT.isoformat().replace("+00:00", "Z")
    msg = InboundMessage.model_validate(
        {
            "message_id": "m-1",
            "seller_id": str(SELLER_UUID),
            "buyer_id": "b",
            "conversation_id": "c",
            "type": "text",
            "text": "hi",
            "timestamp": iso_z,
        }
    )
    assert msg.timestamp == FIXED_DT


def test_inbound_message_normalizes_other_tz_to_utc() -> None:
    shanghai = timezone(timedelta(hours=8))
    local_dt = datetime(2026, 4, 20, 20, 0, 0, tzinfo=shanghai)
    msg = InboundMessage(
        message_id="m-1",
        seller_id=SELLER_UUID,
        buyer_id="b",
        conversation_id="c",
        type=MessageType.TEXT,
        text="hi",
        timestamp=local_dt,
    )
    assert msg.timestamp == FIXED_DT
    assert msg.timestamp.utcoffset() == timedelta(0)


def test_inbound_message_naive_datetime_rejected() -> None:
    naive = datetime(2026, 4, 20, 12, 0, 0)
    with pytest.raises(ValidationError) as exc:
        InboundMessage(
            message_id="m-1",
            seller_id=SELLER_UUID,
            buyer_id="b",
            conversation_id="c",
            type=MessageType.TEXT,
            text="hi",
            timestamp=naive,
        )
    assert "timezone-aware" in str(exc.value)


def test_inbound_message_naive_iso_string_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        InboundMessage.model_validate(
            {
                "message_id": "m-1",
                "seller_id": str(SELLER_UUID),
                "buyer_id": "b",
                "conversation_id": "c",
                "type": "text",
                "text": "hi",
                "timestamp": "2026-04-20T12:00:00",
            }
        )
    assert "timezone" in str(exc.value)


def test_inbound_message_round_trip_json() -> None:
    original = InboundMessage(
        message_id="m-1",
        seller_id=SELLER_UUID,
        buyer_id="b",
        conversation_id="c",
        item_id="item-1",
        type=MessageType.IMAGE,
        text=None,
        image_url="https://example.com/y.jpg",
        timestamp=FIXED_DT,
        raw={"k": "v"},
    )
    restored = InboundMessage.model_validate_json(original.model_dump_json())
    assert restored == original


def test_inbound_message_extra_field_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        InboundMessage.model_validate(
            {
                "message_id": "m-1",
                "seller_id": str(SELLER_UUID),
                "buyer_id": "b",
                "conversation_id": "c",
                "type": "text",
                "text": "hi",
                "timestamp": FIXED_DT_MS,
                "unexpected_field": "boom",
            }
        )
    assert "extra" in str(exc.value).lower()


# --- OutboundMessage --------------------------------------------------------


def test_outbound_message_happy_path() -> None:
    out = OutboundMessage(
        seller_id=SELLER_UUID,
        conversation_id="c",
        buyer_id="b",
        type=MessageType.TEXT,
        text="hello",
    )
    assert isinstance(out.outbound_id, UUID)
    assert out.send_at is None


def test_outbound_message_outbound_id_auto_unique() -> None:
    a = OutboundMessage(
        seller_id=SELLER_UUID,
        conversation_id="c",
        buyer_id="b",
        type=MessageType.TEXT,
        text="x",
    )
    b = OutboundMessage(
        seller_id=SELLER_UUID,
        conversation_id="c",
        buyer_id="b",
        type=MessageType.TEXT,
        text="x",
    )
    assert a.outbound_id != b.outbound_id


def test_outbound_message_send_at_serializes_to_ms() -> None:
    out = OutboundMessage(
        seller_id=SELLER_UUID,
        conversation_id="c",
        buyer_id="b",
        type=MessageType.TEXT,
        text="x",
        send_at=FIXED_DT,
    )
    payload = json.loads(out.model_dump_json())
    assert payload["send_at"] == FIXED_DT_MS


def test_outbound_message_send_at_none_serializes_to_null() -> None:
    out = OutboundMessage(
        seller_id=SELLER_UUID,
        conversation_id="c",
        buyer_id="b",
        type=MessageType.TEXT,
        text="x",
    )
    payload = json.loads(out.model_dump_json())
    assert payload["send_at"] is None


def test_outbound_message_naive_send_at_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        OutboundMessage(
            seller_id=SELLER_UUID,
            conversation_id="c",
            buyer_id="b",
            type=MessageType.TEXT,
            text="x",
            send_at=datetime(2026, 4, 20, 12, 0, 0),
        )
    assert "timezone-aware" in str(exc.value)


def test_outbound_message_round_trip_json_with_send_at() -> None:
    original = OutboundMessage(
        outbound_id=uuid4(),
        seller_id=SELLER_UUID,
        conversation_id="c",
        buyer_id="b",
        type=MessageType.TEXT,
        text="hello",
        send_at=FIXED_DT,
    )
    restored = OutboundMessage.model_validate_json(original.model_dump_json())
    assert restored == original


def test_outbound_message_round_trip_json_without_send_at() -> None:
    original = OutboundMessage(
        seller_id=SELLER_UUID,
        conversation_id="c",
        buyer_id="b",
        type=MessageType.TEXT,
        text="hello",
    )
    restored = OutboundMessage.model_validate_json(original.model_dump_json())
    assert restored == original


def test_outbound_message_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        OutboundMessage.model_validate(
            {
                "seller_id": str(SELLER_UUID),
                "conversation_id": "c",
                "buyer_id": "b",
                "type": "text",
                "text": "x",
                "mystery": "hello",
            }
        )


# --- ConversationTurn -------------------------------------------------------


def test_conversation_turn_happy_path() -> None:
    turn = ConversationTurn(
        role="buyer",
        text="how much",
        timestamp=FIXED_DT,
    )
    assert turn.role == "buyer"
    assert turn.intent is None
    assert turn.was_ai_generated is False


def test_conversation_turn_invalid_role_rejected() -> None:
    with pytest.raises(ValidationError):
        ConversationTurn.model_validate(
            {
                "role": "seller_assistant",
                "text": "x",
                "timestamp": FIXED_DT_MS,
            }
        )


def test_conversation_turn_round_trip_from_int_ms_json() -> None:
    raw = json.dumps(
        {
            "role": "ai_suggestion",
            "text": "ok",
            "image_summary": None,
            "timestamp": FIXED_DT_MS,
            "intent": "FAQ",
            "was_ai_generated": True,
            "was_modified": False,
        }
    )
    turn = ConversationTurn.model_validate_json(raw)
    assert turn.intent is IntentType.FAQ
    assert turn.timestamp == FIXED_DT


def test_conversation_turn_naive_datetime_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        ConversationTurn(
            role="buyer",
            text="x",
            timestamp=datetime(2026, 4, 20, 12, 0, 0),
        )
    assert "timezone-aware" in str(exc.value)


# --- SubgraphOutput ---------------------------------------------------------


def test_subgraph_output_default_metadata_is_empty_dict() -> None:
    out = SubgraphOutput(
        reply="hi",
        automation_level=AutomationLevel.L4,
        confidence=0.95,
        rationale="high confidence faq",
    )
    assert out.metadata == {}


def test_subgraph_output_metadata_independent_per_instance() -> None:
    a = SubgraphOutput(
        reply="x",
        automation_level=AutomationLevel.L3,
        confidence=0.7,
        rationale="r",
    )
    b = SubgraphOutput(
        reply="y",
        automation_level=AutomationLevel.L3,
        confidence=0.7,
        rationale="r",
    )
    a.metadata["k"] = "v"
    assert b.metadata == {}


def test_subgraph_output_accepts_arbitrary_metadata() -> None:
    out = SubgraphOutput(
        reply="x",
        automation_level=AutomationLevel.L2,
        confidence=0.5,
        rationale="r",
        metadata={"negotiation_round": 3, "remaining_room": "30.00"},
    )
    assert out.metadata["negotiation_round"] == 3
    assert out.metadata["remaining_room"] == "30.00"


def test_subgraph_output_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        SubgraphOutput(
            reply="x",
            automation_level=AutomationLevel.L4,
            confidence=1.5,
            rationale="r",
        )


def test_subgraph_output_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        SubgraphOutput.model_validate(
            {
                "reply": "x",
                "automation_level": "L4",
                "confidence": 0.9,
                "rationale": "r",
                "mystery": 1,
            }
        )
