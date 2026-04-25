# -*- coding: utf-8 -*-
"""TrendAnalyzer — multi-timeframe trend alignment.

The single most important filter in any day-trading system: do not
trade against the higher-timeframe trend. We require alignment of
H4 (bias) → H1 (setup) → M15 (trigger) before a trade is "with trend".

Method per timeframe
--------------------
   1. EMA stack: EMA20 vs EMA50 vs EMA200
       - up trend: EMA20 > EMA50 > EMA200 AND price > EMA20
       - down trend: EMA20 < EMA50 < EMA200 AND price < EMA20
       - flat: anything else

   2. Wilder's ADX(14): trend strength
       - >= 25 = strong trend
       - 15-25 = mild trend
       - < 15 = ranging

   3. Higher highs / higher lows (HH/HL) sequence over last 20 bars
       - HH+HL = up
       - LH+LL = down
       - mixed = flat

We aggregate the three signals per timeframe via majority vote and
combine timeframes via simple alignment count.

Reasoning canon
---------------
   * John Murphy — *Technical Analysis of the Financial Markets*:
     "Trade with the trend on the higher timeframe."
   * Welles Wilder — *New Concepts*: ADX 25 is the canonical trend cutoff.
   * Linda Raschke — *Street Smarts*: H4 sets the bias, H1 sets the
     setup, M15 fires the trigger. Disagreement = no trade.
"""
from __future__ import annotations
from typing import Optional
from .models import TrendReading


def _ema(values: list, span: int) -> list:
    """Exponential moving average. Pure python (no numpy dependency)."""
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _adx(highs: list, lows: list, closes: list, period: int = 14) -> float:
    """Wilder ADX. Returns the latest ADX value or 0 if insufficient data."""
    n = len(closes)
    if n < period * 2 + 1:
        return 0.0
    tr_list, pdm_list, mdm_list = [], [], []
    for i in range(1, n):
        h_diff = highs[i] - highs[i-1]
        l_diff = lows[i-1] - lows[i]
        pdm = h_diff if (h_diff > l_diff and h_diff > 0) else 0.0
        mdm = l_diff if (l_diff > h_diff and l_diff > 0) else 0.0
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i-1]),
                 abs(lows[i] - closes[i-1]))
        tr_list.append(tr)
        pdm_list.append(pdm)
        mdm_list.append(mdm)
    # Wilder smoothing
    atr = sum(tr_list[:period]) / period
    pdms = sum(pdm_list[:period]) / period
    mdms = sum(mdm_list[:period]) / period
    dxs = []
    for i in range(period, len(tr_list)):
        atr = (atr * (period-1) + tr_list[i]) / period
        pdms = (pdms * (period-1) + pdm_list[i]) / period
        mdms = (mdms * (period-1) + mdm_list[i]) / period
        if atr <= 0:
            continue
        plus_di = 100 * pdms / atr
        minus_di = 100 * mdms / atr
        dsum = plus_di + minus_di
        if dsum == 0:
            dxs.append(0.0)
        else:
            dxs.append(100 * abs(plus_di - minus_di) / dsum)
    if not dxs:
        return 0.0
    # ADX = smoothed DX over `period` periods
    if len(dxs) >= period:
        adx = sum(dxs[:period]) / period
        for i in range(period, len(dxs)):
            adx = (adx * (period-1) + dxs[i]) / period
        return adx
    return sum(dxs) / len(dxs)


def _direction_from_emas(closes: list, ema20: list, ema50: list, ema200: list) -> str:
    if len(closes) < 1 or not ema20 or not ema50 or not ema200:
        return "flat"
    px = closes[-1]
    if ema20[-1] > ema50[-1] > ema200[-1] and px > ema20[-1]:
        return "up"
    if ema20[-1] < ema50[-1] < ema200[-1] and px < ema20[-1]:
        return "down"
    return "flat"


def _direction_from_swings(highs: list, lows: list, lookback: int = 20) -> str:
    """Higher highs / higher lows over last `lookback` bars."""
    if len(highs) < lookback:
        return "flat"
    h, l = highs[-lookback:], lows[-lookback:]
    # Find two most recent local extremes
    n = len(h)
    if n < 6:
        return "flat"
    mid = n // 2
    h1, h2 = max(h[:mid]), max(h[mid:])
    l1, l2 = min(l[:mid]), min(l[mid:])
    if h2 > h1 and l2 > l1:
        return "up"
    if h2 < h1 and l2 < l1:
        return "down"
    return "flat"


def _vote(*directions: str) -> str:
    """Majority vote across multiple direction calls."""
    counts = {"up": 0, "down": 0, "flat": 0}
    for d in directions:
        counts[d] = counts.get(d, 0) + 1
    if counts["up"] > counts["down"] and counts["up"] >= counts["flat"]:
        return "up"
    if counts["down"] > counts["up"] and counts["down"] >= counts["flat"]:
        return "down"
    return "flat"


class TrendAnalyzer:
    """Stateless analyzer. Pass M15 bars + optionally aggregate to H1/H4
    for full multi-timeframe view.

    Inputs are plain lists/dicts (no pandas dependency for the core).
    """
    def __init__(self,
                 ema_short: int = 20,
                 ema_med: int = 50,
                 ema_long: int = 200,
                 adx_period: int = 14):
        self.ema_short = ema_short
        self.ema_med = ema_med
        self.ema_long = ema_long
        self.adx_period = adx_period

    def analyze_m15(self, m15_bars: list) -> TrendReading:
        """Build a TrendReading purely from M15 bars by aggregating up
        to H1 and H4 internally (4 M15 bars = 1 H1; 16 M15 = 1 H4).

        m15_bars: list of objects with .open .high .low .close (chronological).
        """
        if len(m15_bars) < 200:
            return TrendReading(
                h4_direction="flat", h1_direction="flat", m15_direction="flat",
                aligned_direction="mixed", alignment_score=0.0,
                h4_adx=0.0, h1_adx=0.0, m15_adx=0.0,
            )

        h1 = self._aggregate(m15_bars, factor=4)
        h4 = self._aggregate(m15_bars, factor=16)

        m15_dir, m15_adx = self._tf_direction(m15_bars)
        h1_dir, h1_adx = self._tf_direction(h1)
        h4_dir, h4_adx = self._tf_direction(h4)

        # Alignment: count how many timeframes agree on a non-flat direction
        non_flat = [d for d in (h4_dir, h1_dir, m15_dir) if d != "flat"]
        if non_flat and len(set(non_flat)) == 1:
            aligned = non_flat[0]
            score = len(non_flat) / 3.0
        else:
            aligned = "mixed"
            score = 0.0

        return TrendReading(
            h4_direction=h4_dir, h1_direction=h1_dir, m15_direction=m15_dir,
            aligned_direction=aligned, alignment_score=score,
            h4_adx=h4_adx, h1_adx=h1_adx, m15_adx=m15_adx,
        )

    def _aggregate(self, bars: list, factor: int) -> list:
        """Aggregate `factor` consecutive M15 bars into one bar."""
        out = []
        for i in range(0, len(bars) - factor + 1, factor):
            chunk = bars[i:i+factor]
            agg = type("AggBar", (), {
                "open": chunk[0].open,
                "high": max(b.high for b in chunk),
                "low": min(b.low for b in chunk),
                "close": chunk[-1].close,
            })()
            out.append(agg)
        return out

    def _tf_direction(self, bars: list) -> tuple[str, float]:
        if len(bars) < self.ema_long + 5:
            return "flat", 0.0
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]
        ema_s = _ema(closes, self.ema_short)
        ema_m = _ema(closes, self.ema_med)
        ema_l = _ema(closes, self.ema_long)
        d_ema = _direction_from_emas(closes, ema_s, ema_m, ema_l)
        d_swing = _direction_from_swings(highs, lows, lookback=20)
        adx = _adx(highs, lows, closes, period=self.adx_period)
        # Direction = vote of EMAs and swings; if ADX too low, downgrade to flat
        d = _vote(d_ema, d_swing)
        if adx < 15:
            d = "flat"
        return d, adx
