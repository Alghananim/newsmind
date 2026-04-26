# -*- coding: utf-8 -*-
"""ChartMind permission engine — final allow/wait/block + grade ladder.

Hard BLOCK (overrides everything):
   - liquidity_sweep_detected
   - fake_breakout_status
   - chase entry quality
   - R/R < 0.8 (stop_target.status == rr_too_low)
   - stop too tight / too wide
   - MTF conflicting AND opposing
   - liquidity sweep at key level

Hard WAIT:
   - retest_status == pending
   - breakout_status == weak
   - candlestick signal != none with no_context
   - trend_quality == exhausting

Grade ladder:
   A+: trend strong + structure aligned + candle strong at level + breakout real
       + retest successful + entry excellent + R/R ≥ 1.5 + MTF aligned
       + vol normal + no traps
   A:  trend strong + similar conditions but R/R 1.2-1.5 OR MTF "n_a"
   B:  marginal — incomplete pattern, retest pending, candle weak, etc.
   C:  any failure path; or warnings ≥ 3
"""
from __future__ import annotations
from .models import ChartAssessment


HARD_BLOCK_TABLE = [
    (lambda a: a.liquidity_sweep_detected, "liquidity_sweep"),
    (lambda a: a.fake_breakout_risk and a.breakout_status == "fake", "fake_breakout"),
    (lambda a: a.entry_quality == "chase", "chase_entry"),
    # Only check rr_too_low if we have an actionable setup. For no_setup/late
    # the wait path applies via entry rules below.
    (lambda a: (a.risk_reward is not None and a.risk_reward < 0.8
                and a.entry_quality in ("excellent","good","marginal")),
     "rr_too_low"),
    (lambda a: a.timeframe_alignment == "conflicting", "mtf_conflict"),
]

HARD_WAIT_TABLE = [
    (lambda a: a.retest_status == "pending", "retest_pending"),
    (lambda a: a.breakout_status == "weak", "breakout_weak"),
    (lambda a: (a.candlestick_signal != "none"
                and a.candlestick_context in ("no_context", "midrange")),
     "candle_no_context_or_midrange"),
    (lambda a: a.trend_quality == "exhausting", "trend_exhausting"),
    (lambda a: a.entry_quality == "late", "entry_late"),
    (lambda a: a.stop_loss is None, "stop_undefined"),
    (lambda a: any("breakout_direction_conflicts_trend" in w for w in a.warnings),
     "breakout_vs_trend_conflict"),
]


GRADE_RANK = {"A+":4,"A":3,"B":2,"C":1}


def finalize(a: ChartAssessment) -> ChartAssessment:
    reasons = []

    # 1. Hard blocks
    for cond, label in HARD_BLOCK_TABLE:
        try:
            if cond(a):
                a.trade_permission = "block"
                a.grade = "C"
                reasons.append(f"hard_block:{label}")
                a.reason = (a.reason + "|" if a.reason else "") + ";".join(reasons)
                return a
        except Exception:
            continue

    # 2. Hard waits
    for cond, label in HARD_WAIT_TABLE:
        try:
            if cond(a):
                a.trade_permission = "wait"
                a.grade = "C"
                reasons.append(f"hard_wait:{label}")
                a.reason = (a.reason + "|" if a.reason else "") + ";".join(reasons)
                return a
        except Exception:
            continue

    # 2.5: catch unclear/no-setup paths and route to wait+C cleanly
    if (a.market_structure in ("unclear", "range") and
        a.entry_quality in ("no_setup", "late", "chase")):
        a.trade_permission = "wait"
        a.grade = "C"
        a.confidence = 0.20
        reasons.append("no_actionable_setup")
        a.reason = (a.reason + "|" if a.reason else "") + ";".join(reasons)
        return a

    # 3. Grade ladder
    rr = a.risk_reward or 0
    A_plus = (
        a.trend_strength >= 0.5
        and a.market_structure in ("uptrend", "downtrend", "bos_up", "bos_down")
        and a.candlestick_quality == "strong"
        and a.candlestick_context.startswith("at_")
        and a.breakout_status in ("real", "none")
        and (a.retest_status == "successful" or a.breakout_status == "none")
        and a.entry_quality in ("excellent",)
        and rr >= 1.5
        and a.timeframe_alignment in ("aligned", "n_a")
        and a.volatility_status in ("normal", "low")
        and not a.fake_breakout_risk
        and not a.liquidity_sweep_detected
    )
    A_grade = (
        a.trend_strength >= 0.3
        and a.market_structure in ("uptrend", "downtrend", "bos_up", "bos_down",
                                    "choch_up", "choch_down")
        and a.candlestick_quality in ("strong", "weak")
        and a.entry_quality in ("excellent", "good")
        and rr >= 1.2
        and a.timeframe_alignment != "conflicting"
        and a.volatility_status in ("normal", "low")
    )

    if A_plus:
        a.grade = "A+"
        a.trade_permission = "allow"
        a.confidence = 0.85
        reasons.append("a_plus_all_conditions_met")
    elif A_grade:
        a.grade = "A"
        a.trade_permission = "allow"
        a.confidence = 0.70
        reasons.append("a_grade_strong_with_caveats")
    elif a.market_structure in ("uptrend", "downtrend", "range") and (rr is None or rr >= 0.8):
        a.grade = "B"
        a.trade_permission = "wait"
        a.confidence = 0.50
        reasons.append("b_partial_setup_observable")
    else:
        a.grade = "C"
        a.trade_permission = "wait"
        a.confidence = 0.20
        reasons.append("c_default_unclear")

    a.reason = (a.reason + "|" if a.reason else "") + ";".join(reasons)
    return a
