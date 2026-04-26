# -*- coding: utf-8 -*-
"""Alignment check — do all 3 brains agree on direction + permission?"""
from __future__ import annotations
from typing import Optional
from .models import BrainSummary


def check(news: Optional[BrainSummary], market: Optional[BrainSummary],
          chart: Optional[BrainSummary]) -> dict:
    if news is None or market is None or chart is None:
        return {"status": "missing", "direction": "none",
                "details": "missing_brain_input"}

    perms = (news.permission, market.permission, chart.permission)
    grades = (news.grade, market.grade, chart.grade)

    # Block-by-brain (any block dominates)
    if "block" in perms:
        blocking = [b.name for b in (news, market, chart) if b.permission == "block"]
        return {"status": "blocked_by_brain", "direction": "none",
                "details": f"blocked_by={blocking}"}

    # All allow + check direction agreement
    if all(p == "allow" for p in perms):
        # News bias is per-pair (bullish/bearish/neutral), market/chart use direction
        directions = [news.direction, market.direction, chart.direction]
        clear = [d for d in directions if d in ("bullish", "bearish")]
        if len(clear) >= 2 and len(set(clear)) == 1:
            dir_word = "buy" if clear[0] == "bullish" else "sell"
            return {"status": "aligned", "direction": dir_word,
                    "details": f"all_allow_{clear[0]}"}
        if len(set(clear)) > 1:
            return {"status": "conflicting", "direction": "none",
                    "details": f"directions={directions}"}
        return {"status": "partial", "direction": "none",
                "details": "all_allow_but_direction_unclear"}

    # Any wait
    if "wait" in perms:
        waiters = [b.name for b in (news, market, chart) if b.permission == "wait"]
        return {"status": "partial", "direction": "none",
                "details": f"wait_from={waiters}"}

    return {"status": "partial", "direction": "none",
            "details": f"perms={perms}"}
