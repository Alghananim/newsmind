# -*- coding: utf-8 -*-
"""Candle pattern detection (Nison) — IN CONTEXT.

Patterns are recognized only when their context matches. A bullish pin-bar
in mid-range is noise; the same pin-bar at a strong support is a setup.

Patterns supported:
    engulfing (bull/bear)
    pin_bar (hammer / shooting star)
    doji (rejection)
    inside_bar (compression)
    momentum (large body, strong close)
    exhaustion (very long candle after extended trend)
"""
from __future__ import annotations
from typing import List, Optional, Tuple
from .models import Bar


def _body(b: Bar) -> float: return abs(b.close - b.open)
def _range(b: Bar) -> float: return b.high - b.low
def _upper_wick(b: Bar) -> float: return b.high - max(b.open, b.close)
def _lower_wick(b: Bar) -> float: return min(b.open, b.close) - b.low
def _is_bull(b: Bar) -> bool: return b.close > b.open
def _is_bear(b: Bar) -> bool: return b.close < b.open


def _engulfing(prev: Bar, cur: Bar) -> Optional[str]:
    """bull_engulfing if prev bear + cur bull engulfs prev body."""
    if _body(prev) <= 0 or _body(cur) <= 0: return None
    if _is_bear(prev) and _is_bull(cur):
        if cur.open <= prev.close and cur.close >= prev.open:
            return "bull_engulfing"
    if _is_bull(prev) and _is_bear(cur):
        if cur.open >= prev.close and cur.close <= prev.open:
            return "bear_engulfing"
    return None


def _pin_bar(b: Bar) -> Optional[str]:
    rng = _range(b)
    if rng == 0: return None
    body = _body(b)
    upper = _upper_wick(b)
    lower = _lower_wick(b)
    # Need long wick (>= 2x body) on one side, short wick on other
    if body / rng > 0.4: return None        # body too big for pin
    if lower >= 2 * body and upper <= body * 0.5:
        return "hammer"      # bullish reversal
    if upper >= 2 * body and lower <= body * 0.5:
        return "shooting_star"
    return None


def _doji(b: Bar) -> bool:
    rng = _range(b)
    if rng == 0: return False
    return _body(b) / rng < 0.1


def _inside_bar(prev: Bar, cur: Bar) -> bool:
    return cur.high < prev.high and cur.low > prev.low


def _momentum(b: Bar, atr: float) -> Optional[str]:
    if atr == 0: return None
    body = _body(b)
    rng = _range(b)
    if rng < 1.2 * atr: return None
    if body / rng < 0.55: return None
    if _is_bull(b) and (b.close - b.low) / rng > 0.7: return "momentum_bull"
    if _is_bear(b) and (b.high - b.close) / rng > 0.7: return "momentum_bear"
    return None


def _exhaustion(b: Bar, atr: float, trend_dir: str) -> bool:
    """Very long candle in direction of trend — possible exhaustion."""
    if atr == 0: return False
    if _range(b) < 2 * atr: return False
    if trend_dir == "bullish" and _is_bull(b): return True
    if trend_dir == "bearish" and _is_bear(b): return True
    return False


def detect(bars: List[Bar], atr: float, *,
           nearest_level_price: Optional[float] = None,
           nearest_level_role: str = "none",
           trend_dir: str = "unclear") -> dict:
    """Return dict with signal + context + quality.

    signal: engulfing/pin_bar/doji/inside_bar/momentum/exhaustion/none
    context: at_support/at_resistance/midrange/exhaustion/no_context
    quality: strong/weak/late/n_a
    """
    if len(bars) < 2:
        return {"signal": "none", "context": "no_context", "quality": "n_a",
                "details": "insufficient_bars"}

    cur = bars[-1]
    prev = bars[-2]

    # Try patterns in priority order
    sig = _engulfing(prev, cur)
    if sig is None:
        pb = _pin_bar(cur)
        if pb: sig = pb
    if sig is None and _doji(cur): sig = "doji"
    if sig is None and _inside_bar(prev, cur): sig = "inside_bar"
    if sig is None:
        m = _momentum(cur, atr)
        if m: sig = m
    if sig is None and _exhaustion(cur, atr, trend_dir):
        sig = "exhaustion_candle"
    if sig is None:
        sig = "none"

    # Context check
    context = "midrange"
    if nearest_level_price is not None and atr > 0:
        # Use 1.0×ATR as "near level"
        dist = abs(cur.close - nearest_level_price)
        if dist <= 1.0 * atr:
            context = "at_" + nearest_level_role
        else:
            context = "midrange"

    # Quality
    quality = "n_a"
    if sig != "none":
        if context.startswith("at_"):
            quality = "strong"
        elif context == "midrange":
            # Pattern in midrange = weak
            quality = "weak"
        # Late: candle right after a big move
        if atr > 0 and len(bars) >= 6:
            recent_move = abs(cur.close - bars[-6].close)
            if recent_move > 1.5 * atr:
                # We are AT a level + large move = caution; signal could be late
                if quality == "strong":
                    quality = "late"

    return {"signal": sig, "context": context, "quality": quality,
            "details": f"body={_body(cur):.5f} range={_range(cur):.5f}"}
