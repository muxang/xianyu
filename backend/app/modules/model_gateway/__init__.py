"""Module 11: Model Gateway.

Unified LLM entry point for the whole project. Business code must
not import vendor SDKs (openai, dashscope, ...) — it goes through
``call_llm`` here, which handles routing, fallback, retries,
timeouts, cost estimation, and Langfuse observability.

Startup:
    In FastAPI ``lifespan`` (or equivalent), call ``initialize()``
    once before accepting traffic. ``call_llm`` refuses to run
    until ``initialize()`` has succeeded.

Typical use::

    from app.modules.model_gateway import (
        call_llm, LLMPurpose, Message,
    )

    response = await call_llm(
        purpose=LLMPurpose.CHITCHAT,
        messages=[Message(role="user", content="hi")],
        seller_id=str(seller.id),
        trace_id=str(request_trace_id),
    )
"""

from app.modules.model_gateway.errors import (
    LLMAllModelsFailedError,
    LLMAuthenticationError,
    LLMError,
    LLMFeatureNotImplementedError,
    LLMNotInitializedError,
    LLMTimeoutError,
    RoutingConfigError,
)
from app.modules.model_gateway.gateway import call_llm, initialize
from app.modules.model_gateway.schema import LLMResponse, Message

# Re-exported from app.shared.enums because gateway consumers commonly
# need LLMPurpose at the same import site as ``call_llm``; the original
# definition still lives in ``app.shared.enums``.
from app.shared.enums import LLMPurpose

__all__ = [
    "LLMAllModelsFailedError",
    "LLMAuthenticationError",
    "LLMError",
    "LLMFeatureNotImplementedError",
    "LLMNotInitializedError",
    "LLMPurpose",
    "LLMResponse",
    "LLMTimeoutError",
    "Message",
    "RoutingConfigError",
    "call_llm",
    "initialize",
]
