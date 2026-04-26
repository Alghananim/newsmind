"""LLM reasoning layer for Engine V3 brains.

Default-deny philosophy: if OpenAI fails / times out / no API key,
the LLM review is treated as 'unavailable' and the system falls back
to mechanical-only decisions. The LLM can NEVER upgrade a decision
(only downgrade it for safety).
"""
from .openai_brain import (
    OpenAIClient, LLMReview, review_brain_outputs,
    LLM_AVAILABLE, LLM_DISABLED_REASON,
)

__all__ = [
    "OpenAIClient", "LLMReview", "review_brain_outputs",
    "LLM_AVAILABLE", "LLM_DISABLED_REASON",
]
