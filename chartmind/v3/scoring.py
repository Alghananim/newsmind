# -*- coding: utf-8 -*-
"""Score helpers — turn discrete labels into 0..1 scores."""
from __future__ import annotations


_STRUCT_SCORE = {
    "uptrend": 1.0, "downtrend": 1.0, "bos_up": 0.85, "bos_down": 0.85,
    "choch_up": 0.6, "choch_down": 0.6, "range": 0.4, "unclear": 0.2,
}

_TREND_QUALITY = {"smooth": 1.0, "jagged": 0.3, "exhausting": 0.2, "unclear": 0.4}

_CANDLE_QUALITY = {"strong": 1.0, "weak": 0.4, "late": 0.2, "n_a": 0.0}

_BREAKOUT_QUALITY = {"real": 1.0, "pending": 0.5, "weak": 0.3, "fake": 0.0,
                     "none": 0.5}

_RETEST_QUALITY = {"successful": 1.0, "failed": 0.0, "pending": 0.4,
                   "none": 0.5}

_ENTRY_QUALITY = {"excellent": 1.0, "good": 0.8, "marginal": 0.5,
                  "late": 0.2, "chase": 0.0, "no_setup": 0.3}

_VOL = {"low": 0.6, "normal": 1.0, "high": 0.5, "extreme": 0.1, "unclear": 0.4}
_DQ = {"good": 1.0, "partial": 0.7, "degraded": 0.4, "missing": 0.0, "unknown": 0.3}
_MTF = {"aligned": 1.0, "n_a": 0.7, "insufficient": 0.4, "conflicting": 0.0}


def structure_score(s: str) -> float:        return _STRUCT_SCORE.get(s, 0.2)
def trend_quality_score(q: str) -> float:    return _TREND_QUALITY.get(q, 0.4)
def candle_score(q: str) -> float:           return _CANDLE_QUALITY.get(q, 0.0)
def breakout_score(s: str) -> float:         return _BREAKOUT_QUALITY.get(s, 0.5)
def retest_score(s: str) -> float:           return _RETEST_QUALITY.get(s, 0.5)
def entry_quality_score(q: str) -> float:    return _ENTRY_QUALITY.get(q, 0.3)
def volatility_score(v: str) -> float:       return _VOL.get(v, 0.4)
def data_quality_score(s: str) -> float:     return _DQ.get(s, 0.3)
def timeframe_alignment_score(s: str) -> float: return _MTF.get(s, 0.4)


def speed_score(total_ms: float, target_ms: float = 50.0) -> float:
    """1.0 if <= target; degrades to 0 at 5× target."""
    if total_ms <= 0: return 0.0
    if total_ms <= target_ms: return 1.0
    if total_ms >= 5 * target_ms: return 0.0
    return round(1 - (total_ms - target_ms) / (4 * target_ms), 3)


def chart_intelligence_score(*, structure: float, trend_q: float,
                             candle: float, breakout: float, retest: float,
                             entry_q: float, vol: float, mtf: float,
                             traps_count: int, rr: float = 1.0) -> float:
    """Composite 0..1. Equal weight base + bonuses/penalties."""
    base = (structure + trend_q + candle + breakout + retest +
            entry_q + vol + mtf) / 8.0
    base -= 0.15 * traps_count
    if rr is not None and rr >= 1.5: base += 0.1
    elif rr is not None and rr < 0.8: base -= 0.2
    return round(max(0.0, min(1.0, base)), 3)
