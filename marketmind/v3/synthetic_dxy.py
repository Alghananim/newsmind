# -*- coding: utf-8 -*-
"""Synthetic Dollar Strength Index — proxy for DXY when not directly available.

ICE DXY weights (we use these even though we usually have only a subset):
    EUR/USD : 57.6%   (inverse — EUR up = DXY down)
    USD/JPY : 13.6%   (direct — USD/JPY up = DXY up)
    GBP/USD : 11.9%   (inverse)
    USD/CAD :  9.1%   (direct)
    SEK/USD :  4.2%   (inverse)
    USD/CHF :  3.6%   (direct)

If we only have a partial basket, we re-normalize weights across what's
available. If only EUR/USD + USD/JPY exist, we get a 71% basket — still
indicative.

Returns:
    SyntheticDxyResult(
        value:         current synthetic DXY level (normalized, base 100)
        direction:     up/down/flat
        strength:      0..1 (how strong USD looks)
        components:    dict of contributions
        coverage:      fraction of weights actually represented
    )
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional, List
from .models import Bar


WEIGHTS = {
    "EUR/USD": (-1, 0.576),  # sign, weight
    "USD/JPY": (+1, 0.136),
    "GBP/USD": (-1, 0.119),
    "USD/CAD": (+1, 0.091),
    "SEK/USD": (-1, 0.042),
    "USD/CHF": (+1, 0.036),
}


@dataclass
class SyntheticDxyResult:
    value: float = 100.0
    direction: str = "flat"
    strength: float = 0.5
    components: dict = field(default_factory=dict)
    coverage: float = 0.0


def compute(*, baskets: dict[str, List[Bar]],
            window: int = 20) -> SyntheticDxyResult:
    """`baskets` is {pair_name -> bars}. Pairs missing are skipped.

    Strength = fraction of components moving in USD-strong direction over `window`.
    Direction = up/down/flat based on net delta over `window`.
    """
    contribs = {}
    total_w = 0.0
    net_delta = 0.0
    pos_components = 0
    n_components = 0

    for pair, (sign, w) in WEIGHTS.items():
        bars = baskets.get(pair)
        if not bars or len(bars) < window + 1:
            continue
        old = bars[-window-1].close
        new = bars[-1].close
        if old == 0: continue
        pct = (new - old) / old
        usd_contribution = sign * pct  # +ve means USD-strong
        contribs[pair] = round(usd_contribution * 100, 4)
        net_delta += usd_contribution * w
        total_w += w
        n_components += 1
        if usd_contribution > 0: pos_components += 1

    if total_w == 0:
        return SyntheticDxyResult()

    coverage = total_w / sum(w for _, w in WEIGHTS.values())
    # Normalize net_delta to a 0..1 strength:
    # raw scale is roughly ±0.02 (2% move) — clip to ±0.01 for sensitivity
    raw = net_delta / total_w
    strength = max(0.0, min(1.0, 0.5 + raw / 0.02))
    direction = "up" if raw > 0.0005 else ("down" if raw < -0.0005 else "flat")

    return SyntheticDxyResult(
        value=round(100 * (1 + raw), 4),
        direction=direction,
        strength=round(strength, 3),
        components=contribs,
        coverage=round(coverage, 3),
    )
