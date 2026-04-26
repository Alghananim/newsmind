# -*- coding: utf-8 -*-
"""Rolling correlation tracker + broken-correlation detector.

Tracks log-return correlations between:
    EUR/USD vs synthetic_DXY
    USD/JPY vs synthetic_DXY
    USD/JPY vs Gold (usually negative)
    EUR/USD vs USD/JPY (usually inverse: USD strength affects both)

Detects "broken correlation" when current rolling correlation diverges
sharply from historical norm. That signals abnormal regime — wait.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional
from .models import Bar


@dataclass
class CorrelationResult:
    status: str = "unavailable"     # normal / broken / unavailable
    pairs: dict = field(default_factory=dict)   # {"EURUSD_vs_DXY": float, ...}
    anomalies: tuple = ()
    rationale: tuple = ()


def _log_returns(bars: List[Bar]) -> List[float]:
    out = []
    for i in range(1, len(bars)):
        if bars[i-1].close <= 0 or bars[i].close <= 0: continue
        out.append(math.log(bars[i].close / bars[i-1].close))
    return out


def _pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    n = min(len(xs), len(ys))
    if n < 5: return None
    xs, ys = xs[-n:], ys[-n:]
    mx = sum(xs) / n; my = sum(ys) / n
    cov = sum((xs[i]-mx)*(ys[i]-my) for i in range(n)) / n
    vx = sum((x-mx)**2 for x in xs) / n
    vy = sum((y-my)**2 for y in ys) / n
    if vx == 0 or vy == 0: return None
    return cov / math.sqrt(vx * vy)


# Expected normal correlation ranges (historical norms on M15 last 60 bars)
EXPECTED = {
    "EURUSD_vs_USDJPY": (-1.0, 0.1),    # accept full negative; mild positive ok   # usually negative (USD strength)
    "EURUSD_vs_GOLD":   (-0.3, 0.7),      # usually positive
    "USDJPY_vs_GOLD":   (-0.7, 0.3),     # usually negative (gold + JPY havens)
    "EURUSD_vs_SPX":    (-0.2, 0.5),     # variable
    "USDJPY_vs_SPX":    (0.0, 0.6),      # usually positive (risk-on)
}


def assess(*, bars_eurusd: Optional[List[Bar]] = None,
           bars_usdjpy: Optional[List[Bar]] = None,
           bars_xau: Optional[List[Bar]] = None,
           bars_spx: Optional[List[Bar]] = None,
           window: int = 60) -> CorrelationResult:
    rationale = []
    pairs = {}
    anomalies = []

    series = {
        "EURUSD": _log_returns(bars_eurusd or [])[-window:],
        "USDJPY": _log_returns(bars_usdjpy or [])[-window:],
        "GOLD":   _log_returns(bars_xau or [])[-window:],
        "SPX":    _log_returns(bars_spx or [])[-window:],
    }

    test_pairs = [
        ("EURUSD_vs_USDJPY", "EURUSD", "USDJPY"),
        ("EURUSD_vs_GOLD",   "EURUSD", "GOLD"),
        ("USDJPY_vs_GOLD",   "USDJPY", "GOLD"),
        ("EURUSD_vs_SPX",    "EURUSD", "SPX"),
        ("USDJPY_vs_SPX",    "USDJPY", "SPX"),
    ]

    for label, a, b in test_pairs:
        if not series[a] or not series[b]: continue
        c = _pearson(series[a], series[b])
        if c is None: continue
        pairs[label] = round(c, 3)
        lo, hi = EXPECTED.get(label, (-1, 1))
        if c < lo - 0.4 or c > hi + 0.4:
            anomalies.append(f"{label}={c:.2f}_outside_{lo}..{hi}")

    if not pairs:
        return CorrelationResult(rationale=("no_data",))

    status = "broken" if anomalies else "normal"
    return CorrelationResult(
        status=status, pairs=pairs,
        anomalies=tuple(anomalies),
        rationale=tuple(rationale or ["computed"]),
    )
