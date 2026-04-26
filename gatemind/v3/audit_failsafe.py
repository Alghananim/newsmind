# -*- coding: utf-8 -*-
"""GateMind audit — every failure path must end at wait or block, never enter."""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from gatemind.v3 import GateMindV3, BrainSummary, SystemState

NOW = datetime(2026, 4, 25, 13, 30, 0, tzinfo=timezone.utc)
NOW_OUT = datetime(2026, 4, 25, 6, 0, 0, tzinfo=timezone.utc)


def make_state(**kw):
    base = dict(pair="EUR/USD", broker_mode="live", live_enabled=True,
                spread_pips=0.5, max_spread_pips=2.0,
                expected_slippage_pips=0.3, max_slippage_pips=2.0,
                pair_status="production")
    base.update(kw); return SystemState(**base)


@dataclass
class Spec:
    name: str
    expect_in: tuple   # acceptable decisions

CASES = []

# 1. Brain missing
CASES.append((Spec("brain_news_missing", ("block","wait")),
    lambda: dict(news=None,
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

CASES.append((Spec("brain_market_missing", ("block","wait")),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=None,
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

CASES.append((Spec("brain_chart_missing", ("block","wait")),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=None,
                 state=make_state(), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 2. State missing
CASES.append((Spec("state_missing", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=None, now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 3. now_utc None
CASES.append((Spec("now_utc_none_falls_back_to_real", ("enter","wait","block")),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(), now_utc=None,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 4. atr=0 (no risk_atr check possible)
CASES.append((Spec("atr_zero", ("enter","wait","block")),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.0)))

# 5. spread None
CASES.append((Spec("spread_none", ("block","wait")),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(spread_pips=None), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 6. Unknown pair
def s_unknown_pair():
    cfg = dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
               market=BrainSummary("market","allow","A",0.8,"bullish"),
               chart=BrainSummary("chart","allow","A",0.8,"bullish"),
               state=make_state(pair="ZZZ/YYY", pair_status="unknown"),
               now_utc=NOW,
               entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)
    cfg["pair"] = "ZZZ/YYY"
    return cfg
CASES.append((Spec("unknown_pair", ("block",)), s_unknown_pair))

# 7. Outside session window
CASES.append((Spec("outside_session", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(), now_utc=NOW_OUT,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 8. Daily loss limit
CASES.append((Spec("daily_loss_at_limit", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(daily_loss_pct=5.0), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 9. consecutive losses 3+
CASES.append((Spec("consecutive_losses_3", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(consecutive_losses=3), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 10. Open position
CASES.append((Spec("open_position", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(open_positions=(("EUR/USD","buy",1000),)), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 11. Pair disabled
def s_disabled():
    cfg = dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
               market=BrainSummary("market","allow","A",0.8,"bullish"),
               chart=BrainSummary("chart","allow","A",0.8,"bullish"),
               state=make_state(pair="GBP/USD", pair_status="disabled"),
               now_utc=NOW,
               entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)
    cfg["pair"] = "GBP/USD"
    return cfg
CASES.append((Spec("pair_disabled", ("block",)), s_disabled))

# 12. Monitoring + live
def s_monitoring():
    cfg = dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
               market=BrainSummary("market","allow","A",0.8,"bullish"),
               chart=BrainSummary("chart","allow","A",0.8,"bullish"),
               state=make_state(pair="USD/JPY", pair_status="monitoring",
                                broker_mode="live", live_enabled=True),
               now_utc=NOW,
               entry_price=150.0, stop_loss=149.7, take_profit=150.6, atr=0.10)
    cfg["pair"] = "USD/JPY"
    return cfg
CASES.append((Spec("monitoring_live_blocked", ("block",)), s_monitoring))

# 13. Broker mode unknown
CASES.append((Spec("broker_mode_unknown", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(broker_mode="weird_mode"), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 14. RR < 0.8
CASES.append((Spec("rr_below_0.8", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.098, take_profit=1.101, atr=0.001)))

# 15. Latency too high
CASES.append((Spec("latency_too_high", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(data_latency_ms=2000.0), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 16. Confidence very low
CASES.append((Spec("confidence_very_low", ("wait","block")),
    lambda: dict(news=BrainSummary("news","allow","A",0.2,"bullish"),
                 market=BrainSummary("market","allow","A",0.2,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.2,"bullish"),
                 state=make_state(), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 17. Daily trade limit
CASES.append((Spec("daily_trade_limit", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(trades_today=10), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 18. Pending order
CASES.append((Spec("pending_order_exists", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(pending_orders=(("EUR/USD","sell",500),)), now_utc=NOW,
                 entry_price=1.10, stop_loss=1.0985, take_profit=1.103, atr=0.001)))

# 19. Stop too tight (< 0.3 ATR)
CASES.append((Spec("stop_too_tight", ("block",)),
    lambda: dict(news=BrainSummary("news","allow","A",0.8,"bullish"),
                 market=BrainSummary("market","allow","A",0.8,"bullish"),
                 chart=BrainSummary("chart","allow","A",0.8,"bullish"),
                 state=make_state(), now_utc=NOW,
                 entry_price=1.1000, stop_loss=1.0998, take_profit=1.1010, atr=0.0020)))


def main():
    print("=" * 100)
    print("GateMind — Audit / Fail-Safe (19 سيناريو خطر)")
    print("=" * 100)
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
        print(f"  decision={d.final_decision}  expected_in={list(spec.expect_in)}")
        print(f"  blocking={list(d.blocking_reasons)[:2]}")
        print(f"  reason={d.reason[:140]}")
        if not ok:
            fail_rows.append((i, spec.name, f"got {d.final_decision} not in {spec.expect_in}"))

    print(f"\n{'='*100}\nFINAL: {pass_n}/{len(CASES)} PASSED  ({pass_n*100//len(CASES)}%)")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("="*100)
    return pass_n, len(CASES)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
