"""USD cost estimation for a single LLM call.

This module is the authoritative source for "what did that call
cost?". It is deliberately an **estimate** — aggregated per-seller /
per-day totals compared against vendor invoices will always diverge
slightly due to exchange-rate drift, tiered-price underestimate, and
vendor-side rounding. The numbers here are good enough for
observability and alerting, not billing.

Design decisions:

  - Prices stored once in source-currency (CNY for Qwen, USD for
    DeepSeek) so the link back to the vendor doc stays obvious.
    ``MODEL_PRICES`` is derived at import time, one USD conversion
    per Qwen row — no per-model ``× rate`` hard-codes.

  - Exchange rate lives in a single constant (``USD_PER_CNY``).
    Revisiting once per quarter touches exactly one line.

  - Qwen pricing is TIERED. The 0-128K input tier is represented here;
    above 128K a single warn-log fires so ops can spot cost
    underestimation without needing per-tier billing logic (YAGNI).

  - Unknown / placeholder models are handled by two distinct warn
    events (``unknown_model_price`` vs ``unverified_model_price``)
    so alert rules can route them differently.

Exchange rate complacency risk: if CNY/USD moves >5%, numbers drift
quietly. The note on ``USD_PER_CNY`` tells the next maintainer to
recalibrate quarterly; pair with a Grafana "invoice vs estimate"
delta panel for early warning (Week 3+).
"""

from __future__ import annotations

from decimal import Context, Decimal
from typing import Final

import structlog

log = structlog.get_logger(__name__)

# Dedicated Context so the derivation of USD_PER_CNY is pinned at
# prec=28 regardless of whether some other module has mutated the
# global decimal context (``getcontext().prec = ...``). Without this,
# a process-wide precision change plus a ``importlib.reload`` could
# silently re-derive the rate at reduced precision.
_FX_CONTEXT: Final[Context] = Context(prec=28)

USD_PER_CNY: Final[Decimal] = _FX_CONTEXT.divide(Decimal("1"), Decimal("7.2"))
"""CNY -> USD conversion rate as of 2026-04 (~7.2 CNY/USD).

Re-verify every ~3 months against a live FX source. When the spot
rate drifts >5% from 7.2, update this single line; no per-model
number needs to change.

This is an estimate for observability, not billing. Precision is
pinned at 28 digits via ``_FX_CONTEXT`` so future changes to the
process-wide decimal context cannot silently corrupt this value.
"""

TIER1_TOKEN_LIMIT: Final[int] = 128_000
"""Qwen DashScope pricing is tiered on input size. Below 128K we
use tier-1 rates verbatim. Above 128K the estimator stays at tier-1
(cost will underestimate) but logs a warning so ops can flag long-
prompt workloads that need vendor-invoice reconciliation."""


# Per-model source-currency prices, (input per 1M tokens, output per 1M tokens).
# CNY prices are 0-128K tier only (all Qwen models are tiered).
#
# Source: https://help.aliyun.com/zh/model-studio/models
# Verified: 2026-04-23 via WebFetch; revisit when help.aliyun.com
# indicates a pricing change (Aliyun occasionally rebalances tiers).
_QWEN_PRICES_CNY: dict[str, tuple[Decimal, Decimal]] = {
    "dashscope/qwen-turbo": (Decimal("0.3"), Decimal("0.6")),
    "dashscope/qwen-plus": (Decimal("0.8"), Decimal("2.0")),
    "dashscope/qwen-flash": (Decimal("0.15"), Decimal("1.5")),
    "dashscope/qwen-max": (Decimal("2.4"), Decimal("9.6")),
    "dashscope/qwen-vl-max": (Decimal("1.6"), Decimal("4.0")),
    "dashscope/qwen-vl-plus": (Decimal("0.8"), Decimal("2.0")),
}


# Public USD price table. Qwen rows derived from CNY × USD_PER_CNY at
# import time (one multiplication per model); DeepSeek joins as-is
# since it's already priced in USD.
#
# DeepSeek source: https://api-docs.deepseek.com/quick_start/pricing
# Verified: 2026-04-23. Cache-miss input price used because we do
# not configure prompt caching (every request carries unique
# buyer/conversation context, hit rate would be ~0).
MODEL_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    **{
        model: (cny_in * USD_PER_CNY, cny_out * USD_PER_CNY)
        for model, (cny_in, cny_out) in _QWEN_PRICES_CNY.items()
    },
    "deepseek/deepseek-chat": (Decimal("0.28"), Decimal("0.42")),
}


# Set of models whose per-token price is tier-dependent on input size.
# Above-tier1 warn fires only for these (DeepSeek is flat-rate so we
# skip the warn there to avoid spurious alerts).
_TIERED_MODELS: Final[frozenset[str]] = frozenset(_QWEN_PRICES_CNY.keys())


def estimate_cost(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> Decimal:
    """Estimate USD cost for a single LLM call.

    Returns exact ``Decimal`` cost; does not round. Aggregation
    precision matters at seller / day scale (>10k calls summed).

    Three warn-log branches:

    - ``unknown_model_price``:  ``model`` has no MODEL_PRICES entry
      (almost always a routing bug — a new model slipped in without
      a verified price). Returns ``Decimal("0")``.

    - ``unverified_model_price``: ``model`` is in MODEL_PRICES but
      both prices are 0 (placeholder row for a model whose pricing
      is pending verification). Returns ``Decimal("0")``. Distinct
      from the above so alerts can distinguish "bug" from "ops
      task". No model currently hits this branch after the 2026-04
      Zhipu cleanup, but the hook is kept for future placeholders.

    - ``cost_estimate_above_tier1``: model is tiered (all Qwen) and
      ``prompt_tokens`` exceeds the tier-1 ceiling. Non-fatal; cost
      is still returned but will underestimate. Useful for ops to
      spot workloads that have moved past tier-1 and need vendor
      invoice reconciliation.
    """
    if model not in MODEL_PRICES:
        log.warning(
            "unknown_model_price",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return Decimal("0")

    input_price, output_price = MODEL_PRICES[model]

    if input_price == 0 and output_price == 0:
        log.warning(
            "unverified_model_price",
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return Decimal("0")

    if model in _TIERED_MODELS and prompt_tokens > TIER1_TOKEN_LIMIT:
        log.warning(
            "cost_estimate_above_tier1",
            model=model,
            prompt_tokens=prompt_tokens,
            tier1_limit=TIER1_TOKEN_LIMIT,
            note="cost underestimated; tier-2/3 rates apply at vendor",
        )

    return (
        Decimal(prompt_tokens) * input_price
        + Decimal(completion_tokens) * output_price
    ) / Decimal("1000000")
