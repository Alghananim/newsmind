# -*- coding: utf-8 -*-
"""Decision engine — synthesizes all checks into final enter/wait/block.

Strict order:
   1. Hard BLOCKS — any one fires → block
   2. Hard WAITS — any one fires → wait
   3. ENTER conditions — ALL must pass
   4. Default: wait

No silent allow. No bypass. B grade always = wait. C grade always = block.
"""
from __future__ import annotations
from typing import Optional
from .models import GateDecision, BrainSummary


GRADE_RANK = {"A+": 4, "A": 3, "B": 2, "C": 1, "": 0}


def synthesize(decision: GateDecision,
               *, news: Optional[BrainSummary],
               market: Optional[BrainSummary],
               chart: Optional[BrainSummary],
               alignment: dict, risk: dict, session: dict, news_gate: dict,
               execution: dict, state: dict,
               min_confidence: float = 0.6) -> GateDecision:
    """Mutates and returns the decision."""
    blocking = []

    # ---------------------------------------------------------------- 1) HARD BLOCKS
    if news is None or market is None or chart is None:
        blocking.append("missing_brain_input")
    else:
        # Any brain block
        for b in (news, market, chart):
            if b.permission == "block":
                blocking.append(f"{b.name}_block:{b.reason[:60]}")
            if b.grade == "C":
                blocking.append(f"{b.name}_grade_C")

    if alignment["status"] == "conflicting":
        blocking.append(f"alignment_conflicting:{alignment.get('details','')}")
    if alignment["status"] == "blocked_by_brain":
        blocking.append(f"alignment_blocked:{alignment.get('details','')}")

    if risk["status"] in ("missing", "invalid", "stop_too_tight",
                          "stop_too_wide", "rr_too_low"):
        blocking.append(f"risk_{risk['status']}:{risk.get('details','')}")

    if session["status"] == "outside":
        blocking.append(f"session_outside:{session.get('details','')}")
    if session["status"] == "dst_unknown":
        blocking.append(f"session_dst_unknown")

    if news_gate["status"] == "block":
        blocking.append(f"news_gate_block:{news_gate.get('details','')}")

    if execution["status"] in ("disabled_pair", "monitoring_pair_live_blocked",
                               "broker_unsafe", "spread_too_wide",
                               "slippage_too_high", "unknown_pair"):
        blocking.append(f"execution_{execution['status']}:{execution.get('details','')}")

    if state["position_state"] in ("blocked_by_loss_limit",
                                    "blocked_by_trade_limit",
                                    "in_cooldown",
                                    "after_3_losses_cooldown",
                                    "position_already_open",
                                    "pending_order_exists",
                                    "data_stale"):
        blocking.append(f"state_{state['position_state']}:{state.get('details','')}")

    if state["daily_limits"] == "at_loss_limit":
        blocking.append("daily_loss_limit_hit")
    if state["daily_limits"] == "at_trade_limit":
        blocking.append("daily_trade_limit_hit")

    # ---------------------------------------------------------------- HARD WAITS
    waiting = []
    if news is not None and news.grade == "B": waiting.append("news_grade_B")
    if market is not None and market.grade == "B": waiting.append("market_grade_B")
    if chart is not None and chart.grade == "B": waiting.append("chart_grade_B")

    if news is not None and news.permission == "wait": waiting.append("news_wait")
    if market is not None and market.permission == "wait": waiting.append("market_wait")
    if chart is not None and chart.permission == "wait": waiting.append("chart_wait")

    if alignment["status"] == "partial": waiting.append("alignment_partial")
    if risk["status"] == "rr_marginal": waiting.append("rr_marginal")
    if news_gate["status"] == "wait": waiting.append("news_gate_wait")
    if execution["status"] in ("spread_unknown", "slippage_unknown"):
        waiting.append(f"execution_{execution['status']}")

    # Confidence check
    if news and market and chart:
        confs = [news.confidence, market.confidence, chart.confidence]
        avg_conf = sum(confs) / 3.0
        decision.confidence_summary = round(avg_conf, 3)
        if avg_conf < min_confidence:
            waiting.append(f"low_confidence:{avg_conf:.2f}<{min_confidence}")

    decision.blocking_reasons = tuple(blocking)
    decision.warnings = tuple(waiting)

    # ---------------------------------------------------------------- DECISION
    if blocking:
        decision.final_decision = "block"
        decision.direction = "none"
        decision.approved = False
        decision.reason = "BLOCK: " + " | ".join(blocking[:3])
        return decision

    # No hard blocks. Check if ALL enter conditions met:
    #   - all 3 brains permission == allow
    #   - all 3 grades in {A, A+}
    #   - alignment == aligned
    #   - all check statuses ok
    if news and market and chart:
        all_allow = all(b.permission == "allow" for b in (news, market, chart))
        all_top_grades = all(b.grade in ("A", "A+") for b in (news, market, chart))
    else:
        all_allow = False
        all_top_grades = False

    enter_conditions = (
        all_allow
        and all_top_grades
        and alignment["status"] == "aligned"
        and risk["status"] == "ok"
        and session["status"] == "in_window"
        and news_gate["status"] == "ok"
        and execution["status"] == "ok"
        and state["position_state"] == "flat"
        and state["daily_limits"] == "ok"
        and decision.confidence_summary >= min_confidence
        and not waiting
    )

    if enter_conditions:
        decision.final_decision = "enter"
        decision.direction = alignment["direction"]
        decision.approved = True
        decision.reason = (f"ENTER: aligned/{alignment['direction']} "
                          f"all_grades_top conf={decision.confidence_summary} rr={risk['rr']}")
        return decision

    # Otherwise wait
    decision.final_decision = "wait"
    decision.direction = "none"
    decision.approved = False
    if waiting:
        decision.reason = "WAIT: " + " | ".join(waiting[:3])
    else:
        decision.reason = "WAIT: enter_conditions_not_all_met"
    return decision
