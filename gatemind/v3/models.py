# -*- coding: utf-8 -*-
"""GateMind v3 / V4 — data models with speed + intelligence fields."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Literal


FinalDecision = Literal["enter", "wait", "block"]
Direction = Literal["buy", "sell", "none"]


@dataclass
class BrainSummary:
    name: str
    permission: str = "wait"
    grade: str = "C"
    confidence: float = 0.0
    direction: str = "unclear"
    reason: str = ""
    warnings: tuple = ()


@dataclass
class SystemState:
    pair: str
    broker_mode: str = "paper"
    live_enabled: bool = False
    spread_pips: float = 0.5
    max_spread_pips: float = 2.0
    expected_slippage_pips: float = 0.5
    max_slippage_pips: float = 2.0
    open_positions: tuple = ()
    pending_orders: tuple = ()
    daily_loss_pct: float = 0.0
    daily_loss_limit_pct: float = 5.0
    trades_today: int = 0
    daily_trade_limit: int = 10
    consecutive_losses: int = 0
    cooldown_until_utc: Optional[datetime] = None
    pair_status: str = "production"
    data_latency_ms: float = 0.0
    max_data_latency_ms: float = 1000.0


@dataclass
class GateDecision:
    timestamp_utc: datetime
    pair: str
    audit_id: str = ""

    final_decision: FinalDecision = "wait"
    direction: Direction = "none"
    approved: bool = False

    reason: str = ""
    blocking_reasons: tuple = ()
    warnings: tuple = ()

    grades_received: dict = field(default_factory=dict)
    permissions_received: dict = field(default_factory=dict)
    confidences_received: dict = field(default_factory=dict)
    confidence_summary: float = 0.0

    alignment_status: str = "missing"
    risk_check_status: str = "missing"
    session_check_status: str = "outside"
    news_check_status: str = "wait"
    spread_check_status: str = "unknown"
    slippage_check_status: str = "unknown"
    execution_check_status: str = "unknown"
    daily_limits_status: str = "ok"
    position_state_status: str = "flat"

    broker_mode: str = "paper"
    live_enabled: bool = False

    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward: Optional[float] = None
    position_size: Optional[float] = None

    # ---- V4: speed + intelligence ----
    total_gate_latency_ms: float = 0.0
    input_parse_latency_ms: float = 0.0
    alignment_check_latency_ms: float = 0.0
    risk_check_latency_ms: float = 0.0
    session_check_latency_ms: float = 0.0
    spread_check_latency_ms: float = 0.0
    execution_check_latency_ms: float = 0.0
    daily_limits_check_latency_ms: float = 0.0
    final_decision_latency_ms: float = 0.0

    gate_intelligence_score: float = 0.0
    gate_speed_score: float = 0.0
    alignment_score: float = 0.0
    risk_score: float = 0.0
    execution_safety_score: float = 0.0
    session_safety_score: float = 0.0
    data_quality_score: float = 0.0

    contradictions_detected: tuple = ()
    bottleneck_stage: str = ""
    stages_breakdown: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.audit_id:
            self.audit_id = str(uuid.uuid4())

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("timestamp_utc") and isinstance(d["timestamp_utc"], datetime):
            d["timestamp_utc"] = d["timestamp_utc"].isoformat()
        return d
