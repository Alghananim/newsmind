# -*- coding: utf-8 -*-
"""Risk sentiment classifier — risk_on / risk_off / unclear.

Inputs (any subset):
    XAU/USD bars  — gold rising = risk_off (or USD weak)
    SPX500 bars   — equities up = risk_on
    USD/JPY bars  — usually USD/JPY up = risk_on (yen weak)
                    BUT if SPX falling AND USD/JPY falling = clear risk_off
    NewsMind risk_mode — direct override if news is high-conviction

Logic:
    Score risk-on: equities up + USD/JPY up + gold flat/down
    Score risk-off: equities down + gold up + USD/JPY down (or yen safe-haven)
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from .models import Bar


@dataclass
class RiskSentimentResult:
    risk_mode: str = "unclear"     # risk_on / risk_off / unclear
    confidence: float = 0.0        # 0..1
    components: dict = field(default_factory=dict)
    rationale: tuple = ()


def _pct_change(bars: List[Bar], window: int) -> Optional[float]:
    if not bars or len(bars) < window + 1: return None
    old = bars[-window-1].close
    if old == 0: return None
    return (bars[-1].close - old) / old


def assess(*, bars_xau: Optional[List[Bar]] = None,
           bars_spx: Optional[List[Bar]] = None,
           bars_usdjpy: Optional[List[Bar]] = None,
           news_risk_mode: str = "unclear",
           window: int = 20) -> RiskSentimentResult:
    rationale = []
    comp = {}

    gold_chg = _pct_change(bars_xau, window) if bars_xau else None
    spx_chg = _pct_change(bars_spx, window) if bars_spx else None
    jpy_chg = _pct_change(bars_usdjpy, window) if bars_usdjpy else None

    if gold_chg is not None:
        comp["gold_pct"] = round(gold_chg * 100, 3)
    if spx_chg is not None:
        comp["spx_pct"] = round(spx_chg * 100, 3)
    if jpy_chg is not None:
        comp["usdjpy_pct"] = round(jpy_chg * 100, 3)

    # If NewsMind is very confident, defer
    if news_risk_mode in ("risk_on", "risk_off"):
        rationale.append(f"news_override:{news_risk_mode}")
        return RiskSentimentResult(
            risk_mode=news_risk_mode,
            confidence=0.85,
            components=comp,
            rationale=tuple(rationale),
        )

    # Score: -1 = risk_off cue, +1 = risk_on cue
    score = 0
    n_signals = 0
    if spx_chg is not None:
        n_signals += 1
        if spx_chg > 0.005: score += 1; rationale.append("spx_up")
        elif spx_chg < -0.005: score -= 1; rationale.append("spx_down")
    if gold_chg is not None:
        n_signals += 1
        if gold_chg > 0.005: score -= 1; rationale.append("gold_up")
        elif gold_chg < -0.005: score += 1; rationale.append("gold_down")
    if jpy_chg is not None:
        n_signals += 1
        if jpy_chg > 0.003: score += 1; rationale.append("usdjpy_up")  # yen weak
        elif jpy_chg < -0.003: score -= 1; rationale.append("usdjpy_down")  # yen strong

    if n_signals == 0:
        return RiskSentimentResult(rationale=("no_inputs",))

    # Need at least 2/3 signals agreeing for confident call
    confidence = abs(score) / max(n_signals, 1)
    if score >= 2 and confidence >= 0.5:
        mode = "risk_on"
    elif score <= -2 and confidence >= 0.5:
        mode = "risk_off"
    else:
        mode = "unclear"

    return RiskSentimentResult(
        risk_mode=mode,
        confidence=round(confidence, 2),
        components=comp,
        rationale=tuple(rationale),
    )
