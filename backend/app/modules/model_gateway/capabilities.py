"""Static capability contracts enforced at startup.

Guards against the silent-bomb scenario where ``purpose_routing.yaml``
maps a vision purpose to a text-only model (risk 1 from the Day 2
design brief). Such a typo would let the app start, pass smoke checks,
and only explode in production when the primary fails and fallback
fires into a model that cannot handle the payload.

Design:

1. ``MODEL_CAPABILITIES`` lists what each model can actually do.
   Entries are **conservative**: when uncertain, OMIT the capability
   rather than assume it. A missing capability triggers a loud
   validation error (easy to fix at load time); a wrong capability
   triggers a silent production failure under fallback (expensive).

2. ``PURPOSE_REQUIREMENTS`` lists what each purpose needs.

3. ``validate_routing_capabilities`` is called once at startup by
   ``router.load_routing_table`` and cross-checks the two tables.
   Failures raise ``RoutingConfigError`` with purpose / slot / model /
   missing-capability detail so the YAML can be fixed without
   consulting source.

Sources are annotated inline in the capability map. Update an entry
(and its source note) only after verifying against official docs or a
staging smoke test; do NOT add a capability because "it probably
works".
"""

from __future__ import annotations

from enum import StrEnum

from app.shared.enums import LLMPurpose

from .errors import RoutingConfigError
from .schema import RoutingEntry


class Capability(StrEnum):
    """Capabilities the gateway routes on.

    Deliberately small: only facets the validator needs to gate on.
    Finer concerns (streaming, context window size, pricing) live in
    per-model docstrings or the cost calculator, not here.
    """

    TEXT = "text"
    JSON_MODE = "json_mode"
    VISION = "vision"
    TOOL_USE = "tool_use"


# Per-model advertised capability sets.
#
# Rule: when a capability is undocumented OR unverified, leave it out.
# Missing -> loud fail-fast (good). Over-claimed -> silent fallback
# failure in production (bad).
#
# Aliyun DashScope source:
#   help.aliyun.com/zh/model-studio/developer-reference
# DeepSeek source:
#   api-docs.deepseek.com
MODEL_CAPABILITIES: dict[str, set[Capability]] = {
    # qwen-turbo / qwen-plus / qwen-flash: text tier with JSON Mode
    # documented. tool_use is listed in docs but LiteLLM parity not
    # yet smoke-tested -> omitted until a Day 3+ integration test
    # confirms it.
    "dashscope/qwen-turbo": {
        Capability.TEXT,
        Capability.JSON_MODE,
    },
    "dashscope/qwen-plus": {
        Capability.TEXT,
        Capability.JSON_MODE,
    },
    "dashscope/qwen-flash": {
        Capability.TEXT,
        Capability.JSON_MODE,
    },
    # qwen-max: text + JSON Mode + function-calling (tool_use) all
    # explicitly documented and commonly demoed.
    "dashscope/qwen-max": {
        Capability.TEXT,
        Capability.JSON_MODE,
        Capability.TOOL_USE,
    },
    # qwen-vl-max / qwen-vl-plus: vision + JSON Mode verified.
    # Source: help.aliyun.com/zh/model-studio/structured-output
    # (section "支持的模型 > 多模态")
    # Caveats encoded in the fixed-alias form we use here:
    #   - JSON Mode only applies to NON-thinking mode calls;
    #   - The `-latest` tag and dated snapshot variants (e.g.
    #     `qwen-vl-max-2024-11-19`) are NOT covered -> do NOT reuse
    #     this capability set if a new entry like
    #     ``dashscope/qwen-vl-max-latest`` is ever added.
    "dashscope/qwen-vl-max": {
        Capability.TEXT,
        Capability.VISION,
        Capability.JSON_MODE,
    },
    "dashscope/qwen-vl-plus": {
        Capability.TEXT,
        Capability.VISION,
        Capability.JSON_MODE,
    },
    # deepseek-chat: text + JSON Mode documented. tool_use claimed
    # but LiteLLM parity unverified -> omitted.
    "deepseek/deepseek-chat": {
        Capability.TEXT,
        Capability.JSON_MODE,
    },
}


# What each purpose's chosen model must support. Every ``LLMPurpose``
# must have a row here (enforced by a smoke test in test_capabilities).
PURPOSE_REQUIREMENTS: dict[LLMPurpose, set[Capability]] = {
    LLMPurpose.INTENT_CLASSIFICATION: {Capability.TEXT, Capability.JSON_MODE},
    LLMPurpose.RETRIEVAL_SELECTOR: {Capability.TEXT, Capability.JSON_MODE},
    LLMPurpose.MAIN_GENERATION: {Capability.TEXT},
    LLMPurpose.NEGOTIATION_GENERATION: {Capability.TEXT},
    LLMPurpose.STYLE_REWRITE: {Capability.TEXT, Capability.JSON_MODE},
    LLMPurpose.IMAGE_UNDERSTANDING: {
        Capability.TEXT,
        Capability.VISION,
        Capability.JSON_MODE,
    },
    LLMPurpose.CHITCHAT: {Capability.TEXT},
}


def validate_routing_capabilities(
    routing_table: dict[LLMPurpose, RoutingEntry],
    *,
    model_capabilities: dict[str, set[Capability]] | None = None,
    purpose_requirements: dict[LLMPurpose, set[Capability]] | None = None,
) -> None:
    """Verify every entry's primary AND fallback satisfy purpose requirements.

    Called once per process at startup (from ``router.load_routing_table``).
    Raises ``RoutingConfigError`` on the first violation, with enough
    context (purpose, slot, model, missing capabilities) to fix the
    YAML without reading source.

    The ``model_capabilities`` / ``purpose_requirements`` keyword args
    are test-injection hooks; production always uses the module-level
    tables.

    Unknown model (not in ``MODEL_CAPABILITIES``) and unknown purpose
    (not in ``PURPOSE_REQUIREMENTS``) are both fatal — it is cheaper to
    fail loudly at startup than to route a call that silently succeeds
    or fails in production.
    """
    caps = model_capabilities if model_capabilities is not None else MODEL_CAPABILITIES
    reqs = (
        purpose_requirements if purpose_requirements is not None else PURPOSE_REQUIREMENTS
    )

    for purpose, entry in routing_table.items():
        if purpose not in reqs:
            raise RoutingConfigError(
                f"unknown purpose in routing table: {purpose.value!r} "
                f"(not in PURPOSE_REQUIREMENTS)",
            )
        required = reqs[purpose]
        for slot, model in (("primary", entry.primary), ("fallback", entry.fallback)):
            if model not in caps:
                raise RoutingConfigError(
                    f"{purpose.value}.{slot}: unknown model {model!r} "
                    f"(not in MODEL_CAPABILITIES; add an entry with verified "
                    f"capabilities before routing to it)",
                )
            missing = required - caps[model]
            if missing:
                missing_str = ", ".join(sorted(c.value for c in missing))
                raise RoutingConfigError(
                    f"{purpose.value}.{slot}: model {model!r} "
                    f"missing capabilities: {{{missing_str}}}",
                )
