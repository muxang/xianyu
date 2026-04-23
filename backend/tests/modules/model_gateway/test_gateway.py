"""Tests for ``app.modules.model_gateway.gateway``.

Strategy:
  - ``litellm.acompletion`` is mocked with ``AsyncMock``; no network.
  - Every test starts from a reset gateway + router (autouse fixture),
    so module singletons cannot leak between tests.
  - ``capture_logs`` from ``structlog.testing`` captures structured
    events for assertion (no stdlib logging plumbing needed).

Coverage map:
  - Group A: ``initialize()`` behavior (4 tests)
  - Group B: uninitialized guard (1 test)
  - Group C: primary-path success (4 tests)
  - Group D: fallback semantics (5 tests, 3 spec + 2 extras)
  - Group E: cost / latency contract (3 tests, 2 spec + 1 extra)
  - Group F: images rejected (1 test)

Extras added above the original 15-test spec:
  - D+  fallback_auth_error_raises_not_all_models_failed
  - D+  primary_generic_error_then_fallback
  - E+  timeout_log_includes_duration_ms_field
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Generator, Mapping
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, Mock

import litellm
import pytest
from litellm import exceptions as litellm_exc
from structlog.testing import capture_logs

from app.config import settings
from app.modules.model_gateway.cost_calculator import estimate_cost
from app.modules.model_gateway.errors import (
    LLMAllModelsFailedError,
    LLMAuthenticationError,
    LLMError,
    LLMFeatureNotImplementedError,
    LLMNotInitializedError,
    LLMTimeoutError,
)
from app.modules.model_gateway.gateway import (
    _reset_for_testing as _reset_gateway,
)
from app.modules.model_gateway.gateway import (
    call_llm,
    initialize,
)
from app.modules.model_gateway.router import (
    _reset_for_testing as _reset_router,
)
from app.modules.model_gateway.router import (
    get_routing_table,
)
from app.modules.model_gateway.schema import Message
from app.shared.enums import LLMPurpose

_LANGFUSE_ENV_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")


# ---------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_gateway_state() -> Generator[None, None, None]:
    """Reset gateway + router singletons and Langfuse env leakage.

    Runs before AND after every test so no test inherits state. Also
    pops ``LANGFUSE_*`` from ``os.environ`` because ``initialize()``
    writes directly (monkeypatch cannot intercept these).
    """
    _reset_gateway()
    _reset_router()
    for k in _LANGFUSE_ENV_KEYS:
        os.environ.pop(k, None)
    yield
    _reset_gateway()
    _reset_router()
    for k in _LANGFUSE_ENV_KEYS:
        os.environ.pop(k, None)


@pytest.fixture
def initialized_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Initialize gateway with test Langfuse credentials."""
    monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setattr(settings, "LANGFUSE_HOST", "http://test:3100")
    initialize()


@pytest.fixture
def mock_litellm(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Patch ``litellm.acompletion`` with an AsyncMock for per-test config."""
    mock = AsyncMock()
    monkeypatch.setattr(litellm, "acompletion", mock)
    return mock


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def make_fake_response(
    *,
    content: str = "hi",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> Mock:
    """Minimal mock of the LiteLLM ModelResponse shape actually read
    by ``_attempt_call``: ``.choices[0].message.content``,
    ``.usage.prompt_tokens``, ``.usage.completion_tokens``.
    """
    choice = Mock()
    choice.message = Mock()
    choice.message.content = content

    usage = Mock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    response = Mock()
    response.choices = [choice]
    response.usage = usage
    return response


def _make_auth_error(message: str = "401 unauthorized") -> litellm_exc.AuthenticationError:
    """LiteLLM's AuthenticationError requires positional args; wrap."""
    return litellm_exc.AuthenticationError(
        message=message,
        llm_provider="dashscope",
        model="dashscope/qwen-turbo",
    )


def _await_kwargs(mock: AsyncMock) -> Mapping[str, Any]:
    """Typed accessor for the most recent await's kwargs.

    ``mock.await_args`` is ``_Call | None``; asserting non-None
    narrows the type and surfaces a clear error if the mock was
    never actually awaited in the test.
    """
    assert mock.await_args is not None, "mock was never awaited"
    return mock.await_args.kwargs


_BASIC_MESSAGES: list[Message] = [Message(role="user", content="hello")]


# Use ``asyncio.TimeoutError`` (not bare ``TimeoutError``) so test
# side_effects exactly match what production code catches at
# gateway.py `_attempt_call`. On Python 3.11+ they resolve to the
# same class, but the production clause is deliberately narrower
# (gateway.py has the ruff suppression explaining why). Referencing
# the asyncio-qualified name here documents the intent and keeps
# tests resilient if that clause is later rewritten to e.g.
# ``except asyncio.exceptions.TimeoutError``. Ruff UP041 would
# rewrite this alias back to builtin TimeoutError; the suppression
# on the next line blocks that auto-fix.
_AsyncTimeout = asyncio.TimeoutError  # noqa: UP041


# ---------------------------------------------------------------------
# Group A: initialize() behavior
# ---------------------------------------------------------------------


class TestInitialize:
    def test_loads_routing_and_sets_litellm_globals(
        self,
        initialized_gateway: None,
    ) -> None:
        assert get_routing_table() is not None
        assert litellm.success_callback == ["langfuse"]
        assert litellm.failure_callback == ["langfuse"]
        assert litellm.drop_params is False

    def test_syncs_langfuse_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test-xxx")
        monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test-yyy")
        monkeypatch.setattr(settings, "LANGFUSE_HOST", "http://test:3100")
        initialize()
        assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-test-xxx"
        assert os.environ["LANGFUSE_SECRET_KEY"] == "sk-test-yyy"
        assert os.environ["LANGFUSE_HOST"] == "http://test:3100"

    def test_does_not_write_empty_langfuse_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty-string credentials should not leak into os.environ;
        that would confuse LiteLLM's Langfuse integration into
        sending a half-configured request."""
        monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "")
        monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "")
        monkeypatch.setattr(settings, "LANGFUSE_HOST", "")
        initialize()
        for key in _LANGFUSE_ENV_KEYS:
            assert key not in os.environ, f"{key} should not be set with empty value"

    def test_is_idempotent(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "LANGFUSE_PUBLIC_KEY", "pk-test")
        monkeypatch.setattr(settings, "LANGFUSE_SECRET_KEY", "sk-test")
        monkeypatch.setattr(settings, "LANGFUSE_HOST", "http://test:3100")

        with capture_logs() as logs:
            initialize()
            initialize()

        events = [e["event"] for e in logs]
        assert events.count("model_gateway_initialized") == 1, (
            f"second initialize() must be a no-op; events={events}"
        )
        # Callback list must stay length 1 (replacement assignment, not append).
        assert litellm.success_callback == ["langfuse"]
        assert litellm.failure_callback == ["langfuse"]


# ---------------------------------------------------------------------
# Group B: uninitialized guard
# ---------------------------------------------------------------------


class TestUninitializedGuard:
    async def test_call_llm_raises_when_not_initialized(self) -> None:
        # autouse fixture already reset state; no initialize() called.
        with pytest.raises(LLMNotInitializedError):
            await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
            )


# ---------------------------------------------------------------------
# Group C: primary-path success
# ---------------------------------------------------------------------


class TestPrimaryPath:
    async def test_success_returns_wellformed_response(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.return_value = make_fake_response(
            content="hi there",
            prompt_tokens=10,
            completion_tokens=5,
        )
        resp = await call_llm(
            purpose=LLMPurpose.CHITCHAT,
            messages=_BASIC_MESSAGES,
        )
        # CHITCHAT primary is qwen-turbo per current YAML.
        assert resp.content == "hi there"
        assert resp.model_used == "dashscope/qwen-turbo"
        assert resp.prompt_tokens == 10
        assert resp.completion_tokens == 5
        assert resp.is_fallback is False
        assert resp.cost_usd > Decimal("0")
        assert resp.latency_ms >= 0
        assert mock_litellm.await_count == 1

    async def test_metadata_shape(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.return_value = make_fake_response()
        await call_llm(
            purpose=LLMPurpose.CHITCHAT,
            messages=_BASIC_MESSAGES,
            seller_id="seller-123",
            trace_id="trace-abc",
        )
        kwargs = _await_kwargs(mock_litellm)
        metadata = kwargs["metadata"]
        assert metadata["trace_name"] == "llm.chitchat"
        assert "purpose:CHITCHAT" in metadata["tags"]
        assert "fallback:true" not in metadata["tags"]
        assert metadata["session_id"] == "seller-123"
        assert metadata["trace_id"] == "trace-abc"

    async def test_passes_optional_kwargs(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.return_value = make_fake_response()
        tool_spec: list[dict[str, Any]] = [
            {"type": "function", "function": {"name": "search"}},
        ]
        await call_llm(
            purpose=LLMPurpose.CHITCHAT,
            messages=_BASIC_MESSAGES,
            response_format="json_object",
            max_tokens=100,
            tools=tool_spec,
        )
        kwargs = _await_kwargs(mock_litellm)
        assert kwargs["response_format"] == {"type": "json_object"}
        assert kwargs["max_tokens"] == 100
        assert kwargs["tools"] == tool_spec

    async def test_temperature_override(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.return_value = make_fake_response()
        # Override: CHITCHAT YAML default is 0.7; override to 0.5.
        await call_llm(
            purpose=LLMPurpose.CHITCHAT,
            messages=_BASIC_MESSAGES,
            temperature=0.5,
        )
        assert _await_kwargs(mock_litellm)["temperature"] == 0.5

        # Default: no temperature passed -> use entry's 0.7.
        mock_litellm.reset_mock()
        mock_litellm.return_value = make_fake_response()
        await call_llm(
            purpose=LLMPurpose.CHITCHAT,
            messages=_BASIC_MESSAGES,
        )
        assert _await_kwargs(mock_litellm)["temperature"] == 0.7


# ---------------------------------------------------------------------
# Group D: fallback semantics
# ---------------------------------------------------------------------


class TestFallback:
    async def test_primary_timeout_then_fallback_success(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.side_effect = [
            _AsyncTimeout(),
            make_fake_response(content="from fallback"),
        ]
        with capture_logs() as logs:
            resp = await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
            )
        assert resp.is_fallback is True
        # CHITCHAT fallback is qwen-plus.
        assert resp.model_used == "dashscope/qwen-plus"
        assert resp.content == "from fallback"
        assert mock_litellm.await_count == 2

        # Second call's metadata tags must include fallback:true.
        second_kwargs = mock_litellm.await_args_list[1].kwargs
        assert "fallback:true" in second_kwargs["metadata"]["tags"]

        events = [e["event"] for e in logs]
        assert "llm_primary_failed" in events
        assert "llm_timeout" in events

    async def test_primary_and_fallback_both_timeout_raises(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.side_effect = [
            _AsyncTimeout(),
            _AsyncTimeout(),
        ]
        with capture_logs() as logs, pytest.raises(LLMAllModelsFailedError) as exc_info:
            await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
            )

        # Exception chain: LLMAllModelsFailedError -> LLMTimeoutError (fallback).
        assert isinstance(exc_info.value.__cause__, LLMTimeoutError)

        # Primary error captured on the attribute so incident triage
        # code can inspect both attempts without re-reading logs.
        assert isinstance(exc_info.value.primary_error, LLMTimeoutError)

        events = [e["event"] for e in logs]
        assert "llm_all_models_failed" in events

    async def test_primary_auth_error_does_not_fallback(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.side_effect = [_make_auth_error()]
        with pytest.raises(LLMAuthenticationError):
            await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
            )
        # Only primary attempted; fallback must NOT be invoked.
        assert mock_litellm.await_count == 1

    # -- Extras beyond the original spec --

    async def test_fallback_auth_error_raises_as_auth_not_all_models_failed(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        """When primary fails non-auth and fallback hits auth, the
        raised exception must be ``LLMAuthenticationError`` (routes
        alerts to the key-rotation runbook), NOT
        ``LLMAllModelsFailedError`` (which routes to vendor-outage)."""
        mock_litellm.side_effect = [
            _AsyncTimeout(),
            _make_auth_error("fallback 401"),
        ]
        with pytest.raises(LLMAuthenticationError):
            await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
            )
        assert mock_litellm.await_count == 2

    async def test_primary_generic_error_then_fallback_succeeds(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        """Non-timeout, non-auth primary failure (e.g. bad gateway)
        should also trigger fallback."""
        mock_litellm.side_effect = [
            RuntimeError("upstream 502"),
            make_fake_response(content="fallback saved"),
        ]
        resp = await call_llm(
            purpose=LLMPurpose.CHITCHAT,
            messages=_BASIC_MESSAGES,
        )
        assert resp.is_fallback is True
        assert resp.content == "fallback saved"
        assert mock_litellm.await_count == 2


# ---------------------------------------------------------------------
# Group E: cost / latency contract
# ---------------------------------------------------------------------


class TestCostAndLatency:
    async def test_cost_computed_from_usage_and_model(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.return_value = make_fake_response(
            prompt_tokens=1000,
            completion_tokens=500,
        )
        resp = await call_llm(
            purpose=LLMPurpose.CHITCHAT,
            messages=_BASIC_MESSAGES,
        )
        expected = estimate_cost(
            model="dashscope/qwen-turbo",
            prompt_tokens=1000,
            completion_tokens=500,
        )
        assert resp.cost_usd == expected

    async def test_timeout_does_not_record_cost(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        """Both primary AND fallback timeout -> raises; no LLMResponse
        constructed, therefore cost is not part of any response object."""
        mock_litellm.side_effect = [
            _AsyncTimeout(),
            _AsyncTimeout(),
        ]
        with pytest.raises(LLMAllModelsFailedError):
            await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
            )
        # No assertion needed on "cost"; the control path simply never
        # reaches LLMResponse construction. Keeping the test for
        # regression signal if someone later adds a "partial cost on
        # timeout" feature.

    # -- Extra --

    async def test_timeout_log_includes_duration_ms_field(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        """Per risk-3 design decision, the timeout warn line carries
        both ``timeout_ms`` (configured ceiling) and ``duration_ms``
        (actual elapsed). If duration much exceeds timeout, cancel
        did not propagate in time -- a valuable ops signal."""
        mock_litellm.side_effect = [
            _AsyncTimeout(),
            make_fake_response(),  # fallback succeeds so test doesn't raise
        ]
        with capture_logs() as logs:
            await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
            )
        timeout_events = [e for e in logs if e["event"] == "llm_timeout"]
        assert len(timeout_events) == 1
        event = timeout_events[0]
        assert "timeout_ms" in event
        assert "duration_ms" in event
        assert event["is_fallback"] is False


# ---------------------------------------------------------------------
# Group F: images rejected
# ---------------------------------------------------------------------


class TestImagesRejected:
    async def test_images_raises_llm_feature_not_implemented_error(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        mock_litellm.return_value = make_fake_response()
        with pytest.raises(LLMFeatureNotImplementedError, match="vision"):
            await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
                images=["https://example.com/img.jpg"],
            )
        # Vision path raises BEFORE hitting litellm.acompletion.
        assert mock_litellm.await_count == 0

    async def test_images_error_is_catchable_as_llm_error(
        self,
        initialized_gateway: None,
        mock_litellm: AsyncMock,
    ) -> None:
        """Callers writing ``except LLMError:`` in a single handler
        must catch vision-rejection, not only runtime failures."""
        mock_litellm.return_value = make_fake_response()
        with pytest.raises(LLMError):
            await call_llm(
                purpose=LLMPurpose.CHITCHAT,
                messages=_BASIC_MESSAGES,
                images=["https://example.com/img.jpg"],
            )
