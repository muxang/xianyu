"""Tests for ``app.modules.model_gateway.capabilities``.

Most tests inject fake capability / requirement tables so behaviour
can be exercised hermetically without depending on whatever the
production tables currently claim. A small smoke section checks the
production tables themselves for obvious drift (e.g. a purpose added
to ``LLMPurpose`` without a matching requirement row).

YAML-from-disk loading is exercised by ``test_router.py`` in Step 4;
here we construct ``RoutingEntry`` objects directly.
"""

from __future__ import annotations

import pytest

from app.modules.model_gateway.capabilities import (
    MODEL_CAPABILITIES,
    PURPOSE_REQUIREMENTS,
    Capability,
    validate_routing_capabilities,
)
from app.modules.model_gateway.errors import RoutingConfigError
from app.modules.model_gateway.schema import RoutingEntry
from app.shared.enums import LLMPurpose


def _entry(primary: str, fallback: str) -> RoutingEntry:
    return RoutingEntry(
        primary=primary,
        fallback=fallback,
        timeout_ms=3000,
        max_retries=1,
        temperature=0.5,
    )


# ---------------------------------------------------------------------
# Hermetic: inject fake capability / requirement tables
# ---------------------------------------------------------------------


class TestValidateWithInjectedTables:
    def test_ok_when_every_model_satisfies_requirements(self) -> None:
        caps = {
            "vendor/text-only": {Capability.TEXT},
            "vendor/text-json": {Capability.TEXT, Capability.JSON_MODE},
        }
        reqs = {
            LLMPurpose.CHITCHAT: {Capability.TEXT},
            LLMPurpose.INTENT_CLASSIFICATION: {Capability.TEXT, Capability.JSON_MODE},
        }
        table = {
            LLMPurpose.CHITCHAT: _entry("vendor/text-only", "vendor/text-only"),
            LLMPurpose.INTENT_CLASSIFICATION: _entry(
                "vendor/text-json", "vendor/text-json"
            ),
        }
        validate_routing_capabilities(
            table, model_capabilities=caps, purpose_requirements=reqs
        )

    def test_empty_table_passes(self) -> None:
        """An empty table is a no-op; production YAML is never empty, but the
        validator shouldn't encode that assumption."""
        validate_routing_capabilities({})

    def test_primary_missing_capability_raises(self) -> None:
        caps = {
            "vendor/text-only": {Capability.TEXT},
            "vendor/vision": {Capability.TEXT, Capability.VISION},
        }
        reqs = {LLMPurpose.IMAGE_UNDERSTANDING: {Capability.TEXT, Capability.VISION}}
        table = {
            # primary is text-only -> lacks vision
            LLMPurpose.IMAGE_UNDERSTANDING: _entry("vendor/text-only", "vendor/vision"),
        }
        with pytest.raises(RoutingConfigError) as exc_info:
            validate_routing_capabilities(
                table, model_capabilities=caps, purpose_requirements=reqs
            )
        msg = str(exc_info.value)
        assert "IMAGE_UNDERSTANDING" in msg
        assert "primary" in msg
        assert "vendor/text-only" in msg
        assert "vision" in msg

    def test_fallback_missing_capability_raises(self) -> None:
        caps = {
            "vendor/vision": {Capability.TEXT, Capability.VISION},
            "vendor/text-only": {Capability.TEXT},
        }
        reqs = {LLMPurpose.IMAGE_UNDERSTANDING: {Capability.TEXT, Capability.VISION}}
        table = {
            # fallback is text-only -> lacks vision
            LLMPurpose.IMAGE_UNDERSTANDING: _entry("vendor/vision", "vendor/text-only"),
        }
        with pytest.raises(RoutingConfigError) as exc_info:
            validate_routing_capabilities(
                table, model_capabilities=caps, purpose_requirements=reqs
            )
        msg = str(exc_info.value)
        assert "IMAGE_UNDERSTANDING" in msg
        assert "fallback" in msg
        assert "vendor/text-only" in msg
        assert "vision" in msg

    def test_multiple_missing_capabilities_all_reported_and_sorted(self) -> None:
        """Error message lists every missing capability, sorted alphabetically
        for diff-stable output."""
        caps = {"vendor/basic": {Capability.TEXT}}
        reqs = {
            LLMPurpose.IMAGE_UNDERSTANDING: {
                Capability.TEXT,
                Capability.VISION,
                Capability.JSON_MODE,
            }
        }
        table = {
            LLMPurpose.IMAGE_UNDERSTANDING: _entry("vendor/basic", "vendor/basic"),
        }
        with pytest.raises(RoutingConfigError) as exc_info:
            validate_routing_capabilities(
                table, model_capabilities=caps, purpose_requirements=reqs
            )
        msg = str(exc_info.value)
        assert "json_mode" in msg
        assert "vision" in msg
        # Sorted alphabetically: json_mode before vision.
        assert msg.index("json_mode") < msg.index("vision")

    def test_unknown_model_raises(self) -> None:
        caps = {"vendor/known": {Capability.TEXT}}
        reqs = {LLMPurpose.CHITCHAT: {Capability.TEXT}}
        table = {LLMPurpose.CHITCHAT: _entry("vendor/ghost", "vendor/known")}
        with pytest.raises(RoutingConfigError) as exc_info:
            validate_routing_capabilities(
                table, model_capabilities=caps, purpose_requirements=reqs
            )
        msg = str(exc_info.value)
        assert "CHITCHAT" in msg
        assert "primary" in msg
        assert "unknown model" in msg
        assert "vendor/ghost" in msg

    def test_unknown_purpose_raises(self) -> None:
        """A purpose present in the routing table but absent from
        PURPOSE_REQUIREMENTS is a typo we must not silently accept."""
        caps = {"vendor/text": {Capability.TEXT}}
        reqs = {LLMPurpose.CHITCHAT: {Capability.TEXT}}
        table = {LLMPurpose.MAIN_GENERATION: _entry("vendor/text", "vendor/text")}
        with pytest.raises(RoutingConfigError) as exc_info:
            validate_routing_capabilities(
                table, model_capabilities=caps, purpose_requirements=reqs
            )
        msg = str(exc_info.value)
        assert "unknown purpose" in msg
        assert "MAIN_GENERATION" in msg


# ---------------------------------------------------------------------
# Production table smoke tests
# ---------------------------------------------------------------------


class TestProductionTables:
    def test_every_llm_purpose_has_requirement_row(self) -> None:
        """A new ``LLMPurpose`` enum value without a requirement row would
        make validate silently pass any routing for it."""
        missing = set(LLMPurpose) - PURPOSE_REQUIREMENTS.keys()
        assert not missing, f"Missing PURPOSE_REQUIREMENTS entries: {missing}"

    def test_every_model_capability_value_is_capability_enum(self) -> None:
        """Catches accidental use of raw strings (e.g. ``{"vision"}``) in
        MODEL_CAPABILITIES, which would silently break set subtraction."""
        for model, cap_set in MODEL_CAPABILITIES.items():
            for cap in cap_set:
                assert isinstance(cap, Capability), (
                    f"{model}: {cap!r} is not a Capability member"
                )

    def test_every_requirement_value_is_capability_enum(self) -> None:
        for purpose, req_set in PURPOSE_REQUIREMENTS.items():
            for cap in req_set:
                assert isinstance(cap, Capability), (
                    f"{purpose.value}: {cap!r} is not a Capability member"
                )

    def test_all_known_models_are_qualified_names(self) -> None:
        """LiteLLM convention: ``vendor/model`` (not just ``model``)."""
        for model in MODEL_CAPABILITIES:
            assert "/" in model, f"model name must be vendor-qualified: {model!r}"
