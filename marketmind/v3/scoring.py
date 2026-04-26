# -*- coding: utf-8 -*-
"""Score helpers — turn discrete labels into 0..1 scores.

Output scores are used by the assessment to give an at-a-glance numeric
quality measure for the operator + downstream consumers (GateMind).
"""
from __future__ import annotations


_REGIME_TREND_SCORE = {
    "trend": 1.0, "breakout": 0.85, "reversal": 0.5,
    "range": 0.3, "fake_breakout": 0.1, "choppy": 0.05,
    "high_volatility": 0.2, "dangerous": 0.0, "news_driven": 0.4,
    "low_liquidity": 0.2, "unclear": 0.2,
}

_VOL_SCORE = {"low": 0.6, "normal": 1.0, "high": 0.5, "extreme": 0.1, "unclear": 0.4}
_LIQ_SCORE = {"good": 1.0, "thin": 0.5, "poor": 0.2, "unclear": 0.4}
_SPREAD_SCORE = {"tight": 1.0, "normal": 0.85, "wide": 0.4, "dangerous": 0.0, "unclear": 0.4}
_DQ_SCORE = {"good": 1.0, "partial": 0.7, "degraded": 0.4, "missing": 0.0, "unknown": 0.3}


def trend_score(regime: str, strength: float) -> float:
    base = _REGIME_TREND_SCORE.get(regime, 0.2)
    return round(min(1.0, base * (0.5 + strength)), 3)


def volatility_score(level: str) -> float:
    return _VOL_SCORE.get(level, 0.4)


def liquidity_score(label: str) -> float:
    return _LIQ_SCORE.get(label, 0.4)


def spread_score(label: str) -> float:
    return _SPREAD_SCORE.get(label, 0.4)


def data_quality_score(status: str) -> float:
    return _DQ_SCORE.get(status, 0.3)


def speed_score(total_ms: float, target_ms: float = 50.0) -> float:
    """1.0 if at or under target_ms; degrades linearly to 0 at 5× target."""
    if total_ms <= 0: return 0.0
    if total_ms <= target_ms: return 1.0
    if total_ms >= 5 * target_ms: return 0.0
    return round(1 - (total_ms - target_ms) / (4 * target_ms), 3)


def market_intelligence_score(*, trend: float, vol: float, liq: float,
                              spread: float, dq: float,
                              contradictions: int,
                              correlation_status: str,
                              news_aligned: bool) -> float:
    """Composite 0..1: equal weights, contradictions and broken correlation
    each subtract significantly."""
    base = (trend + vol + liq + spread + dq) / 5.0
    base -= 0.15 * contradictions
    if correlation_status == "broken": base -= 0.15
    if news_aligned: base += 0.1
    return round(max(0.0, min(1.0, base)), 3)


def cross_market_confirmation(*, dxy_dir: str, market_direction: str,
                              risk_mode: str, news_aligned: bool,
                              corr_status: str) -> str:
    """One-word verdict on whether the cross-market picture supports the trade."""
    pts = 0
    if dxy_dir != "flat" and market_direction in ("bullish","bearish"):
        # DXY direction consistent with market_direction (for EUR/USD: dxy_up = market_bearish)
        pts += 1
    if news_aligned: pts += 1
    if corr_status == "normal": pts += 1
    if risk_mode in ("risk_on","risk_off"): pts += 1
    if pts >= 3: return "strong"
    if pts == 2: return "moderate"
    if pts == 1: return "weak"
    return "none"
