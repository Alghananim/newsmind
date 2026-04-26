# -*- coding: utf-8 -*-
"""SmartNoteBook v3 — data models. Append-only journaling."""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, List


@dataclass
class MindOutputs:
    """Snapshot of all 4 brain verdicts at decision time."""
    # NewsMind
    news_grade: str = "C"
    news_perm: str = "wait"
    news_confidence: float = 0.0
    news_bias: str = "unclear"
    news_freshness: str = "unknown"
    news_impact_level: str = "unknown"
    news_source_type: str = "unknown"
    news_verified: bool = False
    news_market_bias: str = "neutral"
    news_reason: str = ""
    news_warnings: tuple = ()

    # MarketMind
    market_grade: str = "C"
    market_perm: str = "wait"
    market_confidence: float = 0.0
    market_regime: str = "unclear"
    market_direction: str = "unclear"
    market_dollar_bias: str = "unclear"
    market_risk_mode: str = "unclear"
    market_volatility: str = "unclear"
    market_liquidity: str = "unclear"
    market_spread: str = "unknown"
    market_reason: str = ""
    market_warnings: tuple = ()

    # ChartMind
    chart_grade: str = "C"
    chart_perm: str = "wait"
    chart_confidence: float = 0.0
    chart_structure: str = "unclear"
    chart_trend_direction: str = "unclear"
    chart_candle_context: str = "no_context"
    chart_breakout_status: str = "none"
    chart_retest_status: str = "none"
    chart_entry_quality: str = "no_setup"
    chart_fake_breakout: bool = False
    chart_late_entry: bool = False
    chart_stop_loss: Optional[float] = None
    chart_take_profit: Optional[float] = None
    chart_rr: Optional[float] = None
    chart_reason: str = ""
    chart_warnings: tuple = ()

    # GateMind
    gate_decision: str = "wait"
    gate_approved: bool = False
    gate_blocking: tuple = ()
    gate_warnings: tuple = ()
    gate_audit_id: str = ""
    gate_reason: str = ""


@dataclass
class AttributionResult:
    primary_success_factor: str = ""
    primary_failure_factor: str = ""
    responsible_mind: str = ""              # which brain bears responsibility
    supporting_minds: tuple = ()
    contradicting_minds: tuple = ()
    decision_quality: str = "unclear"       # good / bad / mixed / lucky / valid_loss


@dataclass
class TradeAuditEntry:
    trade_id: str
    audit_id: str
    pair: str
    system_mode: str = "paper"              # backtest / paper / live
    strategy_variant: str = ""

    direction: str = "none"                 # buy / sell / none
    entry_time: Optional[datetime] = None
    entry_price: Optional[float] = None
    position_size: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    expected_rr: Optional[float] = None

    spread_at_entry: Optional[float] = None
    slippage_estimate: Optional[float] = None
    actual_slippage: Optional[float] = None

    exit_time: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: str = ""

    pnl: float = 0.0
    pnl_pct: float = 0.0
    mfe: float = 0.0                        # max favorable excursion
    mae: float = 0.0                        # max adverse excursion

    hit_target: bool = False
    hit_stop: bool = False
    exit_by_time: bool = False
    exit_by_news: bool = False
    exit_by_protection: bool = False

    mind_outputs: Optional[MindOutputs] = None
    classification: str = ""                # logical_win/lucky_win/valid_loss/...
    attribution: Optional[AttributionResult] = None
    lesson: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("entry_time", "exit_time"):
            if d.get(k) and isinstance(d[k], datetime):
                d[k] = d[k].isoformat()
        return d


@dataclass
class DecisionEvent:
    """ALL decisions — including rejected/wait/block (not just executed trades)."""
    event_id: str
    audit_id: str
    timestamp: datetime
    event_type: str                         # trade / wait / block / bug
    pair: str
    system_mode: str = "paper"

    mind_outputs: Optional[MindOutputs] = None
    gate_decision: str = "wait"
    rejected_reason: str = ""
    blocking_reasons: tuple = ()
    warnings: tuple = ()

    lesson_recorded: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("timestamp") and isinstance(d["timestamp"], datetime):
            d["timestamp"] = d["timestamp"].isoformat()
        return d


@dataclass
class LessonLearned:
    lesson_id: str
    source_event_ids: tuple                 # supporting events
    pair: str
    pattern: str                            # description
    observed_count: int                     # # of instances
    recommendation: str
    requires_more_evidence: bool = True

    def to_dict(self): return asdict(self)


@dataclass
class BugDetected:
    bug_id: str
    affected_mind: str                      # news/market/chart/gate
    bug_type: str
    severity: str                           # low/medium/high/critical
    example_event_id: str
    impact_on_result: str
    detected_at: datetime
    fix_required: bool = True
    fixed: bool = False
    fix_commit_id: str = ""
    retest_required: bool = True

    def to_dict(self):
        d = asdict(self)
        if d.get("detected_at") and isinstance(d["detected_at"], datetime):
            d["detected_at"] = d["detected_at"].isoformat()
        return d


@dataclass
class DailySummary:
    date: str                                # YYYY-MM-DD
    pair: str
    n_opportunities: int = 0
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    n_blocked: int = 0
    n_waited: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    biggest_win: float = 0.0
    biggest_loss: float = 0.0
    daily_drawdown_pct: float = 0.0
    top_win_reason: str = ""
    top_loss_reason: str = ""
    best_decision_id: str = ""
    worst_decision_id: str = ""
    gate_strict_score: float = 0.0           # 0..1 (1=strict, 0=lenient)
    bugs_count: int = 0
    lesson_of_the_day: str = ""

    def to_dict(self): return asdict(self)


@dataclass
class WeeklySummary:
    week_start: str
    pair_stats: dict = field(default_factory=dict)    # {pair: stats}
    best_pair: str = ""
    worst_pair: str = ""
    best_session: str = ""
    worst_session: str = ""
    most_wrong_brain: str = ""
    most_right_brain: str = ""
    a_plus_better_than_a: bool = False
    a_better_than_b: bool = False
    b_stayed_wait: bool = False
    c_respected: bool = False
    grade_calibration_correct: bool = False
    top_lessons: tuple = ()
    top_recommendations: tuple = ()

    def to_dict(self): return asdict(self)
