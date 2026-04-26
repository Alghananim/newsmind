# -*- coding: utf-8 -*-
"""GateMind V4 — Speed + Intelligence Cert (15 سيناريو من قائمة المستخدم)."""
from __future__ import annotations
import sys, time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from gatemind.v3 import GateMindV3, BrainSummary, SystemState

NOW_IN = datetime(2026,4,25,13,30,0,tzinfo=timezone.utc)        # NY 09:30 EDT
NOW_END = datetime(2026,4,25,15,57,0,tzinfo=timezone.utc)       # NY 11:57 EDT (3 min from end)
NOW_OUT = datetime(2026,4,25,6,0,0,tzinfo=timezone.utc)         # NY 02:00 EDT

TARGET_LATENCY_MS = 5.0


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
class Spec:
    name: str
    expect_decision: str            # enter/wait/block/any
    max_latency_ms: float = TARGET_LATENCY_MS
    expect_min_intel: float = 0.0
    expect_contradiction_substr: str = ""


SCENARIOS = []

# 1: All A+ aligned + clean state → enter, ALL fast
SCENARIOS.append((Spec("01_all_top_clean_fast_enter", "enter", 5.0, 0.85),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN, **trade())))

# 2: High grade but warning keyword in reason — should be caught
SCENARIOS.append((Spec("02_grade_high_but_reason_warning", "wait", 5.0, 0.0,
                       expect_contradiction_substr="warning_keyword"),
    lambda: dict(news=BrainSummary("news","allow","A+",0.9,"bullish","fresh_verified|data_stale_warning"),
                 market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN, **trade())))

# 3: High confidence but data stale → contradiction
SCENARIOS.append((Spec("03_high_conf_data_stale", "block", 5.0, 0.0,
                       expect_contradiction_substr="data_stale"),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(data_latency_ms=2500.0), now_utc=NOW_IN, **trade())))

# 4: All ok but spread near max (>70%) → contradiction medium
SCENARIOS.append((Spec("04_spread_near_max", "wait", 5.0, 0.0,
                       expect_contradiction_substr="spread_near_max"),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(spread_pips=1.6, max_spread_pips=2.0),
                 now_utc=NOW_IN, **trade())))

# 5: In window but near end (last 5 min) — retry risk
SCENARIOS.append((Spec("05_in_window_near_end", "wait", 5.0, 0.0,
                       expect_contradiction_substr="near_end"),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_END, **trade())))

# 6: chart allow but reason has "fake_breakout" — hidden trap
SCENARIOS.append((Spec("06_chart_allow_but_reason_fake", "wait", 5.0, 0.0,
                       expect_contradiction_substr="fake_breakout"),
    lambda: dict(news=good("news"), market=good("market"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish","trend_strong|fake_breakout_avoided"),
                 state=state(), now_utc=NOW_IN, **trade())))

# 7: monitoring + live + live_enabled → critical contradiction
def s7():
    cfg = dict(news=good("news"), market=good("market"), chart=good("chart"),
               state=state(pair="USD/JPY", pair_status="monitoring"),
               now_utc=NOW_IN, **trade())
    cfg["pair"] = "USD/JPY"
    return cfg
SCENARIOS.append((Spec("07_monitoring_with_live_critical", "block", 5.0, 0.0,
                       expect_contradiction_substr="monitoring_with_live"), s7))

# 8: Direction inconsistency between brains
SCENARIOS.append((Spec("08_direction_inconsistency_critical", "block", 5.0, 0.0,
                       expect_contradiction_substr="direction_inconsistency"),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bearish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=state(), now_utc=NOW_IN, **trade())))

# 9: Recent loss without cooldown set
SCENARIOS.append((Spec("09_recent_loss_no_cooldown", "wait", 5.0, 0.0,
                       expect_contradiction_substr="no_cooldown"),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(consecutive_losses=2, cooldown_until_utc=None),
                 now_utc=NOW_IN, **trade())))

# 10: R/R weak edge (1.0-1.2) — caught as medium contradiction
SCENARIOS.append((Spec("10_rr_weak_edge", "wait", 5.0, 0.0,
                       expect_contradiction_substr="rr_weak_edge"),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN,
                 entry_price=1.10, stop_loss=1.099, take_profit=1.10105,
                 atr=0.0015)))

# 11: All conditions great → enter (positive control for speed)
SCENARIOS.append((Spec("11_all_great_enter", "enter", 5.0, 0.85),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN, **trade())))

# 12: Outside window → block fast
SCENARIOS.append((Spec("12_outside_window_fast_block", "block", 5.0),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_OUT, **trade())))

# 13: One brain block — fast block
SCENARIOS.append((Spec("13_news_block_fast", "block", 5.0),
    lambda: dict(news=BrainSummary("news","block","C",0.0,"unclear","NFP_window"),
                 market=good("market"), chart=good("chart"),
                 state=state(), now_utc=NOW_IN, **trade())))

# 14: Pair disabled
def s14():
    cfg = dict(news=good("news"), market=good("market"), chart=good("chart"),
               state=state(pair="GBP/USD", pair_status="disabled"),
               now_utc=NOW_IN, **trade())
    cfg["pair"] = "GBP/USD"
    return cfg
SCENARIOS.append((Spec("14_pair_disabled_fast_block", "block", 5.0), s14))

# 15: Daily loss limit reached
SCENARIOS.append((Spec("15_daily_loss_limit_block", "block", 5.0),
    lambda: dict(news=good("news"), market=good("market"), chart=good("chart"),
                 state=state(daily_loss_pct=5.0), now_utc=NOW_IN, **trade())))


def main():
    print("=" * 100)
    print("GateMind V4 — Speed + Intelligence Cert (15 سيناريو)")
    print("=" * 100)
    gm = GateMindV3()
    pass_n = 0
    cold_lats = []
    fail_rows = []

    for i, (spec, build) in enumerate(SCENARIOS, 1):
        cfg = build()
        cfg.setdefault("pair", "EUR/USD")

        # Run twice to get cold + warm (no real cache, but simulate variance)
        t0 = time.perf_counter_ns()
        d = gm.decide(**cfg)
        ms = (time.perf_counter_ns() - t0) / 1e6
        cold_lats.append(ms)

        ok_dec = (spec.expect_decision == "any") or (d.final_decision == spec.expect_decision)
        ok_lat = d.total_gate_latency_ms <= spec.max_latency_ms
        ok_intel = d.gate_intelligence_score >= spec.expect_min_intel
        ok_contr = (not spec.expect_contradiction_substr or
                    any(spec.expect_contradiction_substr in c for c in d.contradictions_detected) or
                    spec.expect_contradiction_substr in d.reason or
                    any(spec.expect_contradiction_substr in r for r in d.blocking_reasons))
        ok_all = ok_dec and ok_lat and ok_intel and ok_contr
        if ok_all: pass_n += 1
        status = "PASS" if ok_all else "FAIL"

        print(f"\n┌─[{i:02d}] {spec.name}  {'✓ '+status if ok_all else '✗ '+status}")
        print(f"│ DECISION    : {d.final_decision}/{d.direction}  approved={d.approved}  expect={spec.expect_decision}  {'✓' if ok_dec else '✗'}")
        print(f"│ LATENCY     : total={d.total_gate_latency_ms}ms  raw={ms:.4f}ms  bottleneck={d.bottleneck_stage}  {'✓' if ok_lat else '✗ EXCEEDS '+str(spec.max_latency_ms)}")
        print(f"│ STAGES      : input={d.input_parse_latency_ms} align={d.alignment_check_latency_ms} risk={d.risk_check_latency_ms} session={d.session_check_latency_ms}")
        print(f"│              exec={d.execution_check_latency_ms} limits={d.daily_limits_check_latency_ms} final={d.final_decision_latency_ms}")
        print(f"│ INTELLIGENCE: gate={d.gate_intelligence_score} speed={d.gate_speed_score}  align={d.alignment_score} risk={d.risk_score} exec={d.execution_safety_score}")
        print(f"│ CONTRADICT  : {list(d.contradictions_detected) or 'none'}  {'✓' if ok_contr else '✗ missing '+spec.expect_contradiction_substr}")
        print(f"│ BLOCKING    : {list(d.blocking_reasons)[:2] or 'none'}")
        print(f"│ WARNINGS    : {list(d.warnings)[:2] or 'none'}")
        print(f"│ REASON      : {d.reason[:200]}")
        if not ok_all:
            why = []
            if not ok_dec: why.append(f"dec {d.final_decision}!={spec.expect_decision}")
            if not ok_lat: why.append(f"lat {d.total_gate_latency_ms}>{spec.max_latency_ms}")
            if not ok_intel: why.append(f"intel {d.gate_intelligence_score}<{spec.expect_min_intel}")
            if not ok_contr: why.append(f"missing contr '{spec.expect_contradiction_substr}'")
            print(f"│ FIX_NEEDED  : {'; '.join(why)}")
            fail_rows.append((i, spec.name, "; ".join(why)))
        print(f"└─ correct? {status}")

    print("\n" + "=" * 100)
    print(f"FINAL: {pass_n}/{len(SCENARIOS)} PASSED")
    avg = sum(cold_lats)/len(cold_lats)
    mx = max(cold_lats)
    print(f"LATENCY: avg={avg:.4f}ms  max={mx:.4f}ms  budget={TARGET_LATENCY_MS}ms  margin={TARGET_LATENCY_MS/avg:.0f}x")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("=" * 100)
    return pass_n, len(SCENARIOS)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
