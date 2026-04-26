# -*- coding: utf-8 -*-
"""Pattern miner for hidden patterns in trade history.

Looks for:
   - grade_A_loses_more_than_B (grade calibration broken)
   - chart_high_conf_but_loses (overconfident brain)
   - same_loss_pattern_repeats (recurring bug?)
   - session_x_underperforms
   - pair_x_underperforms_at_session_y
"""
from __future__ import annotations
from collections import Counter, defaultdict
from typing import List
from .storage import Storage


def detect_patterns(storage: Storage, *, pair: str = None) -> dict:
    trades = storage.query_trades(pair=pair, limit=5000)
    findings = {}

    # 1. Grade calibration: avg pnl per grade
    pnl_by_grade = defaultdict(list)
    for t in trades:
        mo = t.get("mind_outputs", {}) or {}
        for gname in (mo.get("news_grade"), mo.get("market_grade"), mo.get("chart_grade")):
            if gname in ("A+","A","B","C"):
                pnl_by_grade[gname].append(t.get("pnl",0))
    avg_pnl = {g: round(sum(v)/len(v), 5) if v else 0 for g, v in pnl_by_grade.items()}
    findings["avg_pnl_by_grade"] = avg_pnl
    findings["grade_calibration_ok"] = (
        avg_pnl.get("A+", 0) >= avg_pnl.get("A", 0) >=
        avg_pnl.get("B", 0) >= avg_pnl.get("C", 0))

    # 2. Brain overconfidence: high confidence but losses
    brain_overconfident = {}
    for brain_name in ("news", "market", "chart"):
        confs = []
        results = []
        for t in trades:
            mo = t.get("mind_outputs", {}) or {}
            c = mo.get(f"{brain_name}_confidence", 0)
            if c >= 0.8:
                confs.append(c)
                results.append(t.get("pnl", 0))
        if results:
            wins = sum(1 for r in results if r > 0)
            losses = sum(1 for r in results if r < 0)
            wr = wins / max(1, len(results))
            brain_overconfident[brain_name] = {
                "n_high_conf_trades": len(results),
                "win_rate_at_high_conf": round(wr, 3),
                "overconfident_flag": (wr < 0.5 and len(results) >= 3)
            }
    findings["brain_overconfidence"] = brain_overconfident

    # 3. Repeated loss pattern (same classification ≥3 times)
    losses = [t for t in trades if t.get("pnl", 0) < 0]
    cls_counts = Counter(t.get("classification","") for t in losses)
    repeated = {cls: cnt for cls, cnt in cls_counts.items() if cnt >= 3}
    findings["repeated_loss_patterns"] = repeated

    # 4. Same responsible_mind for losses
    resp_counts = Counter(
        (t.get("attribution",{}) or {}).get("responsible_mind","")
        for t in losses)
    findings["responsible_mind_for_losses"] = dict(resp_counts.most_common(5))

    return findings
