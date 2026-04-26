# -*- coding: utf-8 -*-
"""Trap detection — liquidity sweep, bull/bear trap, stop hunt, chop trap.

Each detector returns True/False. Orchestrator collects them as warnings
and the permission engine treats critical ones as hard-block conditions.
"""
from __future__ import annotations
from typing import List
from .models import Bar


def liquidity_sweep(bars: List[Bar], level: float, atr: float,
                    direction: str = "up") -> bool:
    """A long wick that exceeds a known level by >1 ATR but the close
    returns within the level. Classic stop-hunt + reversal pattern."""
    if not bars or atr <= 0 or level is None: return False
    last = bars[-1]
    if direction == "up":
        wick = last.high - level
        return wick > 1.0 * atr and last.close <= level + 0.2 * atr
    else:
        wick = level - last.low
        return wick > 1.0 * atr and last.close >= level - 0.2 * atr


def bull_trap(bars: List[Bar], level: float, atr: float,
              higher_tf_trend: str = "neutral") -> bool:
    """Breakout up while higher timeframe is bearish, then close returns to level."""
    if not bars or len(bars) < 4 or atr <= 0 or level is None: return False
    if higher_tf_trend != "bearish": return False
    # Find recent up-break
    for i in range(-4, 0):
        if bars[i].high > level + 0.3 * atr:
            # Then later close back below level
            for j in range(i+1, 0):
                if j < 0 and bars[j].close < level:
                    return True
    return False


def bear_trap(bars: List[Bar], level: float, atr: float,
              higher_tf_trend: str = "neutral") -> bool:
    if not bars or len(bars) < 4 or atr <= 0 or level is None: return False
    if higher_tf_trend != "bullish": return False
    for i in range(-4, 0):
        if bars[i].low < level - 0.3 * atr:
            for j in range(i+1, 0):
                if j < 0 and bars[j].close > level:
                    return True
    return False


def stop_hunt(bars: List[Bar], level: float, atr: float) -> bool:
    """Spike of >1.5 ATR followed by reversal within 2 bars near a clustered level."""
    if not bars or len(bars) < 3 or atr <= 0 or level is None: return False
    spike_idx = None
    for i in range(-3, 0):
        b = bars[i]
        if (b.high - b.low) > 1.5 * atr:
            spike_idx = i
            break
    if spike_idx is None: return False
    spike_bar = bars[spike_idx]
    last = bars[-1]
    # Spike must have crossed near level
    if not (spike_bar.low <= level <= spike_bar.high): return False
    # Reversal: last close opposite of spike body direction
    spike_bull = spike_bar.close > spike_bar.open
    last_bull = last.close > last.open
    return spike_bull != last_bull


def chop_trap(bars: List[Bar]) -> bool:
    """≥4 sign flips in last 10 bars + ADX < 18."""
    if len(bars) < 11: return False
    flips = 0
    last = bars[-11].close
    sign_prev = 0
    for b in bars[-10:]:
        d = b.close - last
        sign = 1 if d > 0 else (-1 if d < 0 else 0)
        if sign != 0 and sign_prev != 0 and sign != sign_prev:
            flips += 1
        if sign != 0: sign_prev = sign
        last = b.close
    if flips < 4: return False
    # crude ADX proxy: range/atr ratio low → choppy
    from .trend import _adx
    return _adx(bars) < 18
