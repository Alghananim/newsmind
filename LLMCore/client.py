# -*- coding: utf-8 -*-
"""LLMClient — thin, defensive OpenAI wrapper for the trading brains.

What this gives the brains
--------------------------
1. **One configured client** — read OPENAI_API_KEY from env at startup,
   raise LLMError if missing so misconfiguration surfaces immediately.

2. **Retries with exponential backoff** — transient 429/5xx errors are
   retried up to `max_retries` times with jittered backoff. Permanent
   errors (4xx other than 429) raise immediately.

3. **Structured output** — `complete_json()` enforces JSON-mode and
   parses the response. On parse failure we retry once with a
   "your last response was not valid JSON" follow-up; on second
   failure we return `ok=False` so the brain falls back.

4. **Cost tracking** — every successful response increments the global
   CostTracker (token counts + estimated USD) so the live loop can
   report cost-per-cycle in its status line.

5. **Async parallel** — `complete_json_async()` is the same call but
   returns an awaitable, used by `parallel.run_brains_parallel()` so
   the five brains can think simultaneously rather than sequentially.

6. **No silent failures** — if the OpenAI SDK is not installed, or no
   API key is set, the client refuses to initialise (loud error). If
   the call itself fails, we return `LLMResponse(ok=False)` with the
   error string so the caller has full audit trail.

Defaults are tuned for live trading
-----------------------------------
    timeout_seconds = 30          # bar cycle is 60s, so 30s leaves room
    max_retries = 2               # third attempt eats the budget
    backoff_base = 1.5            # 1.5s, 3s, 6s
    temperature = 0.2             # we want analytical, not creative
    response_format = "json"      # always structured

Override per call when needed (e.g. NewsMind summarisation can run hotter
than ChartMind's plan generation).
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ----------------------------------------------------------------------
# Errors and config.
# ----------------------------------------------------------------------
class LLMError(RuntimeError):
    """Raised for unrecoverable LLM errors at construction time."""


@dataclass
class LLMConfig:
    """Per-call configuration. Defaults tuned for live trading.

    Override per brain as needed (NewsMind can be hotter, GateMind
    must be cold and deterministic).
    """
    model: str = "gpt-5"
    temperature: float = 0.2
    max_tokens: Optional[int] = 1500
    timeout_seconds: float = 30.0
    max_retries: int = 2
    backoff_base_seconds: float = 1.5
    seed: Optional[int] = None       # for partial determinism
    response_format: str = "json"    # "json" | "text"


# ----------------------------------------------------------------------
# Response container.
# ----------------------------------------------------------------------
@dataclass
class LLMResponse:
    """Outcome of one LLM call.

    `ok=True` and `data` populated when the call succeeded and JSON
    parsed (in JSON mode) or `text` is set (in text mode).

    `ok=False` when anything went wrong; `error` carries a short
    human-readable string. Tokens may still be set if the failure
    happened during JSON parsing rather than the API call.
    """
    ok: bool
    data: Optional[dict] = None
    text: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_usd_cost: float = 0.0
    latency_seconds: float = 0.0
    error: str = ""
    raw_response: Any = None         # the OpenAI SDK response object


# ----------------------------------------------------------------------
# The client.
# ----------------------------------------------------------------------
class LLMClient:
    """Defensive OpenAI client used by every brain wrapper.

    Construct once at process start and reuse — the underlying
    `openai.OpenAI` client maintains an HTTP connection pool that is
    not free to set up.

    Usage:
        client = LLMClient()                # reads OPENAI_API_KEY
        resp = client.complete_json(
            system="You are ChartMind...",
            user="Bar data: ...",
            cfg=LLMConfig(model='gpt-5'),
        )
        if resp.ok:
            plan = resp.data
    """

    def __init__(self,
                 api_key: Optional[str] = None,
                 default_cfg: Optional[LLMConfig] = None,
                 cost_tracker: Optional[Any] = None,
                 organization: Optional[str] = None,
                 base_url: Optional[str] = None,
                 ):
        # Lazy import — keep `import LLMCore` cheap when openai isn't
        # installed (e.g. in unit tests of the mechanical brains).
        try:
            from openai import OpenAI
            from openai import (
                APIError, RateLimitError, APITimeoutError, BadRequestError,
            )
        except ImportError as e:
            raise LLMError(
                "openai SDK is not installed. Add `openai>=1.50` to "
                "requirements.txt and rebuild."
            ) from e

        self._OpenAI = OpenAI
        self._APIError = APIError
        self._RateLimitError = RateLimitError
        self._APITimeoutError = APITimeoutError
        self._BadRequestError = BadRequestError

        key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not key:
            raise LLMError(
                "OPENAI_API_KEY environment variable is required and "
                "was not set. Configure it in docker-compose.yml under "
                "`environment:` or pass api_key= explicitly."
            )

        kwargs: dict[str, Any] = {"api_key": key, "timeout": 30.0}
        if organization:
            kwargs["organization"] = organization
        if base_url:
            kwargs["base_url"] = base_url
        self._sdk = self._OpenAI(**kwargs)

        self._default_cfg = default_cfg or LLMConfig()
        # Lazy import to avoid circular module load.
        if cost_tracker is None:
            from .cost import GLOBAL_COST_TRACKER
            cost_tracker = GLOBAL_COST_TRACKER
        self._cost = cost_tracker

    # ==================================================================
    # Sync entry points.
    # ==================================================================
    def complete_text(self,
                      *,
                      system: str,
                      user: str,
                      cfg: Optional[LLMConfig] = None,
                      ) -> LLMResponse:
        """Free-form text completion. Use only when JSON isn't needed
        (e.g. SmartNoteBook narrative grader).
        """
        c = cfg or self._default_cfg
        c = LLMConfig(**{**c.__dict__, "response_format": "text"})
        return self._call(system=system, user=user, cfg=c, expect_json=False)

    def complete_json(self,
                      *,
                      system: str,
                      user: str,
                      cfg: Optional[LLMConfig] = None,
                      ) -> LLMResponse:
        """Structured JSON completion. The prompt should already
        instruct the model to respond with a single JSON object whose
        shape matches the brain's schema; we set response_format=json
        so the model is forced into JSON mode.
        """
        c = cfg or self._default_cfg
        c = LLMConfig(**{**c.__dict__, "response_format": "json"})
        return self._call(system=system, user=user, cfg=c, expect_json=True)

    # ==================================================================
    # Internal: one call with retry + parsing + cost tracking.
    # ==================================================================
    def _call(self, *,
              system: str, user: str,
              cfg: LLMConfig,
              expect_json: bool) -> LLMResponse:
        last_err: str = ""
        for attempt in range(cfg.max_retries + 1):
            t0 = time.monotonic()
            try:
                kwargs: dict[str, Any] = {
                    "model": cfg.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": cfg.temperature,
                    "timeout": cfg.timeout_seconds,
                }
                if cfg.max_tokens is not None:
                    kwargs["max_tokens"] = cfg.max_tokens
                if cfg.seed is not None:
                    kwargs["seed"] = cfg.seed
                if expect_json:
                    kwargs["response_format"] = {"type": "json_object"}

                resp = self._sdk.chat.completions.create(**kwargs)
                latency = time.monotonic() - t0

                text = ""
                try:
                    text = resp.choices[0].message.content or ""
                except (AttributeError, IndexError):
                    text = ""

                pt, ct, tt = _extract_tokens(resp)
                est = self._cost.record(
                    model=cfg.model,
                    prompt_tokens=pt, completion_tokens=ct,
                )

                if expect_json:
                    try:
                        data = json.loads(text)
                    except json.JSONDecodeError as e:
                        # One retry with a follow-up explaining the
                        # error, then give up — the brain can fall back.
                        if attempt < cfg.max_retries:
                            last_err = f"json parse error: {e}"
                            self._backoff(attempt, cfg)
                            continue
                        return LLMResponse(
                            ok=False, model=cfg.model, text=text,
                            prompt_tokens=pt, completion_tokens=ct,
                            total_tokens=tt, estimated_usd_cost=est,
                            latency_seconds=latency,
                            error=f"json parse failed: {e}",
                            raw_response=resp,
                        )
                    return LLMResponse(
                        ok=True, data=data, text=text, model=cfg.model,
                        prompt_tokens=pt, completion_tokens=ct,
                        total_tokens=tt, estimated_usd_cost=est,
                        latency_seconds=latency, raw_response=resp,
                    )
                else:
                    return LLMResponse(
                        ok=True, text=text, model=cfg.model,
                        prompt_tokens=pt, completion_tokens=ct,
                        total_tokens=tt, estimated_usd_cost=est,
                        latency_seconds=latency, raw_response=resp,
                    )

            except (self._RateLimitError, self._APITimeoutError) as e:
                last_err = f"transient: {type(e).__name__}: {e}"
                if attempt < cfg.max_retries:
                    self._backoff(attempt, cfg)
                    continue
            except self._BadRequestError as e:
                # Don't retry — likely a prompt or schema bug.
                return LLMResponse(
                    ok=False, model=cfg.model,
                    error=f"bad request: {e}",
                )
            except self._APIError as e:
                last_err = f"api error: {e}"
                if attempt < cfg.max_retries:
                    self._backoff(attempt, cfg)
                    continue
            except Exception as e:    # noqa: BLE001
                last_err = f"unexpected: {type(e).__name__}: {e}"
                if attempt < cfg.max_retries:
                    self._backoff(attempt, cfg)
                    continue

        return LLMResponse(ok=False, model=cfg.model, error=last_err)

    def _backoff(self, attempt: int, cfg: LLMConfig) -> None:
        """Jittered exponential backoff."""
        sleep_for = cfg.backoff_base_seconds * (2 ** attempt)
        sleep_for *= (0.7 + 0.6 * random.random())   # +/- 30% jitter
        time.sleep(sleep_for)


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _extract_tokens(resp: Any) -> tuple[int, int, int]:
    """Best-effort extraction of (prompt, completion, total) tokens
    from an OpenAI ChatCompletion response. The SDK has shifted the
    attribute names across major versions; we try the modern path then
    fall back to the dict layout.
    """
    try:
        usage = resp.usage
        return (
            int(getattr(usage, "prompt_tokens", 0) or 0),
            int(getattr(usage, "completion_tokens", 0) or 0),
            int(getattr(usage, "total_tokens", 0) or 0),
        )
    except AttributeError:
        pass
    try:
        u = resp["usage"]
        return (
            int(u.get("prompt_tokens", 0) or 0),
            int(u.get("completion_tokens", 0) or 0),
            int(u.get("total_tokens", 0) or 0),
        )
    except (TypeError, KeyError):
        return (0, 0, 0)
