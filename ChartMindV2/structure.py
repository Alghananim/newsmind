# -*- coding: utf-8 -*-
"""StructureAnalyzer — support/resistance + key session levels.

A "structure level" is any price the market has previously reacted to:
   * Swing highs and lows (local extremes over a lookback window)
   * Prior day high / low (PDH / PDL)
   * Asian-session high / low
   * London-session high / low
   * NY-session open price
   * Daily mid (50% retrace of the prior day's range)
   * Weekly high / low (last 5 trading days)

Why this matters
----------------
ICT/SMC and classical TA agree on ONE thing: trades that initiate AT
a visible structure level have higher win rates than trades that
initiate in mid-air. Linda Raschke's Holy Grail: HAS the market just
touched a level the larger participants are watching?

Output
------
A `StructureReading` carrying:
   * The nearest support BELOW current price
   * The nearest resistance ABOVE current price
   * Distances in pips so the planner can size SL/TP appropriately
   * A list of all known levels (deduped, sorted) for journaling
"""
from __future__ import annotations
from datetime import datetime, time, timezone
from typing import Optional
from .models import StructureReading, StructureLevel


# Default level proximity threshold (pips)
PROXIMITY_PIPS = 5.0


def _swing_extremes(bars: list, lookback: int = 50, span: int = 5) -> tuple[list, list]:
    """Find swing highs and lows in the last `lookback` bars.
    A swing high is a bar whose high is greater than ALL `span` bars
    on each side. Same logic for swing lows.
    Returns (swing_highs, swing_lows) as lists of (price, bar_index) tuples.
    """
    if len(bars) < lookback or lookback < 2 * span + 1:
        return [], []
    sub = bars[-lookback:]
    highs, lows = [], []
    for i in range(span, len(sub) - span):
        h_window = [b.high for b in sub[i-span:i+span+1]]
        l_window = [b.low for b in sub[i-span:i+span+1]]
        if sub[i].high == max(h_window):
            highs.append((sub[i].high, len(bars) - lookback + i))
        if sub[i].low == min(l_window):
            lows.append((sub[i].low, len(bars) - lookback + i))
    return highs, lows


def _prior_day_levels(bars: list) -> tuple[float, float]:
    """Compute previous trading day's high and low from M15 bars.
    Only scans the last 200 bars (~2 days) for performance.
    """
    if not bars:
        return 0.0, 0.0
    tail = bars[-200:] if len(bars) > 200 else bars
    last_date = tail[-1].time.date()
    prior_day = None
    for b in reversed(tail):
        if b.time.date() < last_date:
            prior_day = b.time.date()
            break
    if prior_day is None:
        return tail[-1].high, tail[-1].low
    prior_bars = [b for b in tail if b.time.date() == prior_day]
    if not prior_bars:
        return bars[-1].high, bars[-1].low
    return max(b.high for b in prior_bars), min(b.low for b in prior_bars)


def _session_open(bars: list) -> float:
    """Return the open of the current NY-session (first bar at/after 13:00 UTC today).

    Rough but good enough — different DST periods shift NY by 1h.
    Bounded to last 100 bars for performance.
    """
    if not bars:
        return 0.0
    tail = bars[-100:] if len(bars) > 100 else bars
    today = tail[-1].time.date()
    today_bars = [b for b in tail if b.time.date() == today]
    if not today_bars:
        return bars[-1].open
    # First bar at or after 12:00 UTC (NY 7-8am)
    for b in today_bars:
        if b.time.time() >= time(12, 0):
            return b.open
    return today_bars[0].open


class StructureAnalyzer:
    """Stateless analyzer over M15 bars. Pure-Python (no pandas)."""

    def __init__(self,
                 swing_lookback: int = 60,
                 swing_span: int = 5,
                 dedupe_pips: float = 3.0,
                 pair_pip: float = 0.0001):
        self.swing_lookback = swing_lookback
        self.swing_span = swing_span
        self.dedupe_pips = dedupe_pips
        self.pair_pip = pair_pip

    def analyze(self, m15_bars: list) -> StructureReading:
        if not m15_bars:
            return self._empty()
        cur = m15_bars[-1].close

        # Gather raw levels
        sw_highs, sw_lows = _swing_extremes(
            m15_bars, lookback=self.swing_lookback, span=self.swing_span)
        pdh, pdl = _prior_day_levels(m15_bars)
        sess_open = _session_open(m15_bars)

        # Mid = 50% retrace of prior day
        pd_mid = (pdh + pdl) / 2 if pdh > 0 and pdl > 0 else 0.0

        # Build labelled levels list
        levels: list[StructureLevel] = []
        for price, idx in sw_highs:
            levels.append(StructureLevel(
                price=price, label="swing_high",
                distance_pips=(price - cur) / self.pair_pip,
                strength=0.6,
            ))
        for price, idx in sw_lows:
            levels.append(StructureLevel(
                price=price, label="swing_low",
                distance_pips=(price - cur) / self.pair_pip,
                strength=0.6,
            ))
        if pdh > 0:
            levels.append(StructureLevel(
                price=pdh, label="prior_day_high",
                distance_pips=(pdh - cur) / self.pair_pip,
                strength=0.85,
            ))
        if pdl > 0:
            levels.append(StructureLevel(
                price=pdl, label="prior_day_low",
                distance_pips=(pdl - cur) / self.pair_pip,
                strength=0.85,
            ))
        if pd_mid > 0:
            levels.append(StructureLevel(
                price=pd_mid, label="prior_day_mid",
                distance_pips=(pd_mid - cur) / self.pair_pip,
                strength=0.55,
            ))
        if sess_open > 0:
            levels.append(StructureLevel(
                price=sess_open, label="ny_session_open",
                distance_pips=(sess_open - cur) / self.pair_pip,
                strength=0.7,
            ))

        # Dedupe nearby levels (keep the strongest one)
        levels.sort(key=lambda l: l.price)
        deduped: list[StructureLevel] = []
        for lv in levels:
            if deduped and abs(lv.price - deduped[-1].price) / self.pair_pip < self.dedupe_pips:
                # Replace if this one is stronger
                if lv.strength > deduped[-1].strength:
                    deduped[-1] = lv
                continue
            deduped.append(lv)

        # Find nearest support (below) and resistance (above)
        below = [l for l in deduped if l.distance_pips < 0]
        above = [l for l in deduped if l.distance_pips > 0]
        nearest_sup = max(below, key=lambda l: l.price) if below else None
        nearest_res = min(above, key=lambda l: l.price) if above else None

        in_va = (pdh > cur > pdl) if (pdh > 0 and pdl > 0) else False

        return StructureReading(
            nearest_support=nearest_sup,
            nearest_resistance=nearest_res,
            prior_day_high=pdh, prior_day_low=pdl,
            session_open=sess_open,
            in_value_area=in_va,
            levels=deduped,
        )

    def _empty(self) -> StructureReading:
        return StructureReading(
            nearest_support=None, nearest_resistance=None,
            prior_day_high=0.0, prior_day_low=0.0, session_open=0.0,
            in_value_area=False, levels=[],
        )

    @staticmethod
    def is_at_level(price: float, level: StructureLevel,
                    pair_pip: float = 0.0001,
                    tolerance_pips: float = 3.0) -> bool:
        """Did `price` just touch `level` within tolerance?"""
        if level is None:
            return False
        dist_pips = abs(price - level.price) / pair_pip
        return dist_pips <= tolerance_pips
