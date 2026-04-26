# -*- coding: utf-8 -*-
"""Final allow/wait/block + grade ladder for MarketMind.

Hard blocks (override grade):
    - NewsMind block inherited
    - data_quality_status == missing
    - spread == dangerous
    - volatility == extreme
    - regime == dangerous

Grade ladder:
    A+ : trend + strong + good liquidity + tight spread + normal volatility
         + correlation normal + news aligned (or no_news) + data_quality good
    A  : trend/breakout + good conditions + no critical warnings
    B  : range OR news=wait OR liquidity=thin
    C  : choppy / fake_breakout / dangerous / extreme / data degraded / news block

Permission ladder:
    A+ / A  -> allow
    B       -> wait
    C       -> block (or wait if non-critical)
"""
from __future__ import annotations
from .models import MarketAssessment


# Hard-block conditions and the resulting grade/permission
# Two-tier: hard_BLOCK (truly unsafe to even monitor) and hard_WAIT (block trades but allow observation)
HARD_BLOCK_TABLE = [
    (lambda a: a.spread_condition == "dangerous", "spread_dangerous"),
    (lambda a: a.market_regime == "dangerous", "regime_dangerous"),
    (lambda a: a.news_alignment == "blocked_by_news", "news_blocked"),
]
HARD_WAIT_TABLE = [
    (lambda a: a.data_quality_status == "missing", "data_missing"),
    (lambda a: a.volatility_level == "extreme", "volatility_extreme"),
    (lambda a: any("risk_off_but_usdjpy_up" in w for w in a.warnings), "risk_off_jpy_dangerous"),
    (lambda a: any("dxy_up_but_eurusd_up" in w for w in a.warnings), "dxy_eurusd_break"),
    (lambda a: any("inconsistent_usd" in w for w in a.warnings), "coincident_usd_pairs"),
    (lambda a: any("dxy_down_but_eurusd_down" in w for w in a.warnings), "dxy_eurusd_break"),
    (lambda a: any("usdjpy_spike" in w for w in a.warnings), "usdjpy_spike_intervention"),
]


def finalize(a: MarketAssessment, news_grade_cap: str = "A+") -> MarketAssessment:
    reasons = []

    # 1. Hard blocks
    for cond, label in HARD_BLOCK_TABLE:
        try:
            if cond(a):
                a.trade_permission = "block"
                a.grade = "C"
                a.trade_environment = "avoid"
                reasons.append(f"hard_block:{label}")
                a.reason = (a.reason + "|" if a.reason else "") + ";".join(reasons)
                return a
        except Exception:
            continue

    # 1b. Hard waits — block trades but stay in observe mode
    for cond, label in HARD_WAIT_TABLE:
        try:
            if cond(a):
                a.trade_permission = "wait"
                a.grade = "C"
                a.trade_environment = "wait"
                reasons.append(f"hard_wait:{label}")
                a.reason = (a.reason + "|" if a.reason else "") + ";".join(reasons)
                return a
        except Exception:
            continue

    # 2. Critical warnings → wait
    # SEVERE-CRITICAL: any single one of these forces grade=C
    severe_critical = [
        a.market_regime in ("choppy", "fake_breakout"),
        a.correlation_status == "broken",
        any("many_stale_sources" in w for w in a.warnings),
        any("future_dated_bars" in w for w in a.warnings),
        a.market_regime == "high_volatility",
    ]
    # SOFT-CRITICAL: each contributes; 2+ ⇒ C, 1 ⇒ B
    soft_critical = [
        a.spread_condition == "wide",
        a.data_quality_status == "degraded",
        a.volatility_level == "high",
        a.news_alignment == "divergent",
        any("dxy_coverage_" in w for w in a.warnings),
    ]
    if any(severe_critical) or any(soft_critical):
        a.trade_permission = "wait"
        if any(severe_critical) or sum(soft_critical) >= 2:
            a.grade = "C"
        else:
            a.grade = "B"
        a.trade_environment = "wait"
        reasons.append(f"critical_warnings:severe={sum(severe_critical)},soft={sum(soft_critical)}")
        a.reason = (a.reason + "|" if a.reason else "") + ";".join(reasons)
        return a

    # 3. Grade ladder for allow
    A_plus_conditions = (
        a.market_regime == "trend"
        and a.trend_strength >= 0.5
        and a.volatility_level == "normal"
        and a.liquidity_condition == "good"
        and a.spread_condition in ("tight", "normal")
        and a.correlation_status == "normal"
        and a.news_alignment in ("aligned", "no_news")
        and a.data_quality_status == "good"
    )
    A_conditions = (
        a.market_regime in ("trend", "breakout")
        and a.trend_strength >= 0.3
        and a.volatility_level in ("low", "normal")
        and a.liquidity_condition in ("good", "thin")
        and a.spread_condition in ("tight", "normal")
        and a.data_quality_status in ("good", "partial")
        and a.news_alignment != "divergent"
    )

    if A_plus_conditions:
        a.grade = "A+"
        a.trade_permission = "allow"
        a.trade_environment = "tradable"
        reasons.append("a_plus_all_conditions_met")
    elif A_conditions:
        a.grade = "A"
        a.trade_permission = "allow"
        a.trade_environment = "tradable"
        reasons.append("a_grade_strong_with_caveats")
    elif a.market_regime == "range":
        a.grade = "B"
        a.trade_permission = "wait"
        a.trade_environment = "wait"
        reasons.append("b_range_no_clear_edge")
    elif a.news_alignment == "news_caution":
        a.grade = "B"
        a.trade_permission = "wait"
        a.trade_environment = "wait"
        reasons.append("b_news_caution")
    else:
        a.grade = "C"
        a.trade_permission = "wait"
        a.trade_environment = "wait"
        reasons.append("c_default_unclear")

    # 4. Apply news grade cap
    GRADE_RANK = {"A+":4, "A":3, "B":2, "C":1}
    if GRADE_RANK.get(a.grade, 0) > GRADE_RANK.get(news_grade_cap, 4):
        a.grade = news_grade_cap
        # When cap reduces grade, force wait (NOT block).
        # block is reserved for HARD_BLOCK_TABLE conditions.
        if a.grade in ("B", "C"):
            a.trade_permission = "wait"
        reasons.append(f"news_grade_cap:{news_grade_cap}")

    a.reason = (a.reason + "|" if a.reason else "") + ";".join(reasons)
    return a
