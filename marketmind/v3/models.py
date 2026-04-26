# -*- coding: utf-8 -*-
"""MarketMind v3 — data models (V4-extended with speed + intelligence fields)."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Literal


TradePermission = Literal["allow", "wait", "block"]
MarketRegime = Literal[
    "trend", "range", "choppy", "breakout", "fake_breakout",
    "reversal", "news_driven", "low_liquidity", "high_volatility",
    "dangerous", "unclear",
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
class MarketAssessment:
    pair: str
    timestamp_utc: datetime
    session: str = "off"
    market_regime: MarketRegime = "unclear"
    direction: str = "unclear"
    trend_strength: float = 0.0

    dollar_bias: str = "unclear"
    counter_currency_bias: str = "unclear"
    yield_signal: str = "unavailable"
    gold_signal: str = "unavailable"
    risk_mode: str = "unclear"

    volatility_level: str = "unclear"
    liquidity_condition: str = "unclear"
    spread_condition: str = "unclear"

    correlation_status: str = "unavailable"
    news_alignment: str = "no_news"

    trade_environment: str = "wait"
    grade: str = "C"
    confidence: float = 0.0
    trade_permission: TradePermission = "wait"
    reason: str = ""
    warnings: tuple = ()
    data_quality_status: str = "unknown"

    # ---- V4 fields: speed + intelligence ----
    decision_latency_ms: float = 0.0
    data_latency_ms: float = 0.0
    sources_used: tuple = ()                   # ("EUR/USD","USD/JPY","XAU","SPX","NewsMind",...)
    stale_sources: tuple = ()                  # sources whose latency exceeded threshold
    market_intelligence_score: float = 0.0     # 0..1 composite
    speed_score: float = 0.0                   # 0..1 (1.0 if total_ms <= 50ms)
    data_quality_score: float = 0.0            # 0..1
    trend_score: float = 0.0                   # 0..1
    volatility_score: float = 0.0              # 0..1
    liquidity_score: float = 0.0               # 0..1
    cross_market_confirmation: str = "none"    # strong/moderate/weak/none
    contradictions_detected: tuple = ()        # list of contradiction labels
    cache_stats: dict = field(default_factory=dict)
    bottleneck_stage: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("timestamp_utc") and isinstance(d["timestamp_utc"], datetime):
            d["timestamp_utc"] = d["timestamp_utc"].isoformat()
        return d
