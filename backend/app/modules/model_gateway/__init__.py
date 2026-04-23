"""Module 11: Model Gateway.

Unified LLM entry point for the whole project. Business code must
not import vendor SDKs (openai, dashscope, ...) — it goes through
``call_llm`` here, which handles routing, fallback, retries,
timeouts, cost estimation, and Langfuse observability.

Public API surface (``call_llm``, ``initialize``, ``Message``,
``LLMResponse``, ``LLMPurpose``, error classes) is added when the
runtime entry point lands; until then this package exists as a
namespace for the internal submodules (schema, errors, capabilities).
"""

__all__: list[str] = []
