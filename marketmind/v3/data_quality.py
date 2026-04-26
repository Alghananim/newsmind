# -*- coding: utf-8 -*-
"""Data quality checker — gap/spread/ATR/staleness anomalies.

Returns (status, warnings_list).
status: good / partial / degraded / missing
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple
from .models import Bar
from .regime_detector import _atr, _atr_percentile


def assess(*, bars: List[Bar],
           expected_interval_min: int = 15,
           now_utc: Optional[datetime] = None) -> Tuple[str, list]:
    warnings = []
    if not bars or len(bars) < 5:
        return "missing", ["insufficient_bars"]

    now = now_utc or datetime.now(timezone.utc)

    # 1. Staleness / clock-skew: last bar timestamp too old OR in future
    last_ts = bars[-1].timestamp
    if last_ts.tzinfo is None: last_ts = last_ts.replace(tzinfo=timezone.utc)
    age = (now - last_ts).total_seconds() / 60.0
    if age > expected_interval_min * 2:
        warnings.append(f"stale_data:{int(age)}min")
    if age < -expected_interval_min:    # bar is more than one interval in the future
        warnings.append(f"future_dated_bars:{int(-age)}min_ahead")

    # 2. Gaps between consecutive bars
    atr = _atr(bars)
    gaps = 0
    for i in range(1, len(bars)):
        gap = abs(bars[i].open - bars[i-1].close)
        if atr > 0 and gap > 0.5 * atr:
            gaps += 1
    if gaps >= 2:
        warnings.append(f"unexplained_gaps:{gaps}")

    # 3. Spread anomalies
    spreads = [b.spread_pips for b in bars if b.spread_pips is not None]
    if spreads:
        avg_spread = sum(spreads) / len(spreads)
        cur_spread = bars[-1].spread_pips or avg_spread
        if avg_spread > 0 and cur_spread > 3 * avg_spread:
            warnings.append(f"wide_spread:{cur_spread:.2f}pips_vs_{avg_spread:.2f}avg")

    # 4. ATR extremes
    atr_p95 = _atr_percentile(bars)
    if atr_p95 > 0 and atr > 2.5 * atr_p95:
        warnings.append(f"atr_extreme:{atr/atr_p95:.1f}x_p95")

    # 5. Volume drought (zero volume on >50% of last 10 bars)
    if len(bars) >= 10:
        zero_vol = sum(1 for b in bars[-10:] if (b.volume or 0) == 0)
        if zero_vol >= 5:
            warnings.append(f"low_volume:{zero_vol}/10_bars_zero")

    if not warnings: return "good", []
    if len(warnings) <= 1: return "partial", warnings
    if len(warnings) <= 2: return "degraded", warnings
    return "missing", warnings
