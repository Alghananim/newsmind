# -*- coding: utf-8 -*-
"""Breakout detection (Brooks) — real / fake / pending / weak.

Real:    close beyond level by >= 0.3 ATR + body >= 0.5 of bar range
         + closed in upper/lower 30% of bar (direction-aware).
Fake:    breakout candle's close returned within the level within 3 bars.
Weak:    close beyond level but body < 0.5 range OR close in middle 40%.
Pending: price tested level (wick beyond) but closed back within.
"""
from __future__ import annotations
from typing import List, Optional
from .models import Bar


def assess(bars: List[Bar], level_price: float, atr: float,
           direction: str = "up", lookback: int = 5) -> dict:
    """direction: 'up' (bullish breakout) or 'down' (bearish breakout).
    Returns dict with:
        status: real/fake/pending/weak/none
        last_break_idx: index of breakout bar (or None)
    """
    if not bars or atr <= 0 or level_price is None:
        return {"status": "none", "last_break_idx": None, "details": "insufficient"}

    n = len(bars)
    last = bars[-1]

    # Look in last `lookback` bars
    breakout_idx = None
    for i in range(max(0, n - lookback), n):
        b = bars[i]
        if direction == "up":
            if b.high > level_price + 0.1 * atr:    # wick exceeded
                breakout_idx = i
                break
        else:
            if b.low < level_price - 0.1 * atr:
                breakout_idx = i
                break

    if breakout_idx is None:
        return {"status": "none", "last_break_idx": None,
                "details": "no_break_in_lookback"}

    bk = bars[breakout_idx]
    body = abs(bk.close - bk.open)
    rng = bk.high - bk.low
    if rng == 0:
        return {"status": "none", "last_break_idx": breakout_idx,
                "details": "zero_range"}

    # Did the close confirm the break?
    close_beyond = (bk.close > level_price + 0.3 * atr if direction == "up"
                    else bk.close < level_price - 0.3 * atr)

    # Strong close (close near extreme of bar in break direction)
    if direction == "up":
        close_strength = (bk.close - bk.low) / rng
    else:
        close_strength = (bk.high - bk.close) / rng

    # FAKE: breakout bar wick exceeded but close came back inside
    wick_beyond_close_inside = (
        (direction == "up" and bk.high > level_price + 0.3 * atr
         and bk.close <= level_price)
        or (direction == "down" and bk.low < level_price - 0.3 * atr
            and bk.close >= level_price)
    )

    # Subsequent bars after breakout — if price returned to level, fake
    returned_within = False
    if breakout_idx < n - 1:
        for j in range(breakout_idx + 1, n):
            after = bars[j]
            if direction == "up" and after.close < level_price:
                returned_within = True
                break
            if direction == "down" and after.close > level_price:
                returned_within = True
                break

    if wick_beyond_close_inside or returned_within:
        return {"status": "fake", "last_break_idx": breakout_idx,
                "details": f"close_strength={close_strength:.2f}"}

    if not close_beyond:
        return {"status": "pending", "last_break_idx": breakout_idx,
                "details": f"close_inside_close_strength={close_strength:.2f}"}

    if body / rng < 0.5 or close_strength < 0.6:
        return {"status": "weak", "last_break_idx": breakout_idx,
                "details": f"body_ratio={body/rng:.2f} close_str={close_strength:.2f}"}

    return {"status": "real", "last_break_idx": breakout_idx,
            "details": f"body_ratio={body/rng:.2f} close_str={close_strength:.2f}"}
