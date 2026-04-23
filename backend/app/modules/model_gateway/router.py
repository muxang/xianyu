"""YAML-backed purpose routing.

``load_routing_table`` runs **at startup** (eagerly, not lazy) and
performs every check that could let a misconfigured app serve broken
requests later:

  1. YAML is syntactically valid.
  2. Schema version matches the code (``_SUPPORTED_SCHEMA_VERSION``).
  3. Top-level ``routes`` key is present and a mapping.
  4. Every key under ``routes`` is a known ``LLMPurpose`` value.
  5. Every ``LLMPurpose`` has a row (completeness).
  6. Every row parses into a ``RoutingEntry`` (bounds, types,
     required fields, extra="forbid").
  7. Every primary / fallback satisfies its purpose's capability
     requirements (delegated to
     ``capabilities.validate_routing_capabilities``).

Any failure raises ``RoutingConfigError`` with the source file path
and the specific problem so a YAML drift can be fixed without
reading source. The app's ``lifespan`` startup hook (Step 6) calls
this before accepting traffic, so misconfiguration fails the deploy
rather than the first production request.

``get_routing_table`` is a strict getter: it refuses to return
anything until ``load_routing_table`` has succeeded, so forgetting
to wire the startup call surfaces as a clear error instead of a
silent first-request load.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import yaml
from pydantic import ValidationError

from app.shared.enums import LLMPurpose

from .capabilities import validate_routing_capabilities
from .errors import RoutingConfigError
from .schema import RoutingEntry

_SUPPORTED_SCHEMA_VERSION: Final[int] = 1

DEFAULT_CONFIG_PATH: Final[Path] = (
    Path(__file__).parent / "config" / "purpose_routing.yaml"
)

# Module-level cache, filled by load_routing_table() at startup.
# Deliberately NOT lazy: get_routing_table() raises rather than
# silently triggering a load, so forgetting to call initialize() at
# startup produces a loud error instead of a concealed first-request
# filesystem read.
_routing_table: dict[LLMPurpose, RoutingEntry] | None = None


def load_routing_table(
    path: Path | None = None,
) -> dict[LLMPurpose, RoutingEntry]:
    """Load, validate, and cache the routing table.

    Not lazy. Intended to be called exactly once per process during
    app startup (from ``model_gateway.initialize()`` in Step 6).
    Idempotent-on-success for testability: calling twice with the
    same valid config reloads and returns the same shape.

    ``path`` defaults to ``DEFAULT_CONFIG_PATH`` (the bundled YAML);
    tests override it with ``tmp_path`` fixtures.

    Raises ``RoutingConfigError`` with file path + detail on any of
    the seven checks described in the module docstring. The wrapped
    ``ValidationError`` / ``YAMLError`` is chained via ``from`` so
    tracebacks keep the root cause.
    """
    global _routing_table

    config_path = path if path is not None else DEFAULT_CONFIG_PATH

    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as e:
        raise RoutingConfigError(
            f"cannot read routing config at {config_path}: {e}",
        ) from e

    try:
        raw: object = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        raise RoutingConfigError(
            f"failed to parse YAML at {config_path}: {e}",
        ) from e

    if not isinstance(raw, dict):
        raise RoutingConfigError(
            f"{config_path}: top-level YAML must be a mapping, "
            f"got {type(raw).__name__}",
        )

    if "version" not in raw:
        raise RoutingConfigError(
            f"{config_path}: missing required 'version' field",
        )
    version = raw["version"]
    if version != _SUPPORTED_SCHEMA_VERSION:
        raise RoutingConfigError(
            f"{config_path}: unsupported schema version: "
            f"expected {_SUPPORTED_SCHEMA_VERSION}, got {version!r}",
        )

    if "routes" not in raw:
        raise RoutingConfigError(
            f"{config_path}: missing required 'routes' field",
        )
    routes_raw = raw["routes"]
    if not isinstance(routes_raw, dict):
        raise RoutingConfigError(
            f"{config_path}: 'routes' must be a mapping, "
            f"got {type(routes_raw).__name__}",
        )

    table: dict[LLMPurpose, RoutingEntry] = {}
    for purpose_key, entry_raw in routes_raw.items():
        try:
            purpose = LLMPurpose(purpose_key)
        except ValueError as e:
            raise RoutingConfigError(
                f"{config_path}: unknown purpose {purpose_key!r} "
                f"(not a member of LLMPurpose)",
            ) from e
        try:
            entry = RoutingEntry.model_validate(entry_raw)
        except ValidationError as e:
            raise RoutingConfigError(
                f"{config_path}: entry for {purpose.value} "
                f"failed validation: {e}",
            ) from e
        table[purpose] = entry

    # Completeness: every LLMPurpose must have a row. Missing rows
    # would otherwise only surface when that purpose is first called.
    missing = set(LLMPurpose) - set(table.keys())
    if missing:
        missing_str = ", ".join(sorted(p.value for p in missing))
        raise RoutingConfigError(
            f"{config_path}: routing table missing purposes: {missing_str}",
        )

    # Capability cross-check (Step 2 validator). Raises a detailed
    # RoutingConfigError on any primary/fallback that lacks a
    # purpose-required capability.
    validate_routing_capabilities(table)

    _routing_table = table
    return table


def get_routing_table() -> dict[LLMPurpose, RoutingEntry]:
    """Return the cached routing table.

    Raises ``RoutingConfigError`` if ``load_routing_table`` has not
    been called in this process. The router is deliberately not lazy
    so a missing startup wire-up surfaces here as a clear error
    rather than a silent first-request read.
    """
    if _routing_table is None:
        raise RoutingConfigError(
            "routing table not initialized; "
            "call load_routing_table() at app startup",
        )
    return _routing_table


def select(purpose: LLMPurpose) -> RoutingEntry:
    """Return the routing entry for ``purpose``.

    Completeness is enforced at load time, so a ``KeyError`` here
    effectively only fires in tests that hand-construct an
    incomplete ``_routing_table``; the check is kept as
    defense-in-depth rather than trusting load-time coverage alone.
    """
    table = get_routing_table()
    if purpose not in table:
        raise KeyError(f"no routing entry for purpose {purpose!r}")
    return table[purpose]


def _reset_for_testing() -> None:
    """Clear the module-level cache. Tests only — never call in production."""
    global _routing_table
    _routing_table = None
