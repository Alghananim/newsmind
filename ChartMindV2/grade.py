# -*- coding: utf-8 -*-
"""GradeAssigner — converts confluence + risk profile into A+/A/B/C.

Grading rules (after extensive thought):
   * A+ : confluence >= 6 AND no risks AND R:R >= 2.5 AND ADX strong
   * A  : confluence >= 5 AND <= 1 risk
   * B  : confluence >= 4 AND <= 2 risks
   * C  : everything else (will be filtered out by min_grade gate)

Grade is consumed by GateMind for sizing. A+ may get full risk %,
A may get 75%, B may get 50%, C is never traded.
"""
from __future__ import annotations
from .models import TradePlan


def assign_grade(plan: TradePlan) -> str:
    score = plan.confluence_score
    n_risks = len(plan.risks)
    rr = plan.rr_ratio
    adx_strong = (plan.trend is not None and plan.trend.m15_adx >= 25)

    if score >= 6 and n_risks == 0 and rr >= 2.5 and adx_strong:
        return "A+"
    if score >= 5 and n_risks <= 1:
        return "A"
    if score >= 4 and n_risks <= 2:
        return "B"
    return "C"
