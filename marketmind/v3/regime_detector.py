# -*- coding: utf-8 -*-
"""Regime classifier — labels the market with one of:
trend, range, choppy, breakout, fake_breakout, reversal, news_driven,
low_liquidity, high_volatility, dangerous, unclear.

Approach:
    * ADX-14 ≥ 25 + directional move ≥ ATR ⇒ trend
    * ADX < 18 + range/ATR small ⇒ range
    * Whipsaws (sign flips ≥ 4 in last 10 bars) + ADX 18-25 ⇒ choppy
    * Last bar range > 2× ATR + close near high/low ⇒ breakout
    * Breakout that reversed within 3 bars ⇒ fake_breakout
    * Trend that flipped polarity in last 5 bars ⇒ reversal
    * ATR > 2.5× ATR_p95 ⇒ high_volatility (or dangerous if also chase signs)
    * Volume < 0.4× avg ⇒ low_liquidity
    * Otherwise ⇒ unclear

Pure functions, no side-effects.
"""
from __future__ import annotations
from typing import List, Tuple
from .models import Bar, MarketRegime
from . import cache as _cache


def _atr(bars: List[Bar], period: int = 14) -> float:
    return _cache.memoize(f"atr_{period}", bars, lambda: _atr_uncached(bars, period))

def _atr_uncached(bars: List[Bar], period: int = 14) -> float:
    """Wilder ATR. Falls back to simple-mean if insufficient bars."""
    if len(bars) < 2: return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, c_prev = bars[i].high, bars[i].low, bars[i-1].close
        tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
        trs.append(tr)
    if not trs: return 0.0
    if len(trs) < period:
        return sum(trs) / len(trs)
    return sum(trs[-period:]) / period


def _adx(bars: List[Bar], period: int = 14) -> float:
    return _cache.memoize(f"adx_{period}", bars, lambda: _adx_uncached(bars, period))

def _adx_uncached(bars: List[Bar], period: int = 14) -> float:
    """Simplified ADX-14 — good enough for regime gating (not trading)."""
    if len(bars) < period + 1: return 0.0
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(bars)):
        up = bars[i].high - bars[i-1].high
        dn = bars[i-1].low - bars[i].low
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        h, l, cp = bars[i].high, bars[i].low, bars[i-1].close
        trs.append(max(h - l, abs(h - cp), abs(l - cp)))
    if sum(trs[-period:]) == 0: return 0.0
    plus_di = 100.0 * sum(plus_dm[-period:]) / sum(trs[-period:])
    minus_di = 100.0 * sum(minus_dm[-period:]) / sum(trs[-period:])
    if (plus_di + minus_di) == 0: return 0.0
    dx = 100.0 * abs(plus_di - minus_di) / (plus_di + minus_di)
    return dx


def _direction(bars: List[Bar], period: int = 14) -> str:
    """bullish/bearish/neutral based on close[-1] vs SMA(period)."""
    if len(bars) < period: return "unclear"
    sma = sum(b.close for b in bars[-period:]) / period
    diff = bars[-1].close - sma
    atr = _atr(bars, period)
    if atr == 0: return "neutral"
    z = diff / atr
    if z > 0.5: return "bullish"
    if z < -0.5: return "bearish"
    return "neutral"


def _whipsaw_count(bars: List[Bar], window: int = 10) -> int:
    """Count of close-direction sign flips in last `window` bars."""
    if len(bars) < window + 1: return 0
    flips = 0
    last = bars[-window-1].close
    sign_prev = 0
    for b in bars[-window:]:
        d = b.close - last
        sign = 1 if d > 0 else (-1 if d < 0 else 0)
        if sign != 0 and sign_prev != 0 and sign != sign_prev:
            flips += 1
        if sign != 0: sign_prev = sign
        last = b.close
    return flips


def _atr_percentile(bars: List[Bar], lookback: int = 100, p: float = 0.95) -> float:
    return _cache.memoize(f"atr_p{int(p*100)}_{lookback}", bars, lambda: _atr_pct_uncached(bars, lookback, p))

def _atr_pct_uncached(bars: List[Bar], lookback: int = 100, p: float = 0.95) -> float:
    """Approximate ATR percentile from rolling ATR series — last `lookback` bars only."""
    if len(bars) < 30: return _atr(bars) * 1.5  # fallback
    # Bound to lookback (avoid scanning the entire history)
    bars_window = bars[-lookback:] if len(bars) > lookback else bars
    atrs = []
    for i in range(14, len(bars_window)):
        atrs.append(_atr(bars_window[max(0,i-14):i+1]))
    if not atrs: return 0.0
    atrs.sort()
    idx = int(len(atrs) * p)
    return atrs[min(idx, len(atrs) - 1)]


def classify_regime(bars: List[Bar]) -> Tuple[MarketRegime, str, float, dict]:
    """Return (regime, direction, trend_strength_0_1, diagnostics_dict)."""
    if len(bars) < 20:
        return "unclear", "unclear", 0.0, {"reason": "insufficient_history"}

    atr = _atr(bars)
    adx = _adx(bars)
    direction = _direction(bars)
    whipsaws = _whipsaw_count(bars, 10)
    atr_p95 = _atr_percentile(bars, 100)
    last = bars[-1]
    last_range = last.high - last.low

    diag = {
        "adx": round(adx, 1),
        "atr": round(atr, 6),
        "atr_p95": round(atr_p95, 6),
        "whipsaws_10": whipsaws,
        "last_range_x_atr": round(last_range / atr, 2) if atr > 0 else 0,
    }

    # 1a. Last-bar spike: range > 4× rolling ATR ⇒ extreme/spike regardless of avg ATR
    if atr > 0 and last_range > 4 * atr:
        return "high_volatility", direction, 0.0, diag

    # 1b. Extreme rolling ATR
    if atr_p95 > 0 and atr > 2.5 * atr_p95:
        return "high_volatility", direction, min(1.0, adx / 50), diag

    # 1c. Recent breakout that has reversed (fake breakout pattern)
    # Look at last 5 bars: if any had range >= 2×ATR AND current direction is opposite
    if atr > 0 and len(bars) >= 6:
        recent_5 = bars[-5:]
        had_breakout = any((b.high - b.low) >= 2 * atr for b in recent_5[:-1])
        if had_breakout:
            # Did the close move back through the breakout?
            # Pick EARLIEST breakout — represents the original move
            breakout_idx = min(i for i, b in enumerate(recent_5[:-1])
                              if (b.high - b.low) >= 2 * atr)
            breakout_bar = recent_5[breakout_idx]
            current_close = bars[-1].close
            # If breakout was up (close > open) but current is below the breakout bar's open
            if (breakout_bar.close > breakout_bar.open
                and current_close < breakout_bar.open):
                return "fake_breakout", direction, 0.1, diag
            # If breakout was down (close < open) but current is above the breakout bar's open
            if (breakout_bar.close < breakout_bar.open
                and current_close > breakout_bar.open):
                return "fake_breakout", direction, 0.1, diag

    # 2. Breakout: last bar range >= 2× ATR + close near extreme
    if atr > 0 and last_range >= 2 * atr:
        body_top = max(last.open, last.close)
        body_bot = min(last.open, last.close)
        if last.high - body_top < 0.2 * last_range or body_bot - last.low < 0.2 * last_range:
            # Check fake_breakout: did the bar before reverse?
            if len(bars) >= 4:
                prev_breakout = any(
                    (b.high - b.low) >= 2 * atr for b in bars[-4:-1]
                )
                if prev_breakout:
                    # Did price reverse since the prior breakout?
                    if (bars[-3].close < bars[-3].open) != (last.close < last.open):
                        return "fake_breakout", direction, 0.2, diag
            return "breakout", direction, min(1.0, adx / 40), diag

    # 3. Trend
    if adx >= 25 and direction in ("bullish", "bearish"):
        return "trend", direction, min(1.0, adx / 50), diag

    # 4. Choppy (low ADX + many whipsaws)
    if whipsaws >= 4 and adx < 25:
        return "choppy", direction, 0.0, diag

    # 5. Range
    if adx < 18:
        return "range", direction, 0.0, diag

    # 6. Reversal: trend that just flipped
    if len(bars) >= 6:
        old_dir = _direction(bars[:-3], 14)
        new_dir = direction
        if old_dir != new_dir and old_dir in ("bullish","bearish") and new_dir in ("bullish","bearish"):
            return "reversal", direction, 0.3, diag

    return "unclear", direction, 0.0, diag
