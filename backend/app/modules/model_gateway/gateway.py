"""Runtime entry point: the single ``call_llm`` every business module uses.

Responsibilities:

  - ``initialize()``: idempotent startup wiring (routing table, Langfuse
    env plumbing, LiteLLM global config). Called once from
    ``main.py`` lifespan before traffic is accepted.

  - ``call_llm(purpose, messages, ...)``: the unified runtime API.
    Looks up the routing entry by purpose, tries primary, falls back
    to the backup model on any non-auth failure, and returns a single
    ``LLMResponse``.

Timeout / retry split (per Step 6 design brief):

  - ``asyncio.wait_for`` wraps every attempt (outer layer).
  - ``litellm.acompletion(num_retries=N)`` handles in-model 429 / 5xx
    retries with exponential backoff (inner layer).
  - LiteLLM is NOT given its own timeout — its cross-vendor timeout
    implementation diverges; ``asyncio.wait_for`` is deterministic.
  - On ``wait_for`` cancellation the attempt is over: no cost record,
    no partial ``LLMResponse``. LiteLLM's ``failure_callback`` still
    reports the failed trace to Langfuse automatically.

Auth failures (401 / 403) are NOT retried or failed-over to the
backup model — see ``LLMAuthenticationError`` for rationale. The
fallback is tried only for non-auth, non-initialized failures.

Langfuse metadata is built once per ``call_llm`` call and passed
through both attempts; the fallback attempt appends a ``fallback:true``
tag to its own copy so Grafana dashboards can see fallback rate by
purpose.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import litellm
import structlog
from litellm import exceptions as litellm_exc

from app.config import settings
from app.shared.enums import LLMPurpose

from .cost_calculator import estimate_cost
from .errors import (
    LLMAllModelsFailedError,
    LLMAuthenticationError,
    LLMFeatureNotImplementedError,
    LLMNotInitializedError,
    LLMTimeoutError,
)
from .router import load_routing_table, select
from .schema import LLMResponse, Message, RoutingEntry

log = structlog.get_logger(__name__)


# Module-level init guard. Set to True by initialize() after routing
# and LiteLLM are wired; checked by call_llm() on every invocation.
_initialized: bool = False


def initialize() -> None:
    """Wire the gateway for runtime. Idempotent; call once at startup.

    **Concurrency**: Intended to be called exactly once from the
    FastAPI ``lifespan`` startup hook, which is single-threaded —
    ``_initialized`` is a plain module-level flag with no lock. Do
    NOT call from multiple tasks / threads concurrently; the
    ``if _initialized: return`` guard is a race between check and
    set, and two concurrent callers would both reach
    ``load_routing_table()`` and redundantly re-register LiteLLM
    callbacks. If a future caller genuinely needs concurrent init,
    wrap the body in an ``asyncio.Lock`` and make this function
    async.

    Steps (order matters):

      1. ``load_routing_table()`` — fail-fast on bad YAML / capability
         mismatch; populates the router's cached table.
      2. Mirror ``LANGFUSE_*`` from ``settings`` into ``os.environ``
         because LiteLLM's Langfuse integration reads env vars
         directly, not our Settings object.
      3. Register LiteLLM global callbacks (``success_callback`` /
         ``failure_callback``). Set ``drop_params=False`` explicitly:
         silently dropping unsupported params (e.g. a VL model
         response_format) would violate our fail-fast stance.
      4. Flip ``_initialized`` and emit a startup log line.

    Calling twice sequentially is a no-op (guards against test
    environments that accumulate callbacks and duplicate traces).
    """
    global _initialized
    if _initialized:
        return

    load_routing_table()

    if settings.LANGFUSE_PUBLIC_KEY:
        os.environ["LANGFUSE_PUBLIC_KEY"] = settings.LANGFUSE_PUBLIC_KEY
    if settings.LANGFUSE_SECRET_KEY:
        os.environ["LANGFUSE_SECRET_KEY"] = settings.LANGFUSE_SECRET_KEY
    if settings.LANGFUSE_HOST:
        os.environ["LANGFUSE_HOST"] = settings.LANGFUSE_HOST

    litellm.success_callback = ["langfuse"]
    litellm.failure_callback = ["langfuse"]
    litellm.drop_params = False

    _initialized = True
    log.info(
        "model_gateway_initialized",
        langfuse_host=settings.LANGFUSE_HOST,
        langfuse_configured=bool(
            settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY,
        ),
    )


def _reset_for_testing() -> None:
    """Clear the init flag. Tests only — never call in production."""
    global _initialized
    _initialized = False


def _messages_to_litellm(
    messages: list[Message],
    images: list[str] | None,
) -> list[dict[str, Any]]:
    """Map our ``Message`` list to LiteLLM's OpenAI-format dict list.

    TODO(vision): Blocked on first integration test for
    IMAGE_UNDERSTANDING purpose (Week 2 when retrieval layer lands).
    LiteLLM vision format varies by vendor (DashScope qwen-vl vs
    OpenAI-style); defer implementation until a real call can
    validate the format end-to-end.
    """
    if images is not None:
        raise LLMFeatureNotImplementedError(
            "vision input not yet wired; blocked on Week 2 integration test "
            "(see _messages_to_litellm TODO)",
        )
    return [m.model_dump(exclude_none=True) for m in messages]


async def _attempt_call(
    *,
    model: str,
    entry: RoutingEntry,
    messages: list[Message],
    response_format: str | None,
    tools: list[dict[str, Any]] | None,
    images: list[str] | None,
    temperature: float | None,
    max_tokens: int | None,
    metadata_base: dict[str, Any],
    is_fallback: bool,
    purpose_value: str,
    trace_id: str | None,
    seller_id: str | None,
) -> LLMResponse:
    """Run one attempt against ``model`` (primary OR fallback) and build
    an ``LLMResponse`` on success.

    Kept private / keyword-only so the call site reads straightforwardly
    and the two invocations from ``call_llm`` differ only in ``model``
    and ``is_fallback``.

    **Timeout semantics**: ``asyncio.wait_for(..., timeout=entry.timeout_ms/1000)``
    wraps the ENTIRE ``litellm.acompletion(num_retries=N)`` call, so
    ``timeout_ms`` is the *total budget* for this single attempt
    including every internal LiteLLM retry. It is NOT a per-attempt
    cap — a slow first try can exhaust the budget before any retry
    fires. See the purpose_routing.yaml header for concrete examples
    and the two documented refactor paths if future workloads need
    strict per-attempt semantics.

    Raises:
        LLMTimeoutError: the outer ``asyncio.wait_for`` budget expired.
        LLMAuthenticationError: vendor returned 401 / 403.
        Other ``Exception`` subclasses: LiteLLM-origin failure that
            the caller must decide how to handle (primary -> fallback;
            fallback -> wrap as LLMAllModelsFailedError).
    """
    # Shallow-copy metadata so the fallback attempt's ``fallback:true``
    # tag does not mutate the shared base.
    metadata: dict[str, Any] = {
        **metadata_base,
        "tags": [*metadata_base["tags"]],
    }
    if is_fallback:
        metadata["tags"].append("fallback:true")

    effective_temperature = (
        temperature if temperature is not None else entry.temperature
    )

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": _messages_to_litellm(messages, images),
        "temperature": effective_temperature,
        "num_retries": entry.max_retries,
        "metadata": metadata,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if response_format is not None:
        kwargs["response_format"] = {"type": response_format}
    if tools is not None:
        kwargs["tools"] = tools

    started = time.monotonic()

    try:
        response: Any = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=entry.timeout_ms / 1000.0,
        )
    except asyncio.TimeoutError as e:  # noqa: UP041
        # asyncio.TimeoutError (not the bare TimeoutError) is the
        # precise type raised by asyncio.wait_for. On 3.11+ it's the
        # same class object, but spelling it out keeps the intent
        # clear and avoids accidentally catching unrelated
        # socket-level TimeoutError (OSError subclass) that LiteLLM
        # may raise and retry internally. Ruff UP041 wants to rewrite
        # this to bare TimeoutError; noqa preserves the deliberate
        # semantic distinction.
        duration_ms = int((time.monotonic() - started) * 1000)
        log.warning(
            "llm_timeout",
            purpose=purpose_value,
            model=model,
            is_fallback=is_fallback,
            timeout_ms=entry.timeout_ms,
            duration_ms=duration_ms,
            trace_id=trace_id,
            seller_id=seller_id,
        )
        raise LLMTimeoutError(
            f"{purpose_value} "
            f"{'fallback' if is_fallback else 'primary'} "
            f"{model!r} exceeded {entry.timeout_ms}ms",
        ) from e
    except litellm_exc.AuthenticationError as e:
        log.error(
            "llm_auth_error",
            purpose=purpose_value,
            model=model,
            is_fallback=is_fallback,
            error=str(e),
            trace_id=trace_id,
            seller_id=seller_id,
        )
        raise LLMAuthenticationError(
            f"vendor auth failed for {model!r}: {e}",
        ) from e
    # Other exceptions (non-auth, non-timeout) propagate to call_llm,
    # which decides whether to try fallback based on which attempt
    # this was.

    latency_ms = int((time.monotonic() - started) * 1000)
    content = response.choices[0].message.content or ""
    usage = response.usage

    # estimate_cost internally warns (unknown_model_price /
    # unverified_model_price / cost_estimate_above_tier1); do not
    # re-log those conditions here.
    cost = estimate_cost(
        model=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
    )

    # Success log with cost so Grafana / Langfuse dashboards can
    # aggregate spend per purpose / model / seller. structlog's
    # default JSON renderer does not know Decimal; cast to float for
    # serialization. The LLMResponse itself still carries the full
    # Decimal precision for caller-side aggregation.
    log.info(
        "llm_call_success",
        purpose=purpose_value,
        model=model,
        is_fallback=is_fallback,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        latency_ms=latency_ms,
        cost_usd=float(cost),
        trace_id=trace_id,
        seller_id=seller_id,
    )

    return LLMResponse(
        content=content,
        model_used=model,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        cost_usd=cost,
        latency_ms=latency_ms,
        is_fallback=is_fallback,
    )


async def call_llm(
    purpose: LLMPurpose,
    messages: list[Message],
    *,
    response_format: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    images: list[str] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    seller_id: str | None = None,
    trace_id: str | None = None,
) -> LLMResponse:
    """Execute one LLM call, routed by ``purpose``, with fallback.

    Flow:
        1. Look up the ``RoutingEntry`` for ``purpose``.
        2. Build the shared Langfuse ``metadata_base`` (trace_name,
           session_id, tags).
        3. Attempt ``entry.primary``. On success, return.
        4. On ``LLMAuthenticationError``, re-raise immediately — no
           fallback for auth issues (see exception docstring).
        5. On any other exception, log and attempt ``entry.fallback``.
        6. On fallback success, return with ``is_fallback=True``.
        7. On fallback auth error, re-raise.
        8. On fallback non-auth failure, wrap the latest error as
           ``LLMAllModelsFailedError`` and raise.

    ``RoutingEntry.fallback`` is a required string in the schema, so
    there is no None-check here; if the YAML is ever changed to make
    fallback optional, add the guard.

    Args:
        purpose: selects the routing entry (primary / fallback /
            timeout / retries / default temperature).
        messages: chat history + current prompt.
        response_format: "json_object" for JSON Mode; None for free
            text. Translated to LiteLLM's ``{"type": ...}`` shape.
        tools: OpenAI-style tool / function-calling specs; passed
            through verbatim.
        images: image URLs / base64. Step-6 later scope — currently
            raises NotImplementedError (see _messages_to_litellm).
        temperature: per-call override for the YAML default.
        max_tokens: caller-imposed completion ceiling.
        seller_id: tagged as Langfuse ``session_id`` when non-None.
        trace_id: tagged as Langfuse ``trace_id`` when non-None.

    Raises:
        LLMNotInitializedError: ``initialize()`` was never called.
        LLMTimeoutError: primary AND fallback both timed out (fallback
            raises its own LLMTimeoutError which is then wrapped).
            Actually — any primary LLMTimeoutError triggers the
            fallback path; only a fallback timeout propagates as
            LLMAllModelsFailedError. See control-flow for nuance.
        LLMAuthenticationError: either attempt returned 401 / 403.
        LLMAllModelsFailedError: both attempts failed with non-auth
            errors.
    """
    if not _initialized:
        raise LLMNotInitializedError(
            "model_gateway.initialize() must be called at app startup "
            "before call_llm (see main.py lifespan)",
        )

    # Vision input is a user-level input constraint, not a runtime
    # failure. Reject BEFORE the primary try/except so the error
    # propagates cleanly instead of being swept into the fallback
    # path (where fallback would hit the same rejection and the
    # final exception would be LLMAllModelsFailedError -- wrong
    # category for a "feature not wired" condition).
    # _messages_to_litellm keeps the same check for defense-in-depth.
    if images is not None:
        raise LLMFeatureNotImplementedError(
            "vision input not yet wired; blocked on Week 2 integration test "
            "(see _messages_to_litellm TODO)",
        )

    entry = select(purpose)

    # Langfuse metadata shared across primary + fallback attempts.
    # ``tags`` is a list so the fallback attempt can append without
    # mutating the base (each attempt deep-copies tags inside
    # _attempt_call).
    metadata_base: dict[str, Any] = {
        "trace_name": f"llm.{purpose.value.lower()}",
        "tags": [f"purpose:{purpose.value}"],
    }
    if trace_id is not None:
        metadata_base["trace_id"] = trace_id
    if seller_id is not None:
        metadata_base["session_id"] = seller_id

    # Capture primary error across scopes: Python's ``except ... as
    # err:`` clears the binding when the block exits, so primary_err
    # would be undefined by the time we reach the fallback handler.
    primary_error: Exception | None = None

    # --- Primary attempt -------------------------------------------------
    try:
        return await _attempt_call(
            model=entry.primary,
            entry=entry,
            messages=messages,
            response_format=response_format,
            tools=tools,
            images=images,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata_base=metadata_base,
            is_fallback=False,
            purpose_value=purpose.value,
            trace_id=trace_id,
            seller_id=seller_id,
        )
    except LLMAuthenticationError:
        # Auth credentials are almost certainly broken for fallback
        # too (shared key pool / correlated rotation). Bubble up so
        # ops can rotate keys immediately.
        raise
    # TODO(future): If we add exceptions that indicate *request-level*
    # errors (e.g., LLMInvalidRequestError for schema violations, or
    # litellm_exc.ContextLengthExceededError for context overflow),
    # list them as explicit ``except ... : raise`` clauses ABOVE this
    # line — those errors will hit fallback with the same broken
    # input and waste vendor quota. Current state: only
    # LLMAuthenticationError is in that "do not fall back" category.
    except Exception as primary_err:
        primary_error = primary_err
        log.warning(
            "llm_primary_failed",
            purpose=purpose.value,
            model=entry.primary,
            error_type=type(primary_err).__name__,
            error_msg=str(primary_err)[:200],
            trace_id=trace_id,
            seller_id=seller_id,
        )

    # --- Fallback attempt ------------------------------------------------
    try:
        return await _attempt_call(
            model=entry.fallback,
            entry=entry,
            messages=messages,
            response_format=response_format,
            tools=tools,
            images=images,
            temperature=temperature,
            max_tokens=max_tokens,
            metadata_base=metadata_base,
            is_fallback=True,
            purpose_value=purpose.value,
            trace_id=trace_id,
            seller_id=seller_id,
        )
    except LLMAuthenticationError:
        # Fallback also auth-broken. Upstream config issue; let it
        # surface as auth error (not "all models failed") so alerts
        # route to the key-rotation runbook, not the vendor-outage
        # runbook.
        raise
    # Fallback: any exception reaching this handler means both
    # attempts have been exhausted. Wrap as LLMAllModelsFailedError
    # so callers can route on "complete outage" vs transient issues.
    except Exception as fallback_err:
        log.error(
            "llm_all_models_failed",
            purpose=purpose.value,
            primary=entry.primary,
            fallback=entry.fallback,
            fallback_error_type=type(fallback_err).__name__,
            fallback_error_msg=str(fallback_err)[:200],
            trace_id=trace_id,
            seller_id=seller_id,
        )
        raise LLMAllModelsFailedError(
            f"{purpose.value}: both primary ({entry.primary}) and "
            f"fallback ({entry.fallback}) failed",
            primary_error=primary_error,
        ) from fallback_err
