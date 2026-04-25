# -*- coding: utf-8 -*-
"""CostTracker — process-wide LLM cost accounting.

The trading loop runs 24/5; with five brains calling LLM each cycle
(default 60s) the daily call volume is ~7,200. Cost-per-cycle is the
single most useful operational number, second only to win rate. We
track it here so `main.py` can surface a one-line summary in the log:

    [2026-04-25T14:30Z] cycle=240 cost_today=$3.27 (chart=1.4 news=0.5 ...)

Pricing
-------
Prices are estimates only — they exist so the operator has a *signal*
about cost growth, not a ledger for accounting. Update `MODEL_PRICES`
when OpenAI publishes new pricing or when the operator changes models.
The estimate uses the published per-million token rate; we deliberately
round generously so the printed number leans high rather than low.

Thread safety
-------------
The tracker uses a single Lock around its counter dict so concurrent
brain calls (the parallel runner) safely accumulate.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional


# ----------------------------------------------------------------------
# Pricing table. USD per 1M tokens. UPDATE when OpenAI changes prices.
# ----------------------------------------------------------------------
MODEL_PRICES: dict[str, tuple[float, float]] = {
    # model_name: (prompt_per_million, completion_per_million)
    # Defaults are placeholders; the live model name (gpt-5) prices
    # should be filled in once OpenAI publishes them officially.
    "gpt-5":         (5.00, 15.00),
    "gpt-5-mini":    (0.30, 1.20),
    "gpt-5-nano":    (0.05, 0.20),
    "gpt-4o":        (5.00, 15.00),
    "gpt-4o-mini":   (0.15, 0.60),
}

_DEFAULT_PRICE = (5.00, 15.00)   # used when model not in table


# ----------------------------------------------------------------------
# Tracker.
# ----------------------------------------------------------------------
@dataclass
class _DayBucket:
    """One UTC day's accumulated counters, broken down by model."""
    day: date
    by_model_prompt: dict[str, int] = field(default_factory=dict)
    by_model_completion: dict[str, int] = field(default_factory=dict)
    by_model_calls: dict[str, int] = field(default_factory=dict)
    by_model_cost_usd: dict[str, float] = field(default_factory=dict)


class CostTracker:
    """Process-wide accumulator. Construct once; share across brains.

    Public API:
        record(model, prompt_tokens, completion_tokens) -> usd_for_call
        snapshot() -> dict (today + lifetime)
        one_line_summary() -> str
        reset_today() -> None
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._today: _DayBucket = _DayBucket(day=_utc_today())
        self._lifetime_calls: int = 0
        self._lifetime_prompt_tokens: int = 0
        self._lifetime_completion_tokens: int = 0
        self._lifetime_cost_usd: float = 0.0

    # ----- write -----------------------------------------------------
    def record(self, *,
               model: str,
               prompt_tokens: int,
               completion_tokens: int,
               ) -> float:
        """Record one call's tokens. Returns the estimated USD cost."""
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        ppm, cpm = MODEL_PRICES.get(model, _DEFAULT_PRICE)
        cost = (prompt_tokens * ppm + completion_tokens * cpm) / 1_000_000.0

        with self._lock:
            self._roll_day_if_needed_locked()
            t = self._today
            t.by_model_prompt[model] = t.by_model_prompt.get(model, 0) + prompt_tokens
            t.by_model_completion[model] = (
                t.by_model_completion.get(model, 0) + completion_tokens
            )
            t.by_model_calls[model] = t.by_model_calls.get(model, 0) + 1
            t.by_model_cost_usd[model] = (
                t.by_model_cost_usd.get(model, 0.0) + cost
            )
            self._lifetime_calls += 1
            self._lifetime_prompt_tokens += prompt_tokens
            self._lifetime_completion_tokens += completion_tokens
            self._lifetime_cost_usd += cost
        return cost

    # ----- read ------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            self._roll_day_if_needed_locked()
            t = self._today
            today_total_cost = sum(t.by_model_cost_usd.values())
            today_total_calls = sum(t.by_model_calls.values())
            return {
                "today": {
                    "date": t.day.isoformat(),
                    "calls": today_total_calls,
                    "cost_usd": round(today_total_cost, 4),
                    "by_model": {
                        m: {
                            "calls": t.by_model_calls[m],
                            "prompt_tokens": t.by_model_prompt[m],
                            "completion_tokens": t.by_model_completion[m],
                            "cost_usd": round(t.by_model_cost_usd[m], 4),
                        } for m in t.by_model_calls
                    },
                },
                "lifetime": {
                    "calls": self._lifetime_calls,
                    "prompt_tokens": self._lifetime_prompt_tokens,
                    "completion_tokens": self._lifetime_completion_tokens,
                    "cost_usd": round(self._lifetime_cost_usd, 4),
                },
            }

    def one_line_summary(self) -> str:
        s = self.snapshot()
        today = s["today"]
        return (
            f"cost_today=${today['cost_usd']:.2f} "
            f"calls={today['calls']} "
            f"lifetime=${s['lifetime']['cost_usd']:.2f}"
        )

    # ----- maintenance ----------------------------------------------
    def reset_today(self) -> None:
        with self._lock:
            self._today = _DayBucket(day=_utc_today())

    def _roll_day_if_needed_locked(self) -> None:
        today = _utc_today()
        if self._today.day != today:
            self._today = _DayBucket(day=today)


# ----------------------------------------------------------------------
# Helpers + global instance.
# ----------------------------------------------------------------------
def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


# Default tracker shared across the process. Brains can opt out by
# passing their own tracker into LLMClient(cost_tracker=...).
GLOBAL_COST_TRACKER = CostTracker()
