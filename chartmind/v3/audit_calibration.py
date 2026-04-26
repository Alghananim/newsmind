# -*- coding: utf-8 -*-
"""Grade calibration audit.

Verifies that:
  1. A+ confidence > A confidence > B confidence > C confidence
  2. A+ never fires when entry is late/chase
  3. A+ never fires when fake_breakout
  4. A+ never fires when MTF conflicts
  5. A+ never fires when R/R < 1.5
  6. A grade requires R/R >= 1.2
  7. B can never have permission=allow
  8. C with hard_block has permission=block (not wait)
  9. C with hard_wait has permission=wait

Approach: invoke permission_engine.finalize() with synthetic ChartAssessment
objects covering each grade dimension and check the outputs.
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from datetime import datetime, timezone
from chartmind.v3 import ChartAssessment, permission_engine

NOW = datetime(2026,4,25,14,0,0,tzinfo=timezone.utc)
results = []


def mk(**kw):
    """Build a baseline assessment with 'all good' values, then override."""
    a = ChartAssessment(
        pair="EUR/USD", timestamp_utc=NOW,
        market_structure="uptrend",
        trend_direction="bullish",
        trend_strength=0.7,
        trend_quality="smooth",
        candlestick_signal="bull_engulfing",
        candlestick_context="at_support",
        candlestick_quality="strong",
        breakout_status="real",
        retest_status="successful",
        entry_quality="excellent",
        risk_reward=2.0,
        timeframe_alignment="aligned",
        volatility_status="normal",
        atr_status="normal",
        stop_loss=1.0950,
        take_profit=1.1100,
        fake_breakout_risk=False,
        liquidity_sweep_detected=False,
    )
    for k, v in kw.items():
        setattr(a, k, v)
    return a


def check(name, a, expect_grade, expect_perm):
    a = permission_engine.finalize(a)
    ok_grade = a.grade == expect_grade
    ok_perm = a.trade_permission == expect_perm
    ok = ok_grade and ok_perm
    results.append((name, ok, a.grade, expect_grade, a.trade_permission, expect_perm, a.reason))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    print(f"    expected grade={expect_grade} perm={expect_perm}")
    print(f"    got      grade={a.grade}      perm={a.trade_permission}")
    print(f"    reason  : {a.reason[:160]}")


print("=" * 100)
print("ChartMind — Grade Calibration Audit")
print("=" * 100)

# Test 1: ideal A+ setup
check("01_ideal_A_plus_setup", mk(), "A+", "allow")

# Test 2: A+ requirements broken — late entry → should NOT be A+
check("02_late_entry_blocks_A_plus", mk(entry_quality="late"), "C", "wait")

# Test 3: chase → block
check("03_chase_blocks_everything", mk(entry_quality="chase"), "C", "block")

# Test 4: fake breakout → block
check("04_fake_breakout_blocks", mk(breakout_status="fake", fake_breakout_risk=True),
      "C", "block")

# Test 5: liquidity sweep → block
check("05_liq_sweep_blocks", mk(liquidity_sweep_detected=True), "C", "block")

# Test 6: MTF conflict → block
check("06_mtf_conflict_blocks", mk(timeframe_alignment="conflicting"), "C", "block")

# Test 7: R/R 0.5 → block
check("07_rr_below_0.8_blocks", mk(risk_reward=0.5), "C", "block")

# Test 8: R/R 1.0 (between 0.8 and 1.2) — should be B (not A+/A)
check("08_rr_marginal_drops_to_B", mk(risk_reward=1.0, retest_status="none"), "B", "wait")

# Test 9: R/R 1.3 — should be A (not A+ since A+ needs 1.5)
check("09_rr_1.3_drops_to_A", mk(risk_reward=1.3), "A", "allow")

# Test 10: Excellent + R/R 1.5 + everything else perfect = A+
check("10_excellent_setup_full_A_plus", mk(), "A+", "allow")

# Test 11: candle quality "weak" + everything else perfect → A (not A+)
check("11_weak_candle_drops_A_plus_to_A", mk(candlestick_quality="weak"), "A", "allow")

# Test 12: candle context midrange + signal != none → hard_wait
check("12_candle_midrange_no_context_waits",
      mk(candlestick_signal="bull_engulfing", candlestick_context="midrange"),
      "C", "wait")

# Test 13: retest pending → hard_wait
check("13_retest_pending_waits", mk(retest_status="pending"), "C", "wait")

# Test 14: breakout weak → hard_wait
check("14_breakout_weak_waits", mk(breakout_status="weak"), "C", "wait")

# Test 15: trend exhausting → hard_wait
check("15_exhausting_trend_waits", mk(trend_quality="exhausting"), "C", "wait")

# Test 16: late entry → hard_wait
check("16_late_entry_waits", mk(entry_quality="late"), "C", "wait")

# Test 17: stop undefined → hard_wait
check("17_stop_undefined_waits", mk(stop_loss=None, risk_reward=None), "C", "wait")

# Test 18: range market → wait B (not block, not allow)
check("18_range_market_b_wait", mk(market_structure="range",
                                    trend_direction="neutral",
                                    market_structure_b="range"),
      "B", "wait")

# Test 19: choppy/unclear with low rr+late → no_actionable_setup → wait C
check("19_no_actionable_unclear_late_to_C_wait",
      mk(market_structure="unclear", entry_quality="late"), "C", "wait")

# Test 20: Confidence ordering check — A+ > A > B > C
print("\n--- Confidence ordering check ---")
ap = permission_engine.finalize(mk())
a  = permission_engine.finalize(mk(candlestick_quality="weak"))   # A grade
b  = permission_engine.finalize(mk(market_structure="range",
                                    trend_direction="neutral",
                                    risk_reward=1.0,
                                    retest_status="none"))         # B grade
c_block = permission_engine.finalize(mk(entry_quality="chase"))   # C block
c_wait  = permission_engine.finalize(mk(entry_quality="late"))    # C wait
print(f"  A+ confidence : {ap.confidence}  grade={ap.grade}")
print(f"  A  confidence : {a.confidence}   grade={a.grade}")
print(f"  B  confidence : {b.confidence}   grade={b.grade}")
print(f"  C(block) conf : {c_block.confidence}  grade={c_block.grade}")
print(f"  C(wait)  conf : {c_wait.confidence}  grade={c_wait.grade}")
ordering_ok = (ap.confidence > a.confidence > b.confidence > c_wait.confidence)
print(f"  ORDERING (A+ > A > B > C): {'✓ PASS' if ordering_ok else '✗ FAIL'}")
results.append(("20_confidence_ordering", ordering_ok, "", "", "", "",
                f"A+={ap.confidence} A={a.confidence} B={b.confidence} C={c_wait.confidence}"))

# Summary
print("\n" + "=" * 100)
pass_n = sum(1 for r in results if r[1])
print(f"FINAL: {pass_n}/{len(results)} PASSED")
fails = [(r[0], r[6]) for r in results if not r[1]]
if fails:
    print("FAILS:")
    for name, why in fails: print(f"  {name} — {why[:120]}")
print("=" * 100)
sys.exit(0 if pass_n == len(results) else 1)
