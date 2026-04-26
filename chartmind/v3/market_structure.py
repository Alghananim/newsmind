# -*- coding: utf-8 -*-
"""Market structure (Murphy/Brooks) — HH/HL/LH/LL + BoS + CHoCH.

Approach:
    1. Find swing highs and swing lows over a window.
    2. Compare consecutive swings to label structure.
    3. Detect Break of Structure (BoS) when price closes beyond the most recent
       opposing swing.
    4. Detect Change of Character (CHoCH) when price flips structure: e.g. a
       lower-low after a series of higher-lows.
"""
from __future__ import annotations
from typing import List, Tuple, Optional
from .models import Bar
from . import cache as _cache


def _swing_points(bars: List[Bar], k: int = 2) -> Tuple[list, list]:
    return _cache.memoize(f'swings_{k}', bars, lambda: _swing_points_impl(bars, k))


def _swing_points_impl(bars: List[Bar], k: int = 2) -> Tuple[list, list]:
    """Return (swing_highs, swing_lows) as (idx, price) lists.
    A swing high at i means bars[i].high is greater than all bars in [i-k, i+k].
    """
    highs, lows = [], []
    n = len(bars)
    for i in range(k, n - k):
        is_high = all(bars[i].high >= bars[j].high for j in range(i-k, i+k+1) if j != i)
        is_low = all(bars[i].low <= bars[j].low for j in range(i-k, i+k+1) if j != i)
        if is_high: highs.append((i, bars[i].high))
        if is_low: lows.append((i, bars[i].low))
    return highs, lows


def classify(bars: List[Bar], k: int = 2) -> dict:
    """Return dict with structure label + supporting facts."""
    if not bars or len(bars) < 6:
        return {"structure": "unclear", "trend": "unclear",
                "swing_highs": [], "swing_lows": [],
                "bos": None, "choch": None}

    highs, lows = _swing_points(bars, k)
    last_close = bars[-1].close

    # Need at least 2 highs and 2 lows
    if len(highs) < 2 or len(lows) < 2:
        return {"structure": "unclear", "trend": "unclear",
                "swing_highs": highs, "swing_lows": lows,
                "bos": None, "choch": None}

    # Compare last two highs & last two lows
    h1, h2 = highs[-2][1], highs[-1][1]
    l1, l2 = lows[-2][1], lows[-1][1]

    higher_high = h2 > h1
    lower_high = h2 < h1
    higher_low = l2 > l1
    lower_low = l2 < l1

    structure = "unclear"
    trend = "neutral"

    if higher_high and higher_low:
        structure, trend = "uptrend", "bullish"
    elif lower_high and lower_low:
        structure, trend = "downtrend", "bearish"
    elif (higher_high and lower_low) or (lower_high and higher_low):
        structure, trend = "range", "neutral"

    # BoS: close beyond opposing swing
    bos = None
    last_high = highs[-1][1] if highs else None
    last_low = lows[-1][1] if lows else None
    if last_high is not None and last_close > last_high * 1.0001:
        bos = "up"
        structure = "bos_up"
    elif last_low is not None and last_close < last_low * 0.9999:
        bos = "down"
        structure = "bos_down"

    # CHoCH: structure flipped vs recent past
    choch = None
    if len(highs) >= 3 and len(lows) >= 3:
        prev_pattern_bull = (highs[-3][1] < highs[-2][1] and lows[-3][1] < lows[-2][1])
        prev_pattern_bear = (highs[-3][1] > highs[-2][1] and lows[-3][1] > lows[-2][1])
        if prev_pattern_bull and lower_low:
            choch = "down"
            structure = "choch_down"
        elif prev_pattern_bear and higher_high:
            choch = "up"
            structure = "choch_up"

    return {
        "structure": structure,
        "trend": trend,
        "swing_highs": highs,
        "swing_lows": lows,
        "bos": bos,
        "choch": choch,
    }
