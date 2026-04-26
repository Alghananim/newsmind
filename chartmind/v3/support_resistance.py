# -*- coding: utf-8 -*-
"""Support / Resistance detection (Murphy).

Build levels from swing highs/lows by clustering touches. A level's strength
is its number of distinct touches over the lookback. Identify the nearest
key level relative to current price and its role.
"""
from __future__ import annotations
from typing import List, Optional, Tuple
from .models import Bar, Level
from .market_structure import _swing_points


def _cluster(prices: List[float], tol_atr: float, atr: float) -> List[Tuple[float, int]]:
    """Cluster prices that fall within tol_atr*ATR of each other.
    Returns list of (cluster_mean, count) sorted by mean.
    """
    if not prices: return []
    sorted_p = sorted(prices)
    clusters = []
    cur = [sorted_p[0]]
    for p in sorted_p[1:]:
        if abs(p - cur[-1]) <= tol_atr * atr:
            cur.append(p)
        else:
            clusters.append((sum(cur)/len(cur), len(cur)))
            cur = [p]
    clusters.append((sum(cur)/len(cur), len(cur)))
    return clusters


def levels_from_bars(bars: List[Bar], k: int = 2,
                     atr: Optional[float] = None,
                     tol_atr: float = 0.5) -> Tuple[List[Level], List[Level]]:
    """Return (supports, resistances) lists of Levels."""
    if not bars or len(bars) < 6:
        return [], []
    highs, lows = _swing_points(bars, k)
    high_prices = [p for _, p in highs]
    low_prices = [p for _, p in lows]

    if atr is None or atr <= 0:
        # crude fallback
        atr = (max(b.high for b in bars) - min(b.low for b in bars)) / max(1, len(bars))

    high_clusters = _cluster(high_prices, tol_atr, atr)
    low_clusters = _cluster(low_prices, tol_atr, atr)

    resistances = [Level(price=p, strength=c, role="resistance") for p, c in high_clusters]
    supports = [Level(price=p, strength=c, role="support") for p, c in low_clusters]

    return supports, resistances


def nearest_key(price: float, supports: List[Level],
                resistances: List[Level]) -> Tuple[Optional[float], str, Optional[Level]]:
    """Return (price, role, level_obj) of the closest level to current price."""
    candidates = [(s.price, "support", s) for s in supports] + \
                 [(r.price, "resistance", r) for r in resistances]
    if not candidates: return None, "none", None
    best = min(candidates, key=lambda x: abs(x[0] - price))
    return best[0], best[1], best[2]
