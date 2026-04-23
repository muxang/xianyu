"""Tests for ``app.modules.model_gateway.cost_calculator``.

Covers:

  - Happy-path arithmetic for both native-USD (DeepSeek) and
    CNY-derived (Qwen) prices.
  - Decimal precision preservation (no float contamination).
  - The three warn-log branches: unknown model, unverified (0/0)
    price, and tier-1 overflow.
  - Edge cases: zero tokens, DeepSeek above 128K input (no warn
    because DeepSeek is flat-rate, not tiered).

``structlog.testing.capture_logs`` captures warn events emitted
during the tested call without touching stdlib logging config.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from structlog.testing import capture_logs

from app.modules.model_gateway import cost_calculator as cc


class TestHappyPathDeepSeek:
    """DeepSeek is priced directly in USD, no conversion. Simplest
    case — good anchor for the arithmetic contract."""

    def test_simple_call(self) -> None:
        # 1000 in * $0.28/M + 500 out * $0.42/M = 0.00028 + 0.00021 = 0.00049
        cost = cc.estimate_cost("deepseek/deepseek-chat", 1000, 500)
        assert cost == Decimal("0.00049")

    def test_zero_tokens_returns_zero(self) -> None:
        cost = cc.estimate_cost("deepseek/deepseek-chat", 0, 0)
        assert cost == Decimal("0")

    def test_only_prompt_tokens(self) -> None:
        cost = cc.estimate_cost("deepseek/deepseek-chat", 1_000_000, 0)
        assert cost == Decimal("0.28")

    def test_only_completion_tokens(self) -> None:
        cost = cc.estimate_cost("deepseek/deepseek-chat", 0, 1_000_000)
        assert cost == Decimal("0.42")


class TestHappyPathQwen:
    """Qwen prices are derived from CNY; assert the formula matches
    the import-time derivation."""

    def test_qwen_turbo_matches_expected_formula(self) -> None:
        cost = cc.estimate_cost("dashscope/qwen-turbo", 1_000_000, 1_000_000)
        # 1M in * 0.3 CNY + 1M out * 0.6 CNY = 0.9 CNY total
        # -> 0.9 * USD_PER_CNY
        expected = Decimal("0.9") * cc.USD_PER_CNY
        assert cost == expected

    def test_qwen_max_matches_expected_formula(self) -> None:
        cost = cc.estimate_cost("dashscope/qwen-max", 2000, 500)
        # 2000 * 2.4 + 500 * 9.6 = 4800 + 4800 = 9600 CNY-token-units
        # / 1M = 0.0096 CNY -> converted
        expected = Decimal("9600") * cc.USD_PER_CNY / Decimal("1000000")
        assert cost == expected

    def test_qwen_flash_uses_tier1_prices(self) -> None:
        """qwen-flash MODEL_PRICES entry should reflect the 0-128K
        tier (0.15 / 1.5 CNY per 1M)."""
        cost = cc.estimate_cost("dashscope/qwen-flash", 1_000_000, 0)
        expected = Decimal("0.15") * cc.USD_PER_CNY
        assert cost == expected

    def test_qwen_vl_max_matches_expected_formula(self) -> None:
        cost = cc.estimate_cost("dashscope/qwen-vl-max", 1_000_000, 1_000_000)
        # 1.6 + 4.0 = 5.6 CNY
        expected = Decimal("5.6") * cc.USD_PER_CNY
        assert cost == expected


class TestDecimalPrecision:
    def test_returns_decimal_not_float(self) -> None:
        cost = cc.estimate_cost("deepseek/deepseek-chat", 100, 50)
        assert isinstance(cost, Decimal)

    def test_tiny_cost_preserves_precision(self) -> None:
        """Single-token calls produce sub-cent costs — must survive
        aggregation across ~10k calls/day without float rounding."""
        cost = cc.estimate_cost("deepseek/deepseek-chat", 1, 1)
        # 0.28/M + 0.42/M = 0.7/M = 7e-7
        assert cost == Decimal("0.28") / Decimal("1000000") + Decimal("0.42") / Decimal("1000000")


class TestUnknownModelBranch:
    def test_unknown_model_returns_zero(self) -> None:
        cost = cc.estimate_cost("vendor/ghost", 100, 50)
        assert cost == Decimal("0")

    def test_unknown_model_emits_warn(self) -> None:
        with capture_logs() as logs:
            cc.estimate_cost("vendor/ghost", 100, 50)
        assert len(logs) == 1
        event = logs[0]
        assert event["event"] == "unknown_model_price"
        assert event["log_level"] == "warning"
        assert event["model"] == "vendor/ghost"
        assert event["prompt_tokens"] == 100
        assert event["completion_tokens"] == 50


class TestUnverifiedPriceBranch:
    """No model currently has (0, 0) price after the Zhipu cleanup,
    so inject a placeholder to cover the branch."""

    def test_placeholder_price_returns_zero_and_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fake_prices = {
            **cc.MODEL_PRICES,
            "vendor/placeholder": (Decimal("0"), Decimal("0")),
        }
        monkeypatch.setattr(cc, "MODEL_PRICES", fake_prices)
        with capture_logs() as logs:
            cost = cc.estimate_cost("vendor/placeholder", 100, 50)
        assert cost == Decimal("0")
        assert len(logs) == 1
        event = logs[0]
        assert event["event"] == "unverified_model_price"
        assert event["log_level"] == "warning"
        assert event["model"] == "vendor/placeholder"

    def test_unverified_event_distinct_from_unknown(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two placeholder branches must emit different event names
        so alerts can route them differently."""
        fake_prices = {
            **cc.MODEL_PRICES,
            "vendor/placeholder": (Decimal("0"), Decimal("0")),
        }
        monkeypatch.setattr(cc, "MODEL_PRICES", fake_prices)

        with capture_logs() as placeholder_logs:
            cc.estimate_cost("vendor/placeholder", 0, 0)
        with capture_logs() as unknown_logs:
            cc.estimate_cost("vendor/absent", 0, 0)

        assert placeholder_logs[0]["event"] == "unverified_model_price"
        assert unknown_logs[0]["event"] == "unknown_model_price"
        assert placeholder_logs[0]["event"] != unknown_logs[0]["event"]


class TestTier1OverflowBranch:
    def test_qwen_above_tier1_emits_warn(self) -> None:
        with capture_logs() as logs:
            cost = cc.estimate_cost("dashscope/qwen-turbo", 130_000, 500)
        # Cost still returned (not zero).
        assert cost > Decimal("0")
        # Exactly one warn fired.
        assert len(logs) == 1
        event = logs[0]
        assert event["event"] == "cost_estimate_above_tier1"
        assert event["log_level"] == "warning"
        assert event["model"] == "dashscope/qwen-turbo"
        assert event["prompt_tokens"] == 130_000
        assert event["tier1_limit"] == 128_000

    def test_qwen_at_tier1_limit_no_warn(self) -> None:
        """Boundary: exactly 128000 tokens is still tier 1."""
        with capture_logs() as logs:
            cc.estimate_cost("dashscope/qwen-turbo", 128_000, 0)
        assert logs == []

    def test_qwen_below_tier1_no_warn(self) -> None:
        with capture_logs() as logs:
            cc.estimate_cost("dashscope/qwen-turbo", 100, 50)
        assert logs == []

    def test_deepseek_above_128k_no_warn(self) -> None:
        """DeepSeek is flat-rate, not tiered; above-128K should NOT
        warn (would be a spurious alert)."""
        with capture_logs() as logs:
            cc.estimate_cost("deepseek/deepseek-chat", 200_000, 500)
        assert logs == []


class TestModelPricesStructure:
    """Sanity checks on the derived table itself, so a future edit
    that accidentally drops Qwen or mistypes a key fails here."""

    def test_every_qwen_price_is_derived_from_cny(self) -> None:
        for model, (cny_in, cny_out) in cc._QWEN_PRICES_CNY.items():
            usd_in, usd_out = cc.MODEL_PRICES[model]
            assert usd_in == cny_in * cc.USD_PER_CNY
            assert usd_out == cny_out * cc.USD_PER_CNY

    def test_deepseek_in_table(self) -> None:
        assert "deepseek/deepseek-chat" in cc.MODEL_PRICES

    def test_no_zhipu_models(self) -> None:
        """Post-2026-04 Zhipu cleanup: nothing under zhipu/ namespace."""
        for model in cc.MODEL_PRICES:
            assert not model.startswith("zhipu/")

    def test_every_price_is_decimal(self) -> None:
        for model, (p_in, p_out) in cc.MODEL_PRICES.items():
            assert isinstance(p_in, Decimal), f"{model} input price not Decimal"
            assert isinstance(p_out, Decimal), f"{model} output price not Decimal"


def test_model_capabilities_and_prices_keys_align() -> None:
    """Every model registered in ``MODEL_CAPABILITIES`` must also have a
    pricing entry. Without this invariant, adding a new model to the
    capability table (and routing traffic to it) would emit
    ``unknown_model_price`` warnings on every single call — a log
    spam bomb indistinguishable from a routing bug.

    The reverse direction is intentionally not enforced: a model can
    be priced before its capabilities have been verified (pricing
    check is WebFetch-driven, capability verification may lag).
    """
    from app.modules.model_gateway.capabilities import MODEL_CAPABILITIES

    missing = set(MODEL_CAPABILITIES.keys()) - set(cc.MODEL_PRICES.keys())
    assert not missing, (
        f"models in MODEL_CAPABILITIES but missing from MODEL_PRICES: "
        f"{sorted(missing)}. Either add pricing or remove from capabilities."
    )
