# -*- coding: utf-8 -*-
"""Trend strength + quality (Pring).

Strength measured via simplified ADX-like calculation.
Quality:
    smooth     — pullbacks shallow + consistent direction
    jagged     — many sign flips
    exhausting — large impulse + reduced follow-through (recent bar < prior)
"""
from __future__ import annotations
from typing import List
from .models import Bar
from . import cache as _cache


def _atr(bars: List[Bar], period: int = 14) -> float:
    return _cache.memoize(f'atr_{period}', bars, lambda: _atr_impl(bars, period))


def _atr_impl(bars: List[Bar], period: int = 14) -> float:
    if len(bars) < 2: return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l, cp = bars[i].high, bars[i].low, bars[i-1].close
        trs.append(max(h - l, abs(h - cp), abs(l - cp)))
    if not trs: return 0.0
    return sum(trs[-period:]) / min(period, len(trs))


def _adx(bars: List[Bar], period: int = 14) -> float:
    return _cache.memoize(f'adx_{period}', bars, lambda: _adx_impl(bars, period))


def _adx_impl(bars: List[Bar], period: int = 14) -> float:
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
    return 100.0 * abs(plus_di - minus_di) / (plus_di + minus_di)


def assess(bars: List[Bar]) -> dict:
    if not bars or len(bars) < 6:
        return {"direction": "unclear", "strength": 0.0, "quality": "unclear",
                "adx": 0.0, "details": "insufficient_bars"}

    atr = _atr(bars)
    adx = _adx(bars)

    # Direction from SMA position
    sma = sum(b.close for b in bars[-14:]) / min(14, len(bars))
    last_close = bars[-1].close
    if atr == 0:
        direction = "neutral"
    else:
        z = (last_close - sma) / atr
        if z > 0.5: direction = "bullish"
        elif z < -0.5: direction = "bearish"
        else: direction = "neutral"

    strength = min(1.0, adx / 50)

    # Quality: count sign flips in last 10 bars
    flips = 0
    if len(bars) >= 11:
        last_dir_close = bars[-11].close
        sign_prev = 0
        for b in bars[-10:]:
            d = b.close - last_dir_close
            sign = 1 if d > 0 else (-1 if d < 0 else 0)
            if sign != 0 and sign_prev != 0 and sign != sign_prev:
                flips += 1
            if sign != 0: sign_prev = sign
            last_dir_close = b.close

    # Exhaustion check: very large recent bar (>2×ATR) followed by smaller bars
    exhausting = False
    if len(bars) >= 4 and atr > 0:
        recent = bars[-4:]
        sizes = [b.high - b.low for b in recent]
        if sizes[0] > 2 * atr and all(s < sizes[0] * 0.6 for s in sizes[1:]):
            exhausting = True

    if exhausting:
        quality = "exhausting"
    elif flips >= 4:
        quality = "jagged"
    elif adx >= 25 and flips <= 2:
        quality = "smooth"
    else:
        quality = "unclear"

    return {"direction": direction, "strength": round(strength, 2),
            "quality": quality, "adx": round(adx, 1),
            "details": f"adx={adx:.1f} flips={flips} atr={atr:.5f}"}
