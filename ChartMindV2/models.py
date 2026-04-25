# -*- coding: utf-8 -*-
"""ChartMindV2 dataclasses — pure structures, no logic.

Designed to be JSON-serialisable for journal/audit trail.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional


# ----------------------------------------------------------------------
# Sub-readings (one per analysis pillar).
# ----------------------------------------------------------------------
@dataclass
class TrendReading:
    """Multi-timeframe trend alignment."""
    h4_direction: str          # "up" | "down" | "flat"
    h1_direction: str
    m15_direction: str
    aligned_direction: str     # "up" | "down" | "mixed"
    alignment_score: float     # 0..1 (1.0 = perfect alignment across H4+H1+M15)
    h4_adx: float              # Wilder's ADX on H4
    h1_adx: float
    m15_adx: float


@dataclass
class StructureLevel:
    """One support or resistance level."""
    price: float
    label: str                 # "swing_low" | "swing_high" | "prior_day_high" | etc.
    distance_pips: float       # signed: positive = above current price
    strength: float            # 0..1 (multiple touches, recency)


@dataclass
class StructureReading:
    """Nearby S/R levels and current price context."""
    nearest_support: Optional[StructureLevel]
    nearest_resistance: Optional[StructureLevel]
    prior_day_high: float
    prior_day_low: float
    session_open: float
    in_value_area: bool        # current price between PDH and PDL
    levels: list


@dataclass
class CandleReading:
    """Recent candle pattern at structure."""
    pattern: str               # "bull_engulfing" | "bear_pin" | "inside_bar" | "none"
    direction: str             # "long" | "short" | "neutral"
    at_structure: bool         # was the pattern formed AT a S/R level?
    structure_label: str       # which level (e.g., "prior_day_low")
    strength: float            # 0..1 (body size, wick ratio)
    bar_index: int             # which recent bar (0 = current, 1 = previous)


@dataclass
class MomentumReading:
    """RSI + MACD diagnostics."""
    rsi: float
    rsi_state: str             # "oversold" | "overbought" | "neutral"
    rsi_divergence: str        # "bullish" | "bearish" | "none"
    macd_signal: str           # "bull_cross" | "bear_cross" | "none"
    macd_hist: float           # last histogram value
    momentum_direction: str    # "long" | "short" | "neutral"


@dataclass
class RegimeReading:
    """Market regime (delegates to Backtest.regime)."""
    label: str                 # "TRENDING_UP" | "TRENDING_DOWN" | "RANGING" | "VOLATILE" | "QUIET"
    adx: float
    atr_pips: float
    regime_confidence: float


# ----------------------------------------------------------------------
# Plan (the output that GateMind consumes).
# ----------------------------------------------------------------------
@dataclass
class TradePlan:
    """Output to GateMind. Compatible with Backtest/runner.py contract.

    Mandatory fields used by the runner:
        direction, entry_price, stop_price, target_price, rr_ratio,
        confidence, setup_type, time_budget_bars, is_actionable,
        rationale.

    V2 additions (read by SmartNoteBook journal):
        grade, confluence_score, risks, timing_ok, plan_id.
    """
    # Compatibility surface (matches v1 contract)
    setup_type: str
    direction: str             # "long" | "short"
    entry_price: float
    stop_price: float
    target_price: float
    rr_ratio: float
    time_budget_bars: int
    confidence: float
    rationale: str
    is_actionable: bool = True
    reason_if_not: str = ""

    # V2 enrichment (consumed by GateMind + journal)
    grade: str = "C"           # "A+" | "A" | "B" | "C"
    confluence_score: float = 0.0     # 0..6 (factor count)
    confluence_breakdown: dict = field(default_factory=dict)
    risks: list = field(default_factory=list)        # textual risk flags
    timing_ok: bool = True
    plan_id: str = ""

    # Diagnostic readings preserved for audit
    trend: Optional[TrendReading] = None
    structure: Optional[StructureReading] = None
    candle: Optional[CandleReading] = None
    momentum: Optional[MomentumReading] = None
    regime: Optional[RegimeReading] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ----------------------------------------------------------------------
# Top-level analysis bundle (matches v1 Analysis surface for runner).
# ----------------------------------------------------------------------
@dataclass
class AnalysisV2:
    """Bundle returned by ChartMindV2.analyze()."""
    plan: Optional[TradePlan] = None
    trend: Optional[TrendReading] = None
    structure: Optional[StructureReading] = None
    candle: Optional[CandleReading] = None
    momentum: Optional[MomentumReading] = None
    regime: Optional[RegimeReading] = None

    @property
    def actionable(self) -> bool:
        return bool(self.plan and self.plan.is_actionable)

    @property
    def directive(self) -> str:
        if not self.plan:
            return "no_setup"
        if not self.plan.is_actionable:
            return "wait"
        return "trade"
