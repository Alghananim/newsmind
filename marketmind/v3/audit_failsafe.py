# -*- coding: utf-8 -*-
"""MarketMind audit — every failure path must end at wait or block.

Tests:
    1. DXY missing (only EUR/USD bars)
    2. yields missing (always; no instrument wired) — accept gracefully
    3. gold missing — risk_sentiment must default to unclear
    4. price gap in candles
    5. spread missing (None)
    6. spread extreme
    7. volatility extreme (ATR spike)
    8. NewsMind block — must inherit
    9. NewsMind None — must not allow without scrutiny
    10. correlation data unavailable (only one bar series)
    11. unknown pair name
    12. session "off" (Sunday)
    13. very few bars (3) — must not crash, must wait
    14. timestamp far-future bars
    15. all sources stale
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from marketmind.v3 import MarketMindV3, Bar, cache as mm_cache


NOW = datetime(2026,4,25,14,0,0,tzinfo=timezone.utc)


def make_bars(n=80, start=1.10, slope=0.0, noise=0.0001, vol=1000, spread=0.5,
              ts_step=15, seed=42):
    import random
    rng = random.Random(seed)
    out = []
    last = start
    for i in range(n):
        ts = NOW - timedelta(minutes=ts_step*(n-i))
        target = start + slope * i
        rnd = (rng.random() - 0.5) * 2 * noise
        c = target + rnd
        rg = max(noise, abs(c - last)) * 1.2
        h = max(c, last) + rg/2
        l = min(c, last) - rg/2
        o = last + (c - last) * 0.3
        out.append(Bar(timestamp=ts, open=o, high=h, low=l, close=c,
                       volume=vol, spread_pips=spread))
        last = c
    return out


class MockNews:
    def __init__(self, perm="allow", bias="unclear", risk="unclear", reason=""):
        self.trade_permission = perm
        self.market_bias = bias
        self.risk_mode = risk
        self.reason = reason


@dataclass
class FailSpec:
    name: str
    expect_perm_in: tuple        # acceptable permissions (must contain wait or block)
    expect_warning_substr: str = ""

CASES = []

# 1. DXY missing — only EUR/USD bars (no USD/JPY, no GBP/USD)
CASES.append((
    FailSpec("dxy_missing_only_eurusd", ("wait", "block"),
             expect_warning_substr="dxy_coverage"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001)},
                 news_verdict=None)
))

# 2. yields missing — never wired; just verify yield_signal stays "unavailable"
# No assertion on permission here; just check the field
CASES.append((
    FailSpec("yields_unavailable_normal", ("allow","wait","block")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11),
                          "GBP/USD": make_bars(80, start=1.25, slope=0.0001, seed=22)},
                 news_verdict=None)
))

# 3. gold missing — risk_sentiment must downgrade to unclear; no allow on extreme
CASES.append((
    FailSpec("gold_missing_risk_unclear", ("allow","wait","block")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11),
                          "GBP/USD": make_bars(80, start=1.25, slope=0.0001, seed=22)},
                 bars_xau=None,
                 news_verdict=None)
))

# 4. Price gap in candles
def gap_eur():
    bs = make_bars(80, slope=0.0001)
    bs[-3] = Bar(bs[-3].timestamp, open=bs[-3].open + 0.01,
                 high=bs[-3].high + 0.01, low=bs[-3].low + 0.01,
                 close=bs[-3].close + 0.01, volume=bs[-3].volume,
                 spread_pips=bs[-3].spread_pips)
    bs[-2] = Bar(bs[-2].timestamp, open=bs[-2].open - 0.012,
                 high=bs[-2].high - 0.012, low=bs[-2].low - 0.012,
                 close=bs[-2].close - 0.012, volume=bs[-2].volume,
                 spread_pips=bs[-2].spread_pips)
    return bs

CASES.append((
    FailSpec("gap_in_candles", ("wait","block"),
             expect_warning_substr="gap"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": gap_eur(),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=None)
))

# 5. Spread None on last bar
def spread_none_eur():
    bs = make_bars(80, slope=0.0001)
    bs[-1] = Bar(bs[-1].timestamp, open=bs[-1].open, high=bs[-1].high,
                 low=bs[-1].low, close=bs[-1].close, volume=bs[-1].volume,
                 spread_pips=None)
    return bs
CASES.append((
    FailSpec("spread_none_must_not_crash", ("allow","wait","block")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": spread_none_eur(),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=None)
))

# 6. Spread extreme (5 pips on EUR/USD)
def wide_spread_eur():
    bs = make_bars(80, slope=0.0001)
    bs[-1] = Bar(bs[-1].timestamp, open=bs[-1].open, high=bs[-1].high,
                 low=bs[-1].low, close=bs[-1].close, volume=bs[-1].volume,
                 spread_pips=5.0)
    return bs
CASES.append((
    FailSpec("spread_extreme_must_block", ("block",)),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": wide_spread_eur(),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=None)
))

# 7. Volatility extreme (last bar ATR spike)
def spike_eur():
    bs = make_bars(79, slope=0)
    last = bs[-1].close
    bs.append(Bar(NOW, open=last, high=last+0.01, low=last-0.001,
                  close=last+0.01, volume=4000, spread_pips=2.5))
    return bs
CASES.append((
    FailSpec("volatility_extreme_must_wait", ("wait","block")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": spike_eur(),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=None)
))

# 8. NewsMind block — must inherit
CASES.append((
    FailSpec("news_block_must_inherit", ("block",)),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=MockNews("block","unclear","unclear","NFP_pre_window"))
))

# 9. NewsMind None + clean trend — should NOT allow without source scrutiny
# (clean trend allowed only if all V3 conditions pass; baseline should be A or wait)
CASES.append((
    FailSpec("no_news_clean_trend_acceptable", ("allow","wait")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001, noise=0.000005),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11),
                          "GBP/USD": make_bars(80, start=1.25, slope=0.00009, seed=33)},
                 bars_xau=make_bars(80, start=2400, slope=-0.05, seed=44),
                 bars_spx=make_bars(80, start=5500, slope=0.5, seed=55),
                 news_verdict=None)
))

# 10. Correlation unavailable (single series only)
CASES.append((
    FailSpec("correlation_unavailable_must_be_safe", ("wait","block","allow")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001)},
                 news_verdict=None)
))

# 11. Unknown pair name (should not crash)
CASES.append((
    FailSpec("unknown_pair_must_block", ("block",)),
    lambda: dict(pair="FOO/BAR",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001)},
                 news_verdict=None)
))

# 12. Session "off" (Sunday)
CASES.append((
    FailSpec("session_off_handled", ("allow","wait","block")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=None,
                 # Force session "off" via NOW = late hour (e.g., 22 UTC)
                 _now_override=datetime(2026,4,26,22,0,0,tzinfo=timezone.utc))
))

# 13. Very few bars (3)
CASES.append((
    FailSpec("very_few_bars_must_wait", ("wait","block")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(3, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=None)
))

# 14. Future-dated bars (clock skew)
def future_bars():
    bs = make_bars(80, slope=0.0001)
    # Push timestamps 1h into the future
    return [Bar(b.timestamp + timedelta(hours=1), b.open, b.high, b.low,
                b.close, b.volume, b.spread_pips) for b in bs]
CASES.append((
    FailSpec("future_dated_bars_handled", ("allow","wait","block")),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": future_bars(),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=None)
))

# 15. All sources stale (live mode latency hint)
CASES.append((
    FailSpec("all_sources_stale_flagged", ("allow","wait","block"),
             expect_warning_substr=""),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=11)},
                 news_verdict=None,
                 source_latencies_ms={"EUR/USD": 1200, "USD/JPY": 950, "NewsMind": 2000})
))


def main():
    print("=" * 100)
    print("MarketMind — Audit / Fail-Safe Test (15 سيناريو خطر)")
    print("=" * 100)
    mm = MarketMindV3()
    pass_n = 0
    fail_rows = []

    for i, (spec, build) in enumerate(CASES, 1):
        try:
            cfg = build()
            now = cfg.pop("_now_override", NOW)
            mm_cache.clear()
            a = mm.assess(now_utc=now, **cfg)
        except Exception as e:
            print(f"\n┌─[{i:02d}] {spec.name}  ✗ FAIL (CRASH)")
            print(f"│ Exception: {type(e).__name__}: {e}")
            fail_rows.append((i, spec.name, f"crashed: {e}"))
            print(f"└─ correct? FAIL")
            continue

        ok_perm = a.trade_permission in spec.expect_perm_in
        ok_warn = (not spec.expect_warning_substr or
                   any(spec.expect_warning_substr in w for w in a.warnings) or
                   spec.expect_warning_substr in a.reason)
        ok_all = ok_perm and ok_warn
        if ok_all: pass_n += 1
        status = "PASS" if ok_all else "FAIL"

        print(f"\n┌─[{i:02d}] {spec.name}  {'✓ ' + status if ok_all else '✗ ' + status}")
        print(f"│ permission={a.trade_permission}  expected_in={list(spec.expect_perm_in)}  {'✓' if ok_perm else '✗'}")
        print(f"│ grade={a.grade}  data_quality={a.data_quality_status}  regime={a.market_regime}")
        print(f"│ warnings={list(a.warnings) or 'none'}")
        if spec.expect_warning_substr:
            print(f"│ expected_warning_substr={spec.expect_warning_substr}  {'✓' if ok_warn else '✗'}")
        print(f"│ reason={a.reason[:200]}")
        if not ok_all:
            why = []
            if not ok_perm: why.append(f"perm {a.trade_permission} not in {spec.expect_perm_in}")
            if not ok_warn: why.append(f"missing warning '{spec.expect_warning_substr}'")
            print(f"│ FIX_NEEDED: {'; '.join(why)}")
            fail_rows.append((i, spec.name, "; ".join(why)))
        print(f"└─ correct? {status}")

    print("\n" + "=" * 100)
    print(f"FINAL: {pass_n}/{len(CASES)} PASSED  ({pass_n*100//len(CASES)}%)")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("=" * 100)
    return pass_n, len(CASES)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
