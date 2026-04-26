# -*- coding: utf-8 -*-
"""GateMind scoring — turn check statuses into 0..1 scores."""
from __future__ import annotations


_ALIGN_SCORE = {"aligned": 1.0, "partial": 0.4, "conflicting": 0.0,
                "blocked_by_brain": 0.0, "missing": 0.0}

_RISK_SCORE = {"ok": 1.0, "rr_marginal": 0.6,
               "rr_too_low": 0.0, "stop_too_tight": 0.0, "stop_too_wide": 0.0,
               "missing": 0.0, "invalid": 0.0}

_EXEC_SCORE = {"ok": 1.0, "spread_too_wide": 0.0, "slippage_too_high": 0.0,
               "monitoring_pair_live_blocked": 0.0, "disabled_pair": 0.0,
               "broker_unsafe": 0.0, "unknown_pair": 0.0,
               "spread_unknown": 0.3, "slippage_unknown": 0.3}

_SESSION_SCORE = {"in_window": 1.0, "outside": 0.0, "dst_unknown": 0.0}

_DQ_SCORE = {"ok": 1.0, "data_stale": 0.0, "flat": 1.0,
             "position_already_open": 0.0, "pending_order_exists": 0.0,
             "after_3_losses_cooldown": 0.0, "in_cooldown": 0.0,
             "blocked_by_loss_limit": 0.0, "blocked_by_trade_limit": 0.0,
             "missing": 0.0}


def alignment_score(status: str) -> float:    return _ALIGN_SCORE.get(status, 0.2)
def risk_score(status: str) -> float:         return _RISK_SCORE.get(status, 0.3)
def execution_safety_score(status: str) -> float: return _EXEC_SCORE.get(status, 0.3)
def session_safety_score(status: str) -> float: return _SESSION_SCORE.get(status, 0.3)
def data_quality_score(status: str) -> float: return _DQ_SCORE.get(status, 0.3)


def speed_score(total_ms: float, target_ms: float = 5.0) -> float:
    """1.0 if total ≤ target_ms; degrades to 0 at 10× target."""
    if total_ms <= 0: return 0.0
    if total_ms <= target_ms: return 1.0
    if total_ms >= 10 * target_ms: return 0.0
    return round(1 - (total_ms - target_ms) / (9 * target_ms), 3)


def gate_intelligence_score(*, alignment: float, risk: float, execution: float,
                            session: float, data_quality: float,
                            contradictions: int,
                            confidence_summary: float = 0.5) -> float:
    """Composite 0..1: equal-weight base + contradictions penalty + confidence."""
    base = (alignment + risk + execution + session + data_quality) / 5.0
    base -= 0.15 * contradictions
    # blend with confidence (up to ±0.1)
    base += (confidence_summary - 0.5) * 0.2
    return round(max(0.0, min(1.0, base)), 3)
