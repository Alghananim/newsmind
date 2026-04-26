# -*- coding: utf-8 -*-
"""ChartMind v3 / V4 — data models with speed + intelligence fields."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Literal


TradePermission = Literal["allow", "wait", "block"]
MarketStructure = Literal[
    "uptrend", "downtrend", "range",
    "bos_up", "bos_down", "choch_up", "choch_down",
    "unclear",
]


@dataclass
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    spread_pips: float = 0.5


@dataclass
class Level:
    price: float
    strength: int = 1
    role: str = "support"


@dataclass
class ChartAssessment:
    pair: str
    timestamp_utc: datetime
    timeframes_used: tuple = ()

    # Structure
    market_structure: MarketStructure = "unclear"
    trend_direction: str = "unclear"
    trend_strength: float = 0.0
    trend_quality: str = "unclear"

    # Levels
    support_levels: tuple = ()
    resistance_levels: tuple = ()
    nearest_key_level: Optional[float] = None
    nearest_key_distance_atr: float = 0.0
    nearest_key_role: str = "none"

    # Candle
    candlestick_signal: str = "none"
    candlestick_context: str = "no_context"
    candlestick_quality: str = "n_a"

    # Breakout / pullback
    breakout_status: str = "none"
    retest_status: str = "none"
    pullback_quality: str = "n_a"

    # Entry
    entry_quality: str = "no_setup"
    entry_price_zone: tuple = ()
    late_entry_risk: bool = False

    # Risk
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    stop_logic: str = "n_a"
    target_logic: str = "n_a"

    # Volatility
    volatility_status: str = "unclear"
    atr_status: str = "unclear"

    # Traps
    fake_breakout_risk: bool = False
    liquidity_sweep_detected: bool = False

    # MTF
    timeframe_alignment: str = "n_a"

    # Final
    grade: str = "C"
    confidence: float = 0.0
    trade_permission: TradePermission = "wait"
    reason: str = ""
    warnings: tuple = ()

    # ---- V4 fields: speed + intelligence ----
    chart_analysis_latency_ms: float = 0.0          # full assess()
    data_load_latency_ms: float = 0.0               # bar copy / sanity
    feature_calc_latency_ms: float = 0.0            # ATR/ADX/etc.
    structure_analysis_latency_ms: float = 0.0
    candlestick_analysis_latency_ms: float = 0.0
    support_resistance_latency_ms: float = 0.0
    breakout_detection_latency_ms: float = 0.0
    risk_reward_calc_latency_ms: float = 0.0
    chart_intelligence_score: float = 0.0
    speed_score: float = 0.0
    data_quality_score: float = 0.0
    timeframe_alignment_score: float = 0.0
    entry_quality_score: float = 0.0
    bottleneck_stage: str = ""
    cache_stats: dict = field(default_factory=dict)
    stages_breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("timestamp_utc") and isinstance(d["timestamp_utc"], datetime):
            d["timestamp_utc"] = d["timestamp_utc"].isoformat()
        return d
