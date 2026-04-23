"""Integration test: one real Qwen call + Langfuse trace assertion.

Scope:
  - Makes an actual billable API call to DashScope (qwen-turbo,
    capped at 20 output tokens — well under $0.001).
  - Verifies Langfuse received the trace by querying the Langfuse
    HTTP API via its SDK (not by eyeballing the UI).

Marker:
  Tagged ``@pytest.mark.integration``. Default pytest invocation
  skips these (see ``pyproject.toml`` ``addopts``); run explicitly
  with ``uv run pytest -m integration``.

Why one smoke test, not a matrix:
  Each LLMPurpose will exercise its own path as business modules
  land (intent classifier in Week 2, retrieval selector, etc.).
  Here we just need end-to-end confidence that call_llm ->
  LiteLLM -> vendor -> Langfuse plumbing is wired correctly.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Generator
from decimal import Decimal

import pytest

from app.config import settings
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
from app.modules.model_gateway.schema import Message
from app.shared.enums import LLMPurpose

_LANGFUSE_ENV_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST")

# Upload-delay budget for Langfuse's async callback. The LiteLLM
# Langfuse integration flushes on a worker thread; 3s is enough on a
# local dev machine. Increase if CI ever hosts this test.
_LANGFUSE_UPLOAD_WAIT_S = 3.0


def _looks_placeholder(value: str) -> bool:
    """Heuristic: values containing ``your`` / ``xxxxx`` / ``placeholder``
    or blank are considered unconfigured. Matches the template form in
    ``.env.example``."""
    if not value:
        return True
    lowered = value.lower()
    return any(token in lowered for token in ("your", "xxxxx", "placeholder"))


@pytest.fixture
def integration_prereqs() -> None:
    """Skip the test when credentials are not real.

    Keeps the test present so future engineers can `pytest -m
    integration` without manual setup, but avoids burning a real
    DashScope call (and a confusing 401) when the ``.env`` is a
    fresh checkout.
    """
    if _looks_placeholder(settings.DASHSCOPE_API_KEY):
        pytest.skip("DASHSCOPE_API_KEY is placeholder / empty")
    if _looks_placeholder(settings.LANGFUSE_PUBLIC_KEY):
        pytest.skip("LANGFUSE_PUBLIC_KEY is placeholder / empty")
    if _looks_placeholder(settings.LANGFUSE_SECRET_KEY):
        pytest.skip("LANGFUSE_SECRET_KEY is placeholder / empty")


@pytest.fixture(autouse=True)
def _reset_state() -> Generator[None, None, None]:
    """Reset gateway + router singletons and clean Langfuse env leakage."""
    _reset_gateway()
    _reset_router()
    for k in _LANGFUSE_ENV_KEYS:
        os.environ.pop(k, None)
    yield
    _reset_gateway()
    _reset_router()


@pytest.mark.integration
async def test_real_call_chitchat_and_verify_langfuse_trace(
    integration_prereqs: None,
) -> None:
    """End-to-end smoke: CHITCHAT -> qwen-turbo -> Langfuse trace.

    Steps:
      1. ``initialize()`` wires routing, LiteLLM callbacks, env vars.
      2. Call ``call_llm`` with a unique ``seller_id`` so we can
         isolate this specific invocation in Langfuse later.
      3. Assert on the returned ``LLMResponse`` (shape, non-zero cost,
         primary model used).
      4. Sleep briefly so LiteLLM's Langfuse worker thread has time
         to flush the trace to the server.
      5. Query the Langfuse HTTP API via the SDK and assert the
         trace landed with the expected ``name`` / ``session_id`` /
         ``tags``.
    """
    initialize()

    run_id = uuid.uuid4().hex[:12]
    unique_seller_id = f"smoke-seller-{run_id}"
    unique_trace_id = f"smoke-trace-{run_id}"

    response = await call_llm(
        purpose=LLMPurpose.CHITCHAT,
        messages=[
            Message(
                role="user",
                content="Reply with exactly: hello world.",
            ),
        ],
        max_tokens=20,
        seller_id=unique_seller_id,
        trace_id=unique_trace_id,
    )

    # --- LLMResponse assertions -----------------------------------
    assert response.content, "response.content should not be empty"
    assert response.prompt_tokens > 0
    assert response.completion_tokens > 0
    assert response.cost_usd > Decimal("0"), "estimate_cost must produce non-zero cost"
    assert response.latency_ms > 0
    assert response.is_fallback is False
    # Matches current YAML routing for CHITCHAT (qwen-turbo primary
    # after the 2026-04-23 qwen-flash swap).
    assert response.model_used == "dashscope/qwen-turbo"

    # --- Let Langfuse finish its async upload ---------------------
    await asyncio.sleep(_LANGFUSE_UPLOAD_WAIT_S)

    # --- Query Langfuse and verify trace metadata -----------------
    # Import inside the test body so the import error surfaces as a
    # test failure rather than a collection-time explosion for
    # unit-test runs that skip this file.
    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
    )

    fetched = client.fetch_traces(session_id=unique_seller_id, limit=5)
    assert fetched.data, (
        f"no Langfuse trace found for session_id={unique_seller_id}; "
        f"Langfuse callback may not have fired. Check: (a) settings "
        f"LANGFUSE_* env vars are set correctly, (b) gateway.initialize() "
        f"wired litellm.success_callback, (c) Langfuse container is up."
    )

    matching = [t for t in fetched.data if t.name == "llm.chitchat"]
    assert matching, (
        f"Langfuse returned traces for session {unique_seller_id} but "
        f"none named 'llm.chitchat'. Actual names: "
        f"{[t.name for t in fetched.data]}"
    )
    trace = matching[0]

    assert trace.name == "llm.chitchat"
    assert trace.session_id == unique_seller_id
    tags = trace.tags or []
    assert "purpose:CHITCHAT" in tags, (
        f"expected 'purpose:CHITCHAT' in trace.tags; got {tags}"
    )
    # Primary path: fallback tag must NOT be present.
    assert "fallback:true" not in tags

    # --- Print cost so the run leaves a visible paper trail -------
    # pytest captures stdout by default; run with -s to see this
    # inline. The value also prints on failure via the assert msg.
    print(
        f"\n[INTEGRATION COST] ${response.cost_usd} USD "
        f"({response.prompt_tokens} in + {response.completion_tokens} "
        f"out tokens, {response.latency_ms}ms)",
    )
