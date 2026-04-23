"""Model gateway exception hierarchy.

Every module-local error inherits from ``LLMError`` so a caller can
catch the whole family with a single ``except``. Concrete subclasses
let upstream code route on the specific failure mode:

- ``LLMTimeoutError``:           ``asyncio.wait_for`` cancelled the call
- ``LLMAuthenticationError``:    401 / 403 from the vendor (no fallback)
- ``LLMAllModelsFailedError``:   both primary and fallback failed
- ``RoutingConfigError``:        startup-time YAML / capability mismatch
- ``LLMNotInitializedError``:    ``call_llm`` invoked before ``initialize()``

``LLMTimeoutError`` does NOT inherit from the builtin ``TimeoutError``
on purpose: upstream code should only swallow gateway-origin timeouts
through our specific type, not accidentally absorb unrelated stdlib
timeouts raised elsewhere in the stack.
"""

from __future__ import annotations


class LLMError(Exception):
    """Base class for every model gateway error."""


class LLMTimeoutError(LLMError):
    """A single LLM attempt (primary OR fallback) exceeded its configured timeout."""


class LLMAuthenticationError(LLMError):
    """Vendor returned 401 / 403.

    Intentionally does NOT trigger fallback. Auth failures typically
    indicate revoked or expired credentials that will affect the
    fallback route as well (same key pool, or correlated rotation
    schedules). Falling back would add 5-10s of latency to a call
    that is almost certainly doomed; raising fast lets ops rotate
    keys immediately and keeps user-visible latency bounded.

    If a future workload has genuinely independent primary /
    fallback credentials that benefits from auth-level rescue, add a
    per-route ``allow_auth_fallback: bool`` to ``RoutingEntry``
    rather than flipping this default.
    """


class LLMAllModelsFailedError(LLMError):
    """Both primary and fallback failed for a single ``call_llm`` invocation.

    ``__cause__`` (set via ``raise ... from fallback_err``) carries
    the fallback-attempt error. The primary-attempt error is attached
    as ``primary_error`` so incident-triage code can inspect both
    without re-reading logs.
    """

    def __init__(
        self,
        message: str,
        primary_error: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.primary_error = primary_error


class RoutingConfigError(LLMError):
    """``purpose_routing.yaml`` violates capability requirements; fail-fast at load."""


class LLMNotInitializedError(LLMError):
    """``call_llm`` was invoked before ``initialize()``.

    Guards against the silent-first-call-loads-everything anti-pattern:
    the gateway refuses to serve traffic until ``initialize()`` has
    run explicitly, so a missing ``lifespan`` wire-up surfaces here
    at the first request rather than as a subtle behavior change.
    """


class LLMFeatureNotImplementedError(LLMError):
    """``call_llm`` was invoked with a feature declared in the API but
    not yet wired end-to-end (e.g. vision inputs).

    Distinct from stdlib ``NotImplementedError`` so callers writing
    ``except LLMError:`` in a single error-handling block catch this
    alongside runtime failures. Stdlib ``NotImplementedError`` signals
    "must be overridden by subclass"; our semantic is "the gateway
    surface accepts this parameter but the downstream path is
    pending first integration test" — different error categories.
    """
