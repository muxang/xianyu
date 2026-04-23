"""Tests for ``app.modules.model_gateway.router``.

Organization:

- **Group A** loads the real bundled YAML (happy path; catches drift
  between ``purpose_routing.yaml`` and the code).
- **Group B** writes deliberately-broken YAMLs to ``tmp_path`` and
  asserts each fail-fast path raises ``RoutingConfigError`` with a
  useful message.
- **Group C** covers the strict-getter contract (``get_routing_table``
  refuses to return anything until ``load_routing_table`` succeeds)
  and ``select`` behavior including the defense-in-depth ``KeyError``.
- **Group D** verifies ``_reset_for_testing`` actually clears cache.

Module-level ``_routing_table`` is shared state, so an autouse
fixture resets it before and after every test.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

import app.modules.model_gateway.router as router_module
from app.modules.model_gateway.errors import RoutingConfigError
from app.modules.model_gateway.router import (
    _reset_for_testing,
    get_routing_table,
    load_routing_table,
    select,
)
from app.modules.model_gateway.schema import RoutingEntry
from app.shared.enums import LLMPurpose


@pytest.fixture(autouse=True)
def _reset_router_state() -> Generator[None, None, None]:
    """Clear the module cache before AND after every test.

    Pre-reset guards against leakage from earlier tests that forgot
    to clean up; post-reset keeps the next test from inheriting our
    state.
    """
    _reset_for_testing()
    yield
    _reset_for_testing()


# ---------------------------------------------------------------------
# Group A: happy path against the real bundled YAML
# ---------------------------------------------------------------------


class TestLoadHappyPath:
    def test_loads_all_seven_purposes(self) -> None:
        table = load_routing_table()
        assert set(table.keys()) == set(LLMPurpose)
        assert len(table) == 7

    def test_each_entry_is_routing_entry(self) -> None:
        table = load_routing_table()
        for entry in table.values():
            assert isinstance(entry, RoutingEntry)

    def test_chitchat_entry_matches_yaml(self) -> None:
        """Spot-check CHITCHAT: qwen-turbo primary (not qwen-flash --
        short-in/short-out chitchat actually costs more on flash
        because flash's output tier is 2.5x turbo's). See YAML comment."""
        table = load_routing_table()
        e = table[LLMPurpose.CHITCHAT]
        assert e.primary == "dashscope/qwen-turbo"
        assert e.fallback == "dashscope/qwen-plus"
        assert e.timeout_ms == 5000
        assert e.max_retries == 1
        assert e.temperature == 0.7

    def test_image_understanding_entry_matches_yaml(self) -> None:
        """Spot-check IMAGE_UNDERSTANDING: vision-capable models,
        longer timeout, lower temperature for factual description."""
        table = load_routing_table()
        e = table[LLMPurpose.IMAGE_UNDERSTANDING]
        assert e.primary == "dashscope/qwen-vl-max"
        assert e.fallback == "dashscope/qwen-vl-plus"
        assert e.timeout_ms == 15000
        assert e.max_retries == 1
        assert e.temperature == 0.3

    def test_main_generation_entry_matches_yaml(self) -> None:
        """Spot-check MAIN_GENERATION: premium primary, cross-vendor
        fallback for resilience."""
        table = load_routing_table()
        e = table[LLMPurpose.MAIN_GENERATION]
        assert e.primary == "dashscope/qwen-max"
        assert e.fallback == "deepseek/deepseek-chat"
        assert e.timeout_ms == 10000
        assert e.max_retries == 2
        assert e.temperature == 0.7

    def test_get_returns_same_object_as_load(self) -> None:
        loaded = load_routing_table()
        got = get_routing_table()
        assert loaded is got

    def test_select_returns_entry_for_each_purpose(self) -> None:
        load_routing_table()
        for purpose in LLMPurpose:
            entry = select(purpose)
            assert isinstance(entry, RoutingEntry)

    def test_reload_is_idempotent(self) -> None:
        """Calling load twice with the same valid config succeeds and
        produces equal (but fresh) tables."""
        first = load_routing_table()
        second = load_routing_table()
        assert first.keys() == second.keys()
        for purpose in first:
            assert first[purpose].model_dump() == second[purpose].model_dump()


# ---------------------------------------------------------------------
# Group B: fail-fast paths with deliberately-broken YAML
# ---------------------------------------------------------------------


def _full_valid_routes_block(indent: str = "  ") -> str:
    """Helper: produce a YAML routes block that covers every
    LLMPurpose with a minimal-but-valid entry (qwen-turbo / qwen-plus
    both satisfy all non-vision requirements)."""
    lines = []
    for purpose in LLMPurpose:
        if purpose is LLMPurpose.IMAGE_UNDERSTANDING:
            primary, fallback = "dashscope/qwen-vl-max", "dashscope/qwen-vl-plus"
        else:
            primary, fallback = "dashscope/qwen-turbo", "dashscope/qwen-plus"
        lines.extend(
            [
                f"{indent}{purpose.value}:",
                f"{indent}  primary: {primary}",
                f"{indent}  fallback: {fallback}",
                f"{indent}  timeout_ms: 3000",
                f"{indent}  max_retries: 1",
                f"{indent}  temperature: 0.5",
            ],
        )
    return "\n".join(lines)


class TestLoadFailFast:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        missing = tmp_path / "does_not_exist.yaml"
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(missing)
        assert "cannot read routing config" in str(exc.value)
        assert str(missing) in str(exc.value)

    def test_yaml_syntax_error_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        # Unterminated flow sequence -> YAMLError.
        bad.write_text("version: 1\nroutes: [unclosed\n", encoding="utf-8")
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        msg = str(exc.value)
        assert "failed to parse YAML" in msg
        assert str(bad) in msg

    def test_top_level_not_mapping_raises(self, tmp_path: Path) -> None:
        """A bare list at top level is syntactically valid YAML but
        not the shape we want."""
        bad = tmp_path / "list_top.yaml"
        bad.write_text("- version: 1\n- routes: {}\n", encoding="utf-8")
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        assert "top-level YAML must be a mapping" in str(exc.value)

    def test_missing_version_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "no_version.yaml"
        bad.write_text("routes: {}\n", encoding="utf-8")
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        assert "missing required 'version' field" in str(exc.value)

    def test_unsupported_version_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "v2.yaml"
        bad.write_text("version: 2\nroutes: {}\n", encoding="utf-8")
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        msg = str(exc.value)
        assert "expected 1" in msg
        assert "got 2" in msg

    def test_missing_routes_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "no_routes.yaml"
        bad.write_text("version: 1\n", encoding="utf-8")
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        assert "missing required 'routes' field" in str(exc.value)

    def test_routes_not_mapping_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "routes_list.yaml"
        bad.write_text("version: 1\nroutes: [a, b, c]\n", encoding="utf-8")
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        assert "'routes' must be a mapping" in str(exc.value)

    def test_unknown_purpose_key_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "unknown_purpose.yaml"
        bad.write_text(
            "version: 1\n"
            "routes:\n"
            "  UNKNOWN_PURPOSE:\n"
            "    primary: dashscope/qwen-turbo\n"
            "    fallback: dashscope/qwen-plus\n"
            "    timeout_ms: 3000\n"
            "    max_retries: 1\n"
            "    temperature: 0.1\n",
            encoding="utf-8",
        )
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        msg = str(exc.value)
        assert "unknown purpose" in msg
        assert "UNKNOWN_PURPOSE" in msg

    def test_incomplete_coverage_raises(self, tmp_path: Path) -> None:
        """YAML parses cleanly but covers fewer than all
        LLMPurpose values."""
        bad = tmp_path / "partial.yaml"
        bad.write_text(
            "version: 1\n"
            "routes:\n"
            "  CHITCHAT:\n"
            "    primary: dashscope/qwen-turbo\n"
            "    fallback: dashscope/qwen-plus\n"
            "    timeout_ms: 3000\n"
            "    max_retries: 1\n"
            "    temperature: 0.1\n",
            encoding="utf-8",
        )
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        msg = str(exc.value)
        assert "missing purposes" in msg
        # All six non-CHITCHAT purposes should be named.
        for purpose in LLMPurpose:
            if purpose is not LLMPurpose.CHITCHAT:
                assert purpose.value in msg

    def test_entry_missing_temperature_raises(self, tmp_path: Path) -> None:
        """A single entry with the required ``temperature`` field
        omitted surfaces as a wrapped ValidationError, naming the
        offending purpose."""
        bad = tmp_path / "no_temperature.yaml"
        bad.write_text(
            "version: 1\n"
            "routes:\n"
            "  CHITCHAT:\n"
            "    primary: dashscope/qwen-turbo\n"
            "    fallback: dashscope/qwen-plus\n"
            "    timeout_ms: 3000\n"
            "    max_retries: 1\n",
            encoding="utf-8",
        )
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        msg = str(exc.value)
        assert "CHITCHAT" in msg
        assert "validation" in msg.lower()
        # The pydantic detail should mention the actual missing field.
        assert "temperature" in msg.lower()

    def test_entry_timeout_out_of_bounds_raises(self, tmp_path: Path) -> None:
        """A YAML that sets ``timeout_ms: 3`` (meant 3000) is exactly
        the kind of typo RoutingEntry's lower bound is there to catch."""
        bad = tmp_path / "bad_timeout.yaml"
        bad.write_text(
            "version: 1\n"
            "routes:\n"
            "  CHITCHAT:\n"
            "    primary: dashscope/qwen-turbo\n"
            "    fallback: dashscope/qwen-plus\n"
            "    timeout_ms: 3\n"
            "    max_retries: 1\n"
            "    temperature: 0.1\n",
            encoding="utf-8",
        )
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        msg = str(exc.value)
        assert "CHITCHAT" in msg
        assert "validation" in msg.lower()

    def test_capability_mismatch_raises(self, tmp_path: Path) -> None:
        """Structurally valid YAML that routes IMAGE_UNDERSTANDING to
        a non-vision model -> Step 2's validator catches it."""
        # Build complete YAML but route IMAGE_UNDERSTANDING to qwen-turbo
        # (text-only) instead of a vision model.
        lines = ["version: 1", "routes:"]
        for purpose in LLMPurpose:
            lines.extend(
                [
                    f"  {purpose.value}:",
                    "    primary: dashscope/qwen-turbo",
                    "    fallback: dashscope/qwen-plus",
                    "    timeout_ms: 3000",
                    "    max_retries: 1",
                    "    temperature: 0.5",
                ],
            )
        bad = tmp_path / "bad_capability.yaml"
        bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(RoutingConfigError) as exc:
            load_routing_table(bad)
        msg = str(exc.value)
        assert "IMAGE_UNDERSTANDING" in msg
        assert "vision" in msg


# ---------------------------------------------------------------------
# Group C: get_routing_table + select behavior
# ---------------------------------------------------------------------


class TestGetAndSelect:
    def test_uninitialized_get_raises(self) -> None:
        with pytest.raises(RoutingConfigError) as exc:
            get_routing_table()
        assert "not initialized" in str(exc.value)

    def test_uninitialized_select_raises(self) -> None:
        """select() goes through get_routing_table(), so the same
        RoutingConfigError propagates."""
        with pytest.raises(RoutingConfigError) as exc:
            select(LLMPurpose.CHITCHAT)
        assert "not initialized" in str(exc.value)

    def test_select_after_load_returns_correct_entry(self) -> None:
        load_routing_table()
        entry = select(LLMPurpose.CHITCHAT)
        assert entry.primary == "dashscope/qwen-turbo"

    def test_select_missing_purpose_raises_keyerror(self) -> None:
        """Defense-in-depth: hand-construct an incomplete table
        bypassing load() and verify select() raises KeyError, not
        silently returns None or KeyError-from-dict."""
        partial_entry = RoutingEntry(
            primary="dashscope/qwen-turbo",
            fallback="dashscope/qwen-plus",
            timeout_ms=3000,
            max_retries=1,
            temperature=0.5,
        )
        router_module._routing_table = {LLMPurpose.CHITCHAT: partial_entry}
        with pytest.raises(KeyError) as exc:
            select(LLMPurpose.MAIN_GENERATION)
        assert "no routing entry" in str(exc.value)
        assert "MAIN_GENERATION" in str(exc.value)


# ---------------------------------------------------------------------
# Group D: _reset_for_testing contract
# ---------------------------------------------------------------------


class TestResetForTesting:
    def test_reset_clears_cache(self) -> None:
        load_routing_table()
        # Confirm loaded so the reset has something to clear.
        get_routing_table()
        _reset_for_testing()
        with pytest.raises(RoutingConfigError) as exc:
            get_routing_table()
        assert "not initialized" in str(exc.value)

    def test_reset_when_already_empty_is_noop(self) -> None:
        """Calling reset twice (or before any load) must not raise."""
        _reset_for_testing()
        _reset_for_testing()
        with pytest.raises(RoutingConfigError):
            get_routing_table()
