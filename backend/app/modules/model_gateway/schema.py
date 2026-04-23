"""Model gateway data contracts.

``Message``: one element in the ``messages`` list fed into ``call_llm``.
Role uses ``Literal`` rather than a new enum because the four values
appear only here so far; promote to ``app.shared.types`` if a second
consumer needs to construct messages.

``LLMResponse``: unified return shape for every ``call_llm`` call.
Carries enough telemetry (model actually used, tokens, cost, latency,
fallback flag) for observability without leaking vendor SDK types
upstream.

``RoutingEntry``: one row from ``purpose_routing.yaml``. Lives here
(rather than in router.py) so ``capabilities.validate_routing_capabilities``
can import the shape without a circular dependency.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    """One chat message fed into ``call_llm``."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    role: Role
    content: str
    name: str | None = None
    """Optional tool / participant name; only meaningful for role='tool'."""


class LLMResponse(BaseModel):
    """Unified LLM call result."""

    # protected_namespaces=() silences the pydantic warning for the
    # ``model_used`` field (pydantic reserves the ``model_`` prefix for
    # its own methods; opting out is intentional here, not accidental).
    model_config = ConfigDict(
        frozen=False,
        extra="forbid",
        protected_namespaces=(),
    )

    content: str
    model_used: str
    """Fully-qualified LiteLLM model name that actually responded
    (may be primary or fallback; check ``is_fallback`` to distinguish)."""

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0)
    """USD cost estimate; ``Decimal`` (not ``float``) so per-1K-token
    pricing preserves precision when aggregated at seller/day scale."""

    latency_ms: int = Field(ge=0)
    is_fallback: bool = False


class RoutingEntry(BaseModel):
    """One row from ``purpose_routing.yaml``, keyed by ``LLMPurpose``.

    Built in ``router.load_routing_table`` from parsed YAML, then fed
    into ``capabilities.validate_routing_capabilities`` at startup for
    fail-fast config checking.

    ``timeout_ms`` and ``max_retries`` are bounded so typical typos
    (``timeout_ms: 3`` meaning 3s but read as 3ms; ``max_retries: 50``)
    fail at load time rather than in production.
    """

    model_config = ConfigDict(frozen=False, extra="forbid")

    primary: str
    """Fully-qualified LiteLLM model name (``vendor/model`` format)."""

    fallback: str
    """Model used when primary fails or times out."""

    timeout_ms: int = Field(
        ge=100,
        le=60000,
        description=(
            "Total timeout budget in milliseconds for a single call_llm "
            "invocation, INCLUDING any LiteLLM-internal retries. This "
            "is NOT per-attempt. See purpose_routing.yaml header for "
            "concrete examples of how the outer asyncio.wait_for "
            "deadline interacts with num_retries."
        ),
    )
    max_retries: int = Field(ge=0, le=5)

    temperature: float = Field(ge=0.0, le=1.0)
    """Default sampling temperature for this purpose.

    Required (no default) so adding a new purpose row to the YAML
    forces an explicit choice — 0.1 for deterministic classification,
    0.7 for creative generation, etc. Per-call override is possible
    via ``call_llm(..., temperature=...)`` in Step 6.
    """
