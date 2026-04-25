# -*- coding: utf-8 -*-
"""LLMCore — shared LLM plumbing for the five-brain trading system.

Every brain that wants to "think" via OpenAI uses this module so that
retries, cost tracking, structured-output parsing, and async parallel
calls happen in exactly one place. Per-brain wrappers (e.g.
ChartMind/chartmind_llm.py) build the prompts; LLMCore handles the I/O.

Public API
----------
    from LLMCore import (
        LLMConfig, LLMClient, LLMResponse,
        BrainPrompt, build_prompt,
        CostTracker,
        run_brains_parallel,
    )

Design rules
------------
1. **Read keys from env, never from code.** OPENAI_API_KEY is required;
   the client errors loudly if missing rather than silently no-oping.

2. **Structured output by default.** Brains return JSON conforming to a
   per-brain schema. We never trust free-form text for downstream
   parsing.

3. **Defensive defaults.** All public methods catch and log exceptions
   then return a `LLMResponse` with `ok=False` so the calling brain can
   gracefully fall back to its mechanical analysis.

4. **Cost is observable.** Every call increments token counters in a
   process-wide CostTracker so the long-running `main.py` loop can
   surface cost-per-cycle in its status line.
"""
from __future__ import annotations

from .client import (
    LLMClient,
    LLMConfig,
    LLMResponse,
    LLMError,
)
from .prompts import (
    BrainPrompt,
    build_prompt,
)
from .cost import (
    CostTracker,
    GLOBAL_COST_TRACKER,
)
from .parallel import run_brains_parallel

__all__ = [
    "LLMClient", "LLMConfig", "LLMResponse", "LLMError",
    "BrainPrompt", "build_prompt",
    "CostTracker", "GLOBAL_COST_TRACKER",
    "run_brains_parallel",
]

__version__ = "1.0.0"
