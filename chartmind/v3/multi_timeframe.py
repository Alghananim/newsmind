# -*- coding: utf-8 -*-
"""Multi-timeframe alignment (Pring/Murphy).

Rule: 15M dominates 5M dominates 1M.
    aligned        — all 3 trend the same way (or higher TFs neutral and lower TF clear)
    conflicting    — M15 vs M5 disagree, OR M5 vs M1 strongly disagree
    insufficient   — bars missing for any timeframe needed
    n_a            — only one timeframe provided
"""
from __future__ import annotations
from typing import List, Optional
from .models import Bar
from .trend import assess as trend_assess


def assess(*, bars_m15: Optional[List[Bar]] = None,
           bars_m5: Optional[List[Bar]] = None,
           bars_m1: Optional[List[Bar]] = None) -> dict:
    """Return dict with alignment label + per-TF directions."""
    dirs = {}
    if bars_m15 and len(bars_m15) >= 6:
        dirs["M15"] = trend_assess(bars_m15)["direction"]
    if bars_m5 and len(bars_m5) >= 6:
        dirs["M5"]  = trend_assess(bars_m5)["direction"]
    if bars_m1 and len(bars_m1) >= 6:
        dirs["M1"]  = trend_assess(bars_m1)["direction"]

    if len(dirs) < 2:
        return {"label": "n_a" if len(dirs) <= 1 else "insufficient", "dirs": dirs}

    m15 = dirs.get("M15")
    m5  = dirs.get("M5")
    m1  = dirs.get("M1")

    # Conflict: M15 bullish + M5 bearish (or vice versa)
    if m15 and m5 and m15 in ("bullish","bearish") and m5 in ("bullish","bearish") and m15 != m5:
        return {"label": "conflicting", "dirs": dirs,
                "details": "M15_vs_M5_opposite"}

    # If M15 clear and M5 same OR neutral → aligned
    if m15 and m15 in ("bullish","bearish"):
        if m5 in (m15, "neutral", None):
            return {"label": "aligned", "dirs": dirs,
                    "details": f"M15={m15},M5={m5},M1={m1}"}

    # Both higher TFs neutral
    if m15 in ("neutral","unclear", None) and m5 in ("neutral","unclear", None):
        return {"label": "insufficient", "dirs": dirs,
                "details": "no_higher_tf_signal"}

    return {"label": "n_a", "dirs": dirs, "details": "fallthrough"}
