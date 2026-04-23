"""Tests for ``app.modules.model_gateway.schema``.

Covers pydantic boundaries for ``Message`` and ``LLMResponse``:
role whitelist, extra=forbid, non-negative numeric fields, and
Decimal precision preservation for ``cost_usd``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.modules.model_gateway.schema import LLMResponse, Message, RoutingEntry


class TestMessage:
    def test_minimal_user_message(self) -> None:
        m = Message(role="user", content="hi")
        assert m.role == "user"
        assert m.content == "hi"
        assert m.name is None

    @pytest.mark.parametrize("role", ["system", "user", "assistant", "tool"])
    def test_all_roles_accepted(self, role: str) -> None:
        m = Message.model_validate({"role": role, "content": "x"})
        assert m.role == role

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Message.model_validate({"role": "developer", "content": "x"})

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Message.model_validate(
                {"role": "user", "content": "hi", "unknown": 1},
            )

    def test_tool_message_with_name(self) -> None:
        m = Message(role="tool", content="result", name="search")
        assert m.name == "search"


def _valid_response_kwargs() -> dict[str, Any]:
    return {
        "content": "reply",
        "model_used": "dashscope/qwen-turbo",
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "cost_usd": Decimal("0.0003"),
        "latency_ms": 450,
    }


class TestLLMResponse:
    def test_happy_path(self) -> None:
        r = LLMResponse.model_validate(_valid_response_kwargs())
        assert r.content == "reply"
        assert r.model_used == "dashscope/qwen-turbo"
        assert r.prompt_tokens == 10
        assert r.completion_tokens == 20
        assert r.cost_usd == Decimal("0.0003")
        assert r.latency_ms == 450
        assert r.is_fallback is False

    def test_is_fallback_true_when_set(self) -> None:
        kwargs = _valid_response_kwargs()
        kwargs["is_fallback"] = True
        r = LLMResponse.model_validate(kwargs)
        assert r.is_fallback is True

    def test_missing_required_raises(self) -> None:
        kwargs = _valid_response_kwargs()
        del kwargs["content"]
        with pytest.raises(ValidationError):
            LLMResponse.model_validate(kwargs)

    def test_negative_prompt_tokens_rejected(self) -> None:
        kwargs = _valid_response_kwargs()
        kwargs["prompt_tokens"] = -1
        with pytest.raises(ValidationError):
            LLMResponse.model_validate(kwargs)

    def test_negative_completion_tokens_rejected(self) -> None:
        kwargs = _valid_response_kwargs()
        kwargs["completion_tokens"] = -1
        with pytest.raises(ValidationError):
            LLMResponse.model_validate(kwargs)

    def test_negative_cost_rejected(self) -> None:
        kwargs = _valid_response_kwargs()
        kwargs["cost_usd"] = Decimal("-0.01")
        with pytest.raises(ValidationError):
            LLMResponse.model_validate(kwargs)

    def test_negative_latency_rejected(self) -> None:
        kwargs = _valid_response_kwargs()
        kwargs["latency_ms"] = -1
        with pytest.raises(ValidationError):
            LLMResponse.model_validate(kwargs)

    def test_cost_usd_preserves_decimal_precision(self) -> None:
        """Tiny per-call cost must not lose precision when stored."""
        kwargs = _valid_response_kwargs()
        kwargs["cost_usd"] = Decimal("0.0000001234")
        r = LLMResponse.model_validate(kwargs)
        assert r.cost_usd == Decimal("0.0000001234")

    def test_extra_field_rejected(self) -> None:
        kwargs = _valid_response_kwargs()
        kwargs["unknown"] = "x"
        with pytest.raises(ValidationError):
            LLMResponse.model_validate(kwargs)

    def test_zero_values_accepted(self) -> None:
        """ge=0 allows zero (empty reply, cached response w/ no cost)."""
        kwargs = _valid_response_kwargs()
        kwargs["prompt_tokens"] = 0
        kwargs["completion_tokens"] = 0
        kwargs["cost_usd"] = Decimal(0)
        kwargs["latency_ms"] = 0
        r = LLMResponse.model_validate(kwargs)
        assert r.cost_usd == Decimal(0)


def _valid_routing_kwargs() -> dict[str, Any]:
    return {
        "primary": "dashscope/qwen-flash",
        "fallback": "dashscope/qwen-plus",
        "timeout_ms": 3000,
        "max_retries": 2,
        "temperature": 0.5,
    }


class TestRoutingEntry:
    def test_happy_path(self) -> None:
        e = RoutingEntry.model_validate(_valid_routing_kwargs())
        assert e.primary == "dashscope/qwen-flash"
        assert e.fallback == "dashscope/qwen-plus"
        assert e.timeout_ms == 3000
        assert e.max_retries == 2
        assert e.temperature == 0.5

    def test_timeout_below_lower_bound_rejected(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["timeout_ms"] = 50
        with pytest.raises(ValidationError):
            RoutingEntry.model_validate(kwargs)

    def test_timeout_above_upper_bound_rejected(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["timeout_ms"] = 120000
        with pytest.raises(ValidationError):
            RoutingEntry.model_validate(kwargs)

    def test_negative_max_retries_rejected(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["max_retries"] = -1
        with pytest.raises(ValidationError):
            RoutingEntry.model_validate(kwargs)

    def test_max_retries_above_upper_bound_rejected(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["max_retries"] = 10
        with pytest.raises(ValidationError):
            RoutingEntry.model_validate(kwargs)

    def test_temperature_required(self) -> None:
        kwargs = _valid_routing_kwargs()
        del kwargs["temperature"]
        with pytest.raises(ValidationError):
            RoutingEntry.model_validate(kwargs)

    def test_temperature_below_zero_rejected(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["temperature"] = -0.1
        with pytest.raises(ValidationError):
            RoutingEntry.model_validate(kwargs)

    def test_temperature_above_one_rejected(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["temperature"] = 1.5
        with pytest.raises(ValidationError):
            RoutingEntry.model_validate(kwargs)

    def test_temperature_zero_accepted(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["temperature"] = 0.0
        e = RoutingEntry.model_validate(kwargs)
        assert e.temperature == 0.0

    def test_temperature_one_accepted(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["temperature"] = 1.0
        e = RoutingEntry.model_validate(kwargs)
        assert e.temperature == 1.0

    def test_extra_field_rejected(self) -> None:
        kwargs = _valid_routing_kwargs()
        kwargs["unknown"] = "x"
        with pytest.raises(ValidationError):
            RoutingEntry.model_validate(kwargs)
