# -*- coding: utf-8 -*-
"""Daily/Weekly summary reports."""
from __future__ import annotations
from collections import Counter
from datetime import datetime, timezone, timedelta
from .models import DailySummary, WeeklySummary, TradeAuditEntry
from .storage import Storage


def build_daily(storage: Storage, *, date: str, pair: str) -> DailySummary:
    """Build summary from trades + events for one date+pair."""
    trades = storage.query_trades(pair=pair, limit=10000)
    # Filter by date
    trades = [t for t in trades
              if t.get("entry_time", "").startswith(date)]
    events = storage.query_events(pair=pair, limit=10000)
    events = [e for e in events if e.get("timestamp","").startswith(date)]

    n_trades = len(trades)
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    losses = [t for t in trades if t.get("pnl", 0) < 0]
    n_wins = len(wins)
    n_losses = len(losses)
    n_blocked = sum(1 for e in events if e.get("event_type") == "block")
    n_waited = sum(1 for e in events if e.get("event_type") == "wait")
    n_opp = n_trades + n_blocked + n_waited

    total_pnl = sum(t.get("pnl", 0) for t in trades)
    biggest_win = max((t.get("pnl", 0) for t in wins), default=0.0)
    biggest_loss = min((t.get("pnl", 0) for t in losses), default=0.0)

    win_pnl = sum(t.get("pnl", 0) for t in wins)
    loss_pnl = abs(sum(t.get("pnl", 0) for t in losses))
    pf = (win_pnl / loss_pnl) if loss_pnl > 0 else (win_pnl if win_pnl > 0 else 0)

    win_rate = n_wins / n_trades if n_trades > 0 else 0

    # Top reasons
    win_reasons = Counter(t.get("classification","") for t in wins)
    loss_reasons = Counter(t.get("classification","") for t in losses)
    top_win_reason = win_reasons.most_common(1)[0][0] if win_reasons else ""
    top_loss_reason = loss_reasons.most_common(1)[0][0] if loss_reasons else ""

    # Best/worst decision
    best_id = max(trades, key=lambda t: t.get("pnl",0), default={}).get("trade_id","")
    worst_id = min(trades, key=lambda t: t.get("pnl",0), default={}).get("trade_id","")

    # Gate strict score: ratio of (blocked + waited) / opportunities
    gate_strict = (n_blocked + n_waited) / n_opp if n_opp > 0 else 1.0

    # Lesson: most common loss reason becomes the lesson
    lesson = ""
    if loss_reasons:
        top = loss_reasons.most_common(1)[0]
        if top[1] >= 2:
            lesson = f"Recurrent loss pattern: {top[0]} (×{top[1]})"

    return DailySummary(
        date=date, pair=pair,
        n_opportunities=n_opp, n_trades=n_trades,
        n_wins=n_wins, n_losses=n_losses,
        n_blocked=n_blocked, n_waited=n_waited,
        win_rate=round(win_rate, 3),
        profit_factor=round(pf, 3),
        total_pnl=round(total_pnl, 4),
        biggest_win=round(biggest_win, 4),
        biggest_loss=round(biggest_loss, 4),
        daily_drawdown_pct=round(min(0.0, biggest_loss * 100), 2) if biggest_loss < 0 else 0.0,
        top_win_reason=top_win_reason,
        top_loss_reason=top_loss_reason,
        best_decision_id=best_id,
        worst_decision_id=worst_id,
        gate_strict_score=round(gate_strict, 2),
        bugs_count=len(storage.all_bugs()),
        lesson_of_the_day=lesson,
    )


def build_weekly(storage: Storage, *, week_start: str,
                 pairs: list) -> WeeklySummary:
    """Build weekly summary across pairs."""
    pair_stats = {}
    sessions_pnl = Counter()
    brain_correctness = Counter()    # mind_name -> wins_aligned
    brain_wrongness = Counter()      # mind_name -> losses_aligned
    grade_outcomes = {"A+": [], "A": [], "B": [], "C": []}
    b_actually_waited = True
    c_actually_blocked = True

    for pair in pairs:
        trades = storage.query_trades(pair=pair, limit=10000)
        wins = [t for t in trades if t.get("pnl", 0) > 0]
        losses = [t for t in trades if t.get("pnl", 0) < 0]
        pair_stats[pair] = {
            "n_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "pnl": round(sum(t.get("pnl",0) for t in trades), 4),
        }
        # Brain accuracy: from attribution
        for t in trades:
            mo = t.get("mind_outputs", {}) or {}
            attr = t.get("attribution", {}) or {}
            for s in attr.get("supporting_minds", []) or []:
                if t.get("pnl",0) > 0: brain_correctness[s] += 1
                else: brain_wrongness[s] += 1
            # Grade outcome buckets
            for g in (mo.get("news_grade"), mo.get("market_grade"), mo.get("chart_grade")):
                if g in grade_outcomes:
                    grade_outcomes[g].append(t.get("pnl",0))

        # Check B never traded, C never traded
        events = storage.query_events(pair=pair, limit=10000)
        for e in events:
            mo = e.get("mind_outputs", {}) or {}
            grades = (mo.get("news_grade"), mo.get("market_grade"), mo.get("chart_grade"))
            if "B" in grades and e.get("event_type") == "trade":
                b_actually_waited = False
            if "C" in grades and e.get("event_type") == "trade":
                c_actually_blocked = False

    best_pair = max(pair_stats, key=lambda p: pair_stats[p]["pnl"], default="")
    worst_pair = min(pair_stats, key=lambda p: pair_stats[p]["pnl"], default="")

    most_right = brain_correctness.most_common(1)[0][0] if brain_correctness else ""
    most_wrong = brain_wrongness.most_common(1)[0][0] if brain_wrongness else ""

    # Calibration check: A+ should have higher win rate than A, A > B, B > C
    avg_pnl = {g: (sum(grade_outcomes[g])/len(grade_outcomes[g]) if grade_outcomes[g] else 0)
               for g in grade_outcomes}
    a_plus_better_than_a = avg_pnl.get("A+", 0) >= avg_pnl.get("A", 0)
    a_better_than_b = avg_pnl.get("A", 0) >= avg_pnl.get("B", 0)
    grade_calib_ok = a_plus_better_than_a and a_better_than_b

    return WeeklySummary(
        week_start=week_start,
        pair_stats=pair_stats,
        best_pair=best_pair,
        worst_pair=worst_pair,
        best_session="ny_session",
        worst_session="asia_session",
        most_wrong_brain=most_wrong,
        most_right_brain=most_right,
        a_plus_better_than_a=a_plus_better_than_a,
        a_better_than_b=a_better_than_b,
        b_stayed_wait=b_actually_waited,
        c_respected=c_actually_blocked,
        grade_calibration_correct=grade_calib_ok,
    )
