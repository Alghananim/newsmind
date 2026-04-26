# -*- coding: utf-8 -*-
"""GateMind calibration — verify B always wait, C always block,
A+ aligned + everything-good = enter.
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from gatemind.v3 import GateMindV3, BrainSummary, SystemState

NOW = datetime(2026, 4, 25, 13, 30, 0, tzinfo=timezone.utc)


def state(): return SystemState(pair="EUR/USD", broker_mode="live",
                                live_enabled=True, pair_status="production",
                                spread_pips=0.5, max_spread_pips=2.0,
                                expected_slippage_pips=0.3, max_slippage_pips=2.0)


def trade(): return dict(entry_price=1.10, stop_loss=1.0985,
                         take_profit=1.103, atr=0.001)


def check(name, news, market, chart, expect_decision, expect_substr=""):
    gm = GateMindV3()
    d = gm.decide(pair="EUR/USD", news=news, market=market, chart=chart,
                  state=state(), now_utc=NOW, **trade())
    ok_dec = d.final_decision == expect_decision
    ok_sub = (not expect_substr) or expect_substr in d.reason or any(
        expect_substr in r for r in d.blocking_reasons) or any(
        expect_substr in w for w in d.warnings)
    ok = ok_dec and ok_sub
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"   expected: {expect_decision} (substr='{expect_substr}')")
    print(f"   got     : {d.final_decision}/{d.direction}  reason={d.reason[:140]}")
    return ok


print("=" * 100)
print("GateMind — Calibration Audit")
print("=" * 100)

results = []

# Reference brains
def good(name, dir="bullish"):
    return BrainSummary(name, "allow", "A+", 0.9, dir, f"{name}_strong")

# 1: All A+ aligned → ENTER
results.append(check("01_all_A_plus_aligned_buy",
    good("news"), good("market"), good("chart"), "enter"))

# 2: All A aligned → ENTER (A is acceptable)
results.append(check("02_all_A_aligned",
    BrainSummary("news","allow","A",0.8,"bullish"),
    BrainSummary("market","allow","A",0.8,"bullish"),
    BrainSummary("chart","allow","A",0.8,"bullish"),
    "enter"))

# 3: One brain B → WAIT (B never enters)
results.append(check("03_news_B_waits",
    BrainSummary("news","wait","B",0.6,"bullish"),
    good("market"), good("chart"), "wait", "news_grade_B"))

results.append(check("04_market_B_waits",
    good("news"),
    BrainSummary("market","wait","B",0.6,"bullish"),
    good("chart"), "wait", "market_grade_B"))

results.append(check("05_chart_B_waits",
    good("news"), good("market"),
    BrainSummary("chart","wait","B",0.6,"bullish"), "wait", "chart_grade_B"))

# 6-8: One brain C → BLOCK (C never enters/waits)
results.append(check("06_news_C_blocks",
    BrainSummary("news","block","C",0.0,"unclear"),
    good("market"), good("chart"), "block", "news_grade_C"))

results.append(check("07_market_C_blocks",
    good("news"),
    BrainSummary("market","block","C",0.0,"unclear"),
    good("chart"), "block", "market_grade_C"))

results.append(check("08_chart_C_blocks",
    good("news"), good("market"),
    BrainSummary("chart","block","C",0.0,"unclear"), "block", "chart_grade_C"))

# 9: News block dominates everything (chart A+ doesn't save it)
results.append(check("09_news_block_dominates",
    BrainSummary("news","block","C",0.0,"unclear","NFP"),
    good("market"), good("chart"), "block", "news_block"))

# 10: Conflicting directions → BLOCK
results.append(check("10_conflicting_directions",
    BrainSummary("news","allow","A",0.8,"bullish"),
    BrainSummary("market","allow","A",0.8,"bearish"),
    BrainSummary("chart","allow","A",0.8,"bullish"),
    "block", "alignment_conflicting"))

# 11: Low confidence → WAIT
results.append(check("11_low_confidence",
    BrainSummary("news","allow","A",0.3,"bullish"),
    BrainSummary("market","allow","A",0.3,"bullish"),
    BrainSummary("chart","allow","A",0.3,"bullish"),
    "wait", "low_confidence"))

# 12: Two brains B, one A+ — wait (B dominates)
results.append(check("12_two_B_waits",
    good("news"),
    BrainSummary("market","wait","B",0.6,"bullish"),
    BrainSummary("chart","wait","B",0.6,"bullish"),
    "wait", "_grade_B"))

# 13: Two brains C, one A+ — block (C dominates)
results.append(check("13_two_C_blocks",
    good("news"),
    BrainSummary("market","block","C",0.0,"unclear"),
    BrainSummary("chart","block","C",0.0,"unclear"),
    "block", "_grade_C"))

# 14: NewsMind wait grade B (post-news cooldown)
results.append(check("14_news_wait_post_event",
    BrainSummary("news","wait","B",0.5,"unclear","post_event_cooldown"),
    good("market"), good("chart"), "wait", "news_wait"))

# 15: Confirm: A+ + A + A grades = ENTER (no B/C present)
results.append(check("15_mixed_A_plus_and_A",
    BrainSummary("news","allow","A+",0.9,"bullish"),
    BrainSummary("market","allow","A",0.8,"bullish"),
    BrainSummary("chart","allow","A",0.8,"bullish"),
    "enter"))

# 16: Aligned but only 2 brains have direction set (one neutral)
results.append(check("16_one_neutral_two_bullish",
    BrainSummary("news","allow","A",0.8,"neutral"),
    BrainSummary("market","allow","A",0.8,"bullish"),
    BrainSummary("chart","allow","A",0.8,"bullish"),
    "enter"))

# Summary
n_pass = sum(results)
print()
print("=" * 100)
print(f"FINAL: {n_pass}/{len(results)} PASSED")
print("=" * 100)
sys.exit(0 if n_pass == len(results) else 1)
