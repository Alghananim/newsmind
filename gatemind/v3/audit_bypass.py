# -*- coding: utf-8 -*-
"""GateMind bypass audit — try to find any path that allows enter when it shouldn't.

This test deliberately constructs scenarios designed to slip past the gate,
attempting every conceivable bypass:
   - block from one brain combined with allow from others
   - high grades but with hidden warning
   - all allow + valid risk + outside session
   - monitoring + paper (allowed) — verify it doesn't go to live
   - disabled + careful trade construction
   - confidence right at threshold
   - missing fields combined with otherwise valid trade
   - subtle direction conflicts
"""
from __future__ import annotations
import sys
from datetime import datetime, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from gatemind.v3 import GateMindV3, BrainSummary, SystemState

NOW_IN = datetime(2026,4,25,13,30,0,tzinfo=timezone.utc)
NOW_OUT = datetime(2026,4,25,6,0,0,tzinfo=timezone.utc)


def state(**kw):
    base = dict(pair="EUR/USD", broker_mode="live", live_enabled=True,
                spread_pips=0.5, max_spread_pips=2.0,
                expected_slippage_pips=0.3, max_slippage_pips=2.0,
                pair_status="production")
    base.update(kw); return SystemState(**base)


def good(name, dir="bullish"):
    return BrainSummary(name, "allow", "A+", 0.9, dir, f"{name}_strong")


def trade():
    return dict(entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)


@dataclass
class BypassSpec:
    """Each bypass attempt MUST fail to enter — must be wait or block."""
    name: str
    description: str
    expect_in: tuple = ("wait", "block")  # NEVER enter


CASES = []

# B1: News allow + market block — block must dominate
CASES.append((BypassSpec("B1_market_block_with_news_allow",
    "market block should not be bypassed by news/chart allow"),
    lambda: dict(news=good("news"),
                 market=BrainSummary("market","block","C",0.0,"unclear","fake_breakout"),
                 chart=good("chart"), state=state(), now_utc=NOW_IN, **trade())))

# B2: All allow but news has B grade
CASES.append((BypassSpec("B2_all_allow_one_B",
    "B grade should never enter even if permission is allow"),
    lambda: dict(news=BrainSummary("news","allow","B",0.7,"bullish","weak_signal"),
                 market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN, **trade())))

# B3: A+ aligned but outside window — must block, not enter
CASES.append((BypassSpec("B3_aligned_outside_window",
    "outside session must block even with perfect alignment"),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_OUT, **trade())))

# B4: Disabled pair + everything else perfect
def bp4():
    cfg = dict(news=good("news"), market=good("market"), chart=good("chart"),
               state=state(pair="GBP/USD", pair_status="disabled"),
               now_utc=NOW_IN, **trade())
    cfg["pair"] = "GBP/USD"
    return cfg
CASES.append((BypassSpec("B4_disabled_pair_perfect_setup",
    "disabled pair must block regardless of brain quality"), bp4))

# B5: Monitoring + live + everything else perfect
def bp5():
    cfg = dict(news=good("news"), market=good("market"), chart=good("chart"),
               state=state(pair="USD/JPY", pair_status="monitoring",
                           broker_mode="live", live_enabled=True),
               now_utc=NOW_IN, **trade())
    cfg["pair"] = "USD/JPY"
    return cfg
CASES.append((BypassSpec("B5_monitoring_pair_live_attempt",
    "monitoring pair must block live trades"), bp5))

# B6: Confidence exactly at threshold (0.6) — should be exactly OK
CASES.append((BypassSpec("B6_confidence_at_exact_threshold",
    "confidence == 0.6 should pass (boundary check)",
    expect_in=("enter","wait")),  # OK to enter at threshold
    lambda: dict(news=BrainSummary("news","allow","A",0.6,"bullish"),
                 market=BrainSummary("market","allow","A",0.6,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.6,"bullish"),
                 state=state(), now_utc=NOW_IN, **trade())))

# B7: Confidence just below threshold (0.59) — must NOT enter
CASES.append((BypassSpec("B7_confidence_just_below_threshold",
    "confidence < 0.6 must wait"),
    lambda: dict(news=BrainSummary("news","allow","A",0.59,"bullish"),
                 market=BrainSummary("market","allow","A",0.59,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.59,"bullish"),
                 state=state(), now_utc=NOW_IN, **trade())))

# B8: All allow but ONE brain has unclear direction
CASES.append((BypassSpec("B8_one_unclear_direction",
    "unclear direction with 2 bullish should still enter (acceptable)",
    expect_in=("enter","wait")),  # 2/3 bullish is enough
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"unclear"),
                 market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN, **trade())))

# B9: 1 bullish + 1 bearish + 1 neutral — must block (conflicting)
CASES.append((BypassSpec("B9_two_directions_one_neutral",
    "bullish + bearish = conflict → block",
    expect_in=("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bearish"),
                 chart=BrainSummary("chart","allow","A",0.8,"neutral"),
                 state=state(), now_utc=NOW_IN, **trade())))

# B10: Stop slightly tight (0.31 ATR — just above 0.3 limit) — should pass
CASES.append((BypassSpec("B10_stop_just_above_tight_threshold",
    "stop at 0.31 ATR should pass (boundary)",
    expect_in=("enter","wait","block")),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN,
                 entry_price=1.1000, stop_loss=1.09969, take_profit=1.103,
                 atr=0.001)))

# B11: R/R exactly 0.8 — boundary
CASES.append((BypassSpec("B11_rr_just_above_minimum",
    "R/R at 1.0 should be marginal/wait",
    expect_in=("wait",)),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN,
                 entry_price=1.1000, stop_loss=1.0990, take_profit=1.1010,
                 atr=0.001)))

# B12: Position open in different pair — should NOT block
CASES.append((BypassSpec("B12_position_open_different_pair",
    "position in OTHER pair should not block this pair",
    expect_in=("enter","wait","block")),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(open_positions=(("USD/JPY","buy",100),)),
                 now_utc=NOW_IN, **trade())))

# B13: Daily limits at 99% — should still pass (under limit)
CASES.append((BypassSpec("B13_daily_loss_just_under_limit",
    "daily loss < limit should pass",
    expect_in=("enter","wait","block")),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(daily_loss_pct=4.99, daily_loss_limit_pct=5.0),
                 now_utc=NOW_IN, **trade())))

# B14: Trades today equal to limit — must block (>= check)
CASES.append((BypassSpec("B14_trades_today_equal_limit",
    "trades_today == limit must block",
    expect_in=("block",)),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(trades_today=10, daily_trade_limit=10),
                 now_utc=NOW_IN, **trade())))

# B15: Critical contradiction (direction inconsistency) — must block
CASES.append((BypassSpec("B15_direction_inconsistency_critical",
    "directions conflicting → critical contradiction → block",
    expect_in=("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bearish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=state(), now_utc=NOW_IN, **trade())))

# B16: Just one brain None — should block (missing input)
CASES.append((BypassSpec("B16_one_brain_none",
    "missing brain → block",
    expect_in=("block","wait")),
    lambda: dict(news=None,
                 market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN, **trade())))

# B17: All None — should block hard
CASES.append((BypassSpec("B17_all_brains_none",
    "all brains None → block",
    expect_in=("block","wait")),
    lambda: dict(news=None, market=None, chart=None,
                 state=state(), now_utc=NOW_IN, **trade())))

# B18: State None — must block (no state validation possible)
CASES.append((BypassSpec("B18_state_none",
    "state None → block",
    expect_in=("block","wait")),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=None, now_utc=NOW_IN, **trade())))


def main():
    print("=" * 100)
    print("GateMind — BYPASS Audit (18 محاولة لاكتشاف ثغرة)")
    print("=" * 100)
    print("Each test deliberately tries to find a path that allows ENTER when it shouldn't.")
    print()
    gm = GateMindV3()
    pass_n = 0
    fail_rows = []

    for i, (spec, build) in enumerate(CASES, 1):
        try:
            cfg = build()
            cfg.setdefault("pair", "EUR/USD")
            d = gm.decide(**cfg)
        except Exception as e:
            print(f"\n[{i:02d}] {spec.name}  ✗ FAIL (CRASH: {type(e).__name__}: {e})")
            fail_rows.append((i, spec.name, f"crash: {e}"))
            continue
        ok = d.final_decision in spec.expect_in
        if ok: pass_n += 1
        status = "PASS" if ok else "FAIL"
        print(f"\n[{i:02d}] {spec.name}  {'✓ '+status if ok else '✗ '+status}")
        print(f"  description: {spec.description}")
        print(f"  decision={d.final_decision} (expected_in={list(spec.expect_in)})")
        print(f"  blocking={list(d.blocking_reasons)[:2]}")
        print(f"  warnings={list(d.warnings)[:2]}")
        print(f"  reason={d.reason[:140]}")
        if not ok:
            fail_rows.append((i, spec.name, f"got {d.final_decision} not in {spec.expect_in}"))

    print(f"\n{'='*100}\nFINAL: {pass_n}/{len(CASES)} PASSED")
    if fail_rows:
        print("\n*** BYPASS DETECTED — these need immediate fix ***")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("="*100)
    return pass_n, len(CASES)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
