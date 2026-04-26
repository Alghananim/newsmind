# -*- coding: utf-8 -*-
"""GateMind V3 — Certification Test (25 سيناريو من قائمة المستخدم).

Each scenario produces row format:
    INPUT (brain summaries + state)
    CHECKS (alignment / risk / session / news / execution / state)
    DECISION (enter/wait/block + direction + approved)
    REASON
    BLOCKING_REASONS
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from gatemind.v3 import GateMindV3, BrainSummary, SystemState


# UTC time during NY trading window (NY 09:30 EDT = 13:30 UTC)
NOW_IN = datetime(2026, 4, 25, 13, 30, 0, tzinfo=timezone.utc)
# UTC time outside NY trading windows (NY 02:00 EDT = 06:00 UTC)
NOW_OUT = datetime(2026, 4, 25, 6, 0, 0, tzinfo=timezone.utc)


def make_state(**kw):
    """Default state: production EUR/USD, live broker, no positions, fresh data."""
    base = dict(
        pair="EUR/USD", broker_mode="live", live_enabled=True,
        spread_pips=0.5, max_spread_pips=2.0,
        expected_slippage_pips=0.3, max_slippage_pips=2.0,
        open_positions=(), pending_orders=(),
        daily_loss_pct=0.0, daily_loss_limit_pct=5.0,
        trades_today=0, daily_trade_limit=10,
        consecutive_losses=0, cooldown_until_utc=None,
        pair_status="production", data_latency_ms=50.0, max_data_latency_ms=1000.0,
    )
    base.update(kw)
    return SystemState(**base)


def good_brain(name, dir="bullish"):
    return BrainSummary(name, "allow", "A+", 0.9, dir, f"{name}_strong")


def reasonable_trade():
    return dict(entry_price=1.1000, stop_loss=1.0985, take_profit=1.1030, atr=0.0020)


@dataclass
class Spec:
    name: str
    expect_decision: str       # enter/wait/block/any
    expect_blocking_substr: str = ""


SCENARIOS = []

# 1: All A/A+ aligned bullish
SCENARIOS.append((
    Spec("01_all_top_aligned", "enter"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 2: One brain B → wait
SCENARIOS.append((
    Spec("02_one_brain_B", "wait", "_grade_B"),
    lambda: dict(news=good_brain("news"),
                 market=BrainSummary("market","wait","B",0.5,"bullish"),
                 chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 3: One brain C → block
SCENARIOS.append((
    Spec("03_one_brain_C", "block", "_grade_C"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"),
                 chart=BrainSummary("chart","block","C",0.0,"unclear","fake_breakout"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 4: Block from any brain
SCENARIOS.append((
    Spec("04_news_block", "block", "news_block"),
    lambda: dict(news=BrainSummary("news","block","C",0.0,"unclear","NFP_pre_window"),
                 market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 5: Conflicting directions
SCENARIOS.append((
    Spec("05_conflicting_directions", "block", "alignment_conflicting"),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bearish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 6: Low confidence
SCENARIOS.append((
    Spec("06_low_confidence", "wait", "low_confidence"),
    lambda: dict(news=BrainSummary("news","allow","A",0.4,"bullish"),
                 market=BrainSummary("market","allow","A",0.3,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.5,"bullish"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 7: Dangerous warning (we use grade B as proxy)
SCENARIOS.append((
    Spec("07_warning_drops_to_wait", "wait"),
    lambda: dict(news=good_brain("news"),
                 market=BrainSummary("market","wait","B",0.6,"bullish","correlation broken"),
                 chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 8: Stop loss missing
SCENARIOS.append((
    Spec("08_stop_missing", "block", "risk_missing"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN,
                 entry_price=1.10, stop_loss=None, take_profit=1.103, atr=0.001)
))

# 9: Target missing
SCENARIOS.append((
    Spec("09_target_missing", "block", "risk_missing"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN,
                 entry_price=1.10, stop_loss=1.099, take_profit=None, atr=0.001)
))

# 10: R/R weak (< 0.8)
SCENARIOS.append((
    Spec("10_rr_too_low", "block", "rr_too_low"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN,
                 entry_price=1.10, stop_loss=1.098, take_profit=1.101, atr=0.0015)
))

# 11: Spread too high
SCENARIOS.append((
    Spec("11_spread_too_high", "block", "execution_spread_too_wide"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(spread_pips=3.5, max_spread_pips=2.0),
                 now_utc=NOW_IN, **reasonable_trade())
))

# 12: Slippage too high
SCENARIOS.append((
    Spec("12_slippage_too_high", "block", "slippage_too_high"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(expected_slippage_pips=3.0, max_slippage_pips=2.0),
                 now_utc=NOW_IN, **reasonable_trade())
))

# 13: Outside trading window
SCENARIOS.append((
    Spec("13_outside_window", "block", "session_outside"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_OUT, **reasonable_trade())
))

# 14: Pre-news (NewsMind block)
SCENARIOS.append((
    Spec("14_pre_news_block", "block", "news_block"),
    lambda: dict(news=BrainSummary("news","block","C",0.0,"unclear","scheduled_NFP"),
                 market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 15: Post-news (NewsMind wait)
SCENARIOS.append((
    Spec("15_post_news_wait", "wait", "news_wait"),
    lambda: dict(news=BrainSummary("news","wait","B",0.5,"unclear","post_event_cooldown"),
                 market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_IN, **reasonable_trade())
))

# 16: Open position already exists
SCENARIOS.append((
    Spec("16_position_open", "block", "position_already_open"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(open_positions=(("EUR/USD","buy",1000),)),
                 now_utc=NOW_IN, **reasonable_trade())
))

# 17: Pending order
SCENARIOS.append((
    Spec("17_pending_order", "block", "pending_order_exists"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(pending_orders=(("EUR/USD","sell",500),)),
                 now_utc=NOW_IN, **reasonable_trade())
))

# 18: Consecutive losses ≥ 3
SCENARIOS.append((
    Spec("18_consecutive_losses_cooldown", "block", "after_3_losses"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(consecutive_losses=3),
                 now_utc=NOW_IN, **reasonable_trade())
))

# 19: Daily loss limit hit
SCENARIOS.append((
    Spec("19_daily_loss_limit", "block", "daily_loss_limit"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(daily_loss_pct=5.0),
                 now_utc=NOW_IN, **reasonable_trade())
))

# 20: Daily trade limit hit
SCENARIOS.append((
    Spec("20_daily_trade_limit", "block", "daily_trade_limit"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(trades_today=10, daily_trade_limit=10),
                 now_utc=NOW_IN, **reasonable_trade())
))

# 21: Pair disabled
SCENARIOS.append((
    Spec("21_pair_disabled", "block", "disabled_pair"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(pair="GBP/USD", pair_status="disabled"),
                 now_utc=NOW_IN, **reasonable_trade()) | {"pair":"GBP/USD"}
))

# Helper: scenario 21 needs pair=GBP/USD
def s21():
    cfg = dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
               state=make_state(pair="GBP/USD", pair_status="disabled"),
               now_utc=NOW_IN, **reasonable_trade())
    cfg["pair"] = "GBP/USD"
    return cfg
SCENARIOS[20] = (Spec("21_pair_disabled", "block", "disabled_pair"), s21)

# 22: Pair monitoring + live mode
def s22():
    cfg = dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
               state=make_state(pair="USD/JPY", pair_status="monitoring",
                                broker_mode="live", live_enabled=True),
               now_utc=NOW_IN, **reasonable_trade())
    cfg["pair"] = "USD/JPY"
    return cfg
SCENARIOS.append((Spec("22_monitoring_pair_live", "block", "monitoring_pair_live_blocked"), s22))

# 23: Broker live not allowed (broker_mode unsafe)
def s23():
    cfg = dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
               state=make_state(broker_mode="unknown_broker"),
               now_utc=NOW_IN, **reasonable_trade())
    return cfg
SCENARIOS.append((Spec("23_broker_unsafe", "block", "broker_unsafe"), s23))

# 24: Data stale (high latency)
SCENARIOS.append((
    Spec("24_data_stale", "block", "data_stale"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(data_latency_ms=2000.0),
                 now_utc=NOW_IN, **reasonable_trade())
))

# 25: Retry after window ended (proxy: outside window)
SCENARIOS.append((
    Spec("25_retry_outside_window", "block", "session_outside"),
    lambda: dict(news=good_brain("news"), market=good_brain("market"), chart=good_brain("chart"),
                 state=make_state(), now_utc=NOW_OUT, **reasonable_trade())
))


def main():
    print("=" * 100)
    print("GateMind V3 — Certification Test (25 سيناريو)")
    print("=" * 100)
    gm = GateMindV3()
    pass_n = 0
    fail_rows = []

    for i, (spec, build) in enumerate(SCENARIOS, 1):
        cfg = build()
        # Default pair if not overridden
        cfg.setdefault("pair", "EUR/USD")
        d = gm.decide(**cfg)

        ok_dec = (spec.expect_decision == "any") or (d.final_decision == spec.expect_decision)
        ok_block = (not spec.expect_blocking_substr) or any(
            spec.expect_blocking_substr in r for r in d.blocking_reasons) or any(
            spec.expect_blocking_substr in w for w in d.warnings) or (
            spec.expect_blocking_substr in d.reason)
        ok_all = ok_dec and ok_block
        if ok_all: pass_n += 1
        status = "PASS" if ok_all else "FAIL"

        print(f"\n┌─[{i:02d}] {spec.name}  {'✓ '+status if ok_all else '✗ '+status}")
        print(f"│ INPUT     : pair={d.pair}  audit_id={d.audit_id[:8]}")
        print(f"│ GRADES    : {d.grades_received}")
        print(f"│ PERMS     : {d.permissions_received}")
        print(f"│ ALIGN     : {d.alignment_status}  conf_summary={d.confidence_summary}")
        print(f"│ RISK      : {d.risk_check_status}  rr={d.risk_reward}")
        print(f"│ SESSION   : {d.session_check_status}")
        print(f"│ NEWS      : {d.news_check_status}")
        print(f"│ EXECUTION : {d.execution_check_status}  spread={d.spread_check_status}  slip={d.slippage_check_status}")
        print(f"│ STATE     : {d.position_state_status}  daily={d.daily_limits_status}")
        print(f"│ BROKER    : mode={d.broker_mode}  live={d.live_enabled}")
        print(f"│ DECISION  : {d.final_decision}/{d.direction}  approved={d.approved}  expect={spec.expect_decision}  {'✓' if ok_dec else '✗'}")
        print(f"│ BLOCKING  : {list(d.blocking_reasons)[:3] or 'none'}")
        print(f"│ WARNINGS  : {list(d.warnings)[:3] or 'none'}")
        print(f"│ REASON    : {d.reason[:200]}")
        if not ok_all:
            why = []
            if not ok_dec: why.append(f"decision {d.final_decision}!={spec.expect_decision}")
            if not ok_block: why.append(f"missing blocking '{spec.expect_blocking_substr}'")
            print(f"│ FIX_NEEDED: {'; '.join(why)}")
            fail_rows.append((i, spec.name, "; ".join(why)))
        print(f"└─ correct? {status}")

    print("\n" + "=" * 100)
    print(f"FINAL: {pass_n}/{len(SCENARIOS)} PASSED  ({pass_n*100//len(SCENARIOS)}%)")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("=" * 100)
    return pass_n, len(SCENARIOS)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
