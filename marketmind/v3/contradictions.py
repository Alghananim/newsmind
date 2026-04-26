# -*- coding: utf-8 -*-
"""Cross-market contradiction detector.

Each check returns a (label, severity) tuple. Severities:
    "critical" — block the trade
    "high"     — wait
    "medium"   — cap at B
    "low"      — informational only

The detector runs all checks in a single pass and collects them. The
permission_engine consumes the result as additional input to the grade
ladder.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from .models import Bar


@dataclass
class ContradictionResult:
    items: list = field(default_factory=list)   # list of (label, severity)
    @property
    def critical(self) -> bool:
        return any(s == "critical" for _, s in self.items)
    @property
    def high(self) -> bool:
        return any(s == "high" for _, s in self.items)
    @property
    def medium(self) -> bool:
        return any(s == "medium" for _, s in self.items)
    def labels(self) -> tuple:
        return tuple(label for label, _ in self.items)
    def summary(self) -> dict:
        return {
            "count": len(self.items),
            "critical": sum(1 for _, s in self.items if s == "critical"),
            "high": sum(1 for _, s in self.items if s == "high"),
            "medium": sum(1 for _, s in self.items if s == "medium"),
            "labels": list(self.labels()),
        }


def _pct(bars, window):
    if not bars or len(bars) < window + 1: return None
    old = bars[-window-1].close
    if old == 0: return None
    return (bars[-1].close - old) / old


def detect(*,
           pair: str,
           bars: List[Bar],
           dxy_dir: str = "flat",
           dxy_strength: float = 0.5,
           bars_usdjpy: Optional[List[Bar]] = None,
           bars_eurusd: Optional[List[Bar]] = None,
           bars_xau: Optional[List[Bar]] = None,
           bars_spx: Optional[List[Bar]] = None,
           news_bias: str = "unclear",
           news_perm: str = "allow",
           risk_mode: str = "unclear",
           market_direction: str = "neutral",
           ) -> ContradictionResult:
    items: list = []

    # Compute short-window pct moves once
    eur_pct = _pct(bars_eurusd, 10) if bars_eurusd else None
    jpy_pct = _pct(bars_usdjpy, 10) if bars_usdjpy else None
    xau_pct = _pct(bars_xau, 10) if bars_xau else None
    spx_pct = _pct(bars_spx, 10) if bars_spx else None

    # Pair recent direction (last 10 bars)
    pair_pct = _pct(bars, 10) if bars else None

    # 1. DXY ↑ + EUR/USD ↑  (or DXY ↓ + EUR/USD ↓) — broken USD signal
    if eur_pct is not None and abs(eur_pct) > 0.001:
        if dxy_dir == "up" and eur_pct > 0.001:
            items.append(("dxy_up_with_eurusd_up", "high"))
        elif dxy_dir == "down" and eur_pct < -0.001:
            items.append(("dxy_down_with_eurusd_down", "high"))

    # 2. EUR/USD ↑ + USD/JPY ↑ — inconsistent USD signal
    if eur_pct is not None and jpy_pct is not None:
        if eur_pct > 0.001 and jpy_pct > 0.001:
            items.append(("eurusd_and_usdjpy_both_up_inconsistent_usd", "high"))
        elif eur_pct < -0.001 and jpy_pct < -0.001:
            items.append(("eurusd_and_usdjpy_both_down_inconsistent_usd", "high"))

    # 3. risk_off + USD/JPY ↑ — JPY haven flow violated
    if risk_mode == "risk_off" and jpy_pct is not None and jpy_pct > 0.0005:
        items.append(("risk_off_but_usdjpy_rising_haven_violated", "high"))

    # 4. Gold ↑ AND Dollar ↑ — both havens rising = abnormal regime
    if xau_pct is not None and xau_pct > 0.005 and dxy_dir == "up" and dxy_strength > 0.55:
        items.append(("gold_up_and_dollar_up_abnormal_regime", "medium"))

    # 5. SPX ↓ + USD/JPY ↑ — risk-off but yen weakening = strong contradiction
    if spx_pct is not None and jpy_pct is not None:
        if spx_pct < -0.005 and jpy_pct > 0.001:
            items.append(("spx_down_but_usdjpy_up_risk_off_violated", "high"))

    # 6. News supports direction X but market moving X' — divergence
    if news_perm == "allow" and news_bias in ("bullish", "bearish") and \
       market_direction in ("bullish", "bearish") and news_bias != market_direction:
        # bullish news but bearish market (or vice versa)
        items.append((f"news_{news_bias}_but_market_{market_direction}_divergent", "high"))

    # 7. News is allow but market moving very fast (chase risk)
    if pair_pct is not None and abs(pair_pct) > 0.002 and news_perm == "allow":
        # Rapid move + allow signal = chase risk; force wait
        items.append(("rapid_move_chase_risk_news_allow", "high"))

    # 8. Strong direction but no DXY confirmation
    if eur_pct is not None and abs(eur_pct) > 0.002 and dxy_dir == "flat":
        items.append(("eurusd_strong_move_dxy_flat_no_macro_support", "medium"))

    return ContradictionResult(items=items)


def severity_to_outcome(result: ContradictionResult) -> Tuple[str, str]:
    """Map worst severity to (permission_override, grade_cap).
    permission_override is one of: "block", "wait", "" (no override).
    grade_cap is one of: A+, A, B, C.
    """
    if result.critical: return ("block", "C")
    if result.high:     return ("wait", "C")
    if result.medium:   return ("",     "B")
    return ("", "A+")
