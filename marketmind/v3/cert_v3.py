# -*- coding: utf-8 -*-
"""MarketMind v3 — 12-scenario certification test.

Each scenario produces:
    INPUT       which baskets / what pattern
    REGIME      trend / range / choppy / breakout / ...
    BIASES      dollar / counter / risk / news_alignment
    SPREAD/VOL  liquidity / spread / volatility / data_quality
    CORRELATION normal / broken
    GRADE       A+/A/B/C
    PERMISSION  allow / wait / block
    REASON      machine-readable rationale
    CORRECT?    PASS/FAIL vs ground truth
"""
from __future__ import annotations
import sys, math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from marketmind.v3 import MarketMindV3, Bar


NOW = datetime(2026,4,25,14,0,0,tzinfo=timezone.utc)


def make_bars(n=80, start=1.10, slope=0.0, noise=0.0001, vol=1000, spread=0.5,
              ts_step=15, seed=None):
    """Generate `n` bars going back from NOW."""
    out = []
    import random
    rng = random.Random(seed if seed is not None else abs(hash((start, slope, noise))) % (2**31))
    last_close = start
    for i in range(n):
        ts = NOW - timedelta(minutes=ts_step*(n-i))
        target = start + slope * i
        rnd = (rng.random() - 0.5) * 2 * noise
        c = target + rnd
        rg = max(noise, abs(c - last_close)) * 1.2
        h = max(c, last_close) + rg/2
        l = min(c, last_close) - rg/2
        o = last_close + (c - last_close) * 0.3
        out.append(Bar(timestamp=ts, open=o, high=h, low=l, close=c,
                       volume=vol, spread_pips=spread))
        last_close = c
    return out


def choppy_bars(n=80, base=1.10, amplitude=0.0008):
    """Sideways with frequent reversals."""
    out = []
    last = base
    for i in range(n):
        ts = NOW - timedelta(minutes=15*(n-i))
        # Alternate up-down to maximize whipsaws
        sign = 1 if (i % 2 == 0) else -1
        c = base + sign * amplitude * ((i % 4) / 3)
        h = max(c, last) + amplitude*0.3
        l = min(c, last) - amplitude*0.3
        o = last
        out.append(Bar(timestamp=ts, open=o, high=h, low=l, close=c,
                       volume=1000, spread_pips=0.5))
        last = c
    return out


def range_bars(n=80, base=1.10, amp=0.00025):
    out = []
    last = base
    for i in range(n):
        ts = NOW - timedelta(minutes=15*(n-i))
        # smooth oscillation
        c = base + amp * math.sin(i / 2.5)  # higher freq = less trend-like
        h = max(c, last) + amp*0.2
        l = min(c, last) - amp*0.2
        o = last
        out.append(Bar(timestamp=ts, open=o, high=h, low=l, close=c,
                       volume=900, spread_pips=0.6))
        last = c
    return out


def spike_bars(n=80, base=1.10):
    """Calm then a 5x ATR spike on the last bar."""
    out = make_bars(n-1, start=base, slope=0, noise=0.0001)
    last_close = out[-1].close
    spike = last_close + 0.005    # ~50 pips
    ts = NOW
    out.append(Bar(timestamp=ts, open=last_close, high=spike, low=last_close-0.0003,
                   close=spike, volume=4000, spread_pips=2.5))
    return out


def wide_spread_bars(n=80, base=1.10):
    bs = make_bars(n, start=base, slope=0, noise=0.0001, spread=0.5)
    bs[-1] = Bar(timestamp=bs[-1].timestamp, open=bs[-1].open, high=bs[-1].high,
                 low=bs[-1].low, close=bs[-1].close, volume=bs[-1].volume,
                 spread_pips=4.0)   # dangerous
    return bs


def low_volume_bars(n=80, base=1.10):
    bs = []
    for i, b in enumerate(make_bars(n, start=base, slope=0, noise=0.0001)):
        bs.append(Bar(timestamp=b.timestamp, open=b.open, high=b.high, low=b.low,
                      close=b.close, volume=0 if i >= n-10 else b.volume,
                      spread_pips=b.spread_pips))
    return bs


def gap_bars(n=80, base=1.10):
    bs = make_bars(n, start=base, slope=0, noise=0.0001)
    # Insert two gaps in last 5 bars
    bs[-3] = Bar(bs[-3].timestamp, open=bs[-3].open + 0.01, high=bs[-3].high+0.01,
                 low=bs[-3].low+0.01, close=bs[-3].close+0.01, volume=bs[-3].volume,
                 spread_pips=bs[-3].spread_pips)
    bs[-2] = Bar(bs[-2].timestamp, open=bs[-2].open - 0.012, high=bs[-2].high-0.012,
                 low=bs[-2].low-0.012, close=bs[-2].close-0.012, volume=bs[-2].volume,
                 spread_pips=bs[-2].spread_pips)
    return bs


# Mock NewsMind verdicts
class MockVerdict:
    def __init__(self, trade_permission="allow", market_bias="unclear",
                 risk_mode="unclear", reason=""):
        self.trade_permission = trade_permission
        self.market_bias = market_bias
        self.risk_mode = risk_mode
        self.reason = reason


@dataclass
class Spec:
    name: str
    expect_perm: str
    expect_grade_max: str
    expect_grade_min: str = "C"
    expect_regime: str = ""
    expect_warning_substr: str = ""

GRADE = {"A+":4,"A":3,"B":2,"C":1,"":0}
def in_range(g, lo, hi): return GRADE[lo] <= GRADE[g] <= GRADE[hi]


SCENARIOS = []

# 1: Clean trend with correlated USD/JPY (down) + good news
SCENARIOS.append((
    Spec("trend_clean_aligned", "allow", "A+", "A", "trend", ""),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": make_bars(80, start=1.10, slope=0.00012, noise=0.00005, spread=0.5),
            "USD/JPY": make_bars(80, start=150, slope=-0.012, noise=0.005, spread=0.8),
            "GBP/USD": make_bars(80, start=1.25, slope=0.00010, noise=0.00005, spread=0.7),
        },
        news_verdict=MockVerdict("allow", "bullish", "risk_on", "fresh_verified"),
    )
))

# 2: Range market + no news
SCENARIOS.append((
    Spec("range_no_news", "wait", "B", "C", "range"),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": range_bars(80),
            "USD/JPY": range_bars(80, base=150, amp=0.05),
            "GBP/USD": range_bars(80, base=1.25),
        },
        news_verdict=MockVerdict("allow", "neutral", "unclear"),
    )
))

# 3: Choppy market
SCENARIOS.append((
    Spec("choppy_dangerous", "wait", "C", "C", "choppy"),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": choppy_bars(80),
            "USD/JPY": choppy_bars(80, base=150, amplitude=0.05),
            "GBP/USD": choppy_bars(80, base=1.25),
        },
        news_verdict=None,
    )
))

# 4: Pre-news block (NewsMind blocks)
SCENARIOS.append((
    Spec("pre_news_blocked", "block", "C", "C", ""),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": make_bars(80, start=1.10, slope=0.0001, noise=0.0001),
            "USD/JPY": make_bars(80, start=150, slope=-0.01, noise=0.005),
        },
        news_verdict=MockVerdict("block", "unclear", "unclear",
                                 "scheduled_high_impact_NFP_pre_window"),
    )
))

# 5: Post-news with NewsMind=wait
SCENARIOS.append((
    Spec("post_news_wait", "wait", "B"),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": make_bars(80, start=1.10, slope=0.0001, noise=0.0001),
            "USD/JPY": make_bars(80, start=150, slope=-0.005),
        },
        news_verdict=MockVerdict("wait", "bullish", "unclear", "post_event_cooldown"),
    )
))

# 6: Risk-off with USD/JPY rising (broken haven)
SCENARIOS.append((
    Spec("riskoff_usdjpy_rising_dangerous", "wait", "C", "C", "",
         expect_warning_substr="risk_off"),
    lambda: dict(
        pair="USD/JPY",
        baskets={
            "EUR/USD": make_bars(80, start=1.10, slope=-0.00005),
            "USD/JPY": make_bars(80, start=150, slope=0.020, noise=0.01),
            "GBP/USD": make_bars(80, start=1.25, slope=-0.00005),
        },
        news_verdict=MockVerdict("wait", "bearish", "risk_off",
                                 "fresh_verified|risk_off_caution"),
    )
))

# 7: Broken correlation (EUR/USD UP + USD/JPY UP — inconsistent USD)
# With minimal noise so slope dominates returns and correlation anomaly fires.
SCENARIOS.append((
    Spec("dxy_correlation_break", "wait", "B", "C", ""),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": make_bars(80, start=1.10, slope=0.00020, noise=0.000005),
            "USD/JPY": make_bars(80, start=150, slope=0.025,    noise=0.0001),
            "GBP/USD": make_bars(80, start=1.25, slope=0.00018, noise=0.000005),
        },
        news_verdict=None,
    )
))

# 8: Wide spread (dangerous)
SCENARIOS.append((
    Spec("spread_dangerous", "block", "C", "C", ""),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": wide_spread_bars(80),
            "USD/JPY": make_bars(80, start=150, slope=-0.01),
        },
        news_verdict=None,
    )
))

# 9: Volatility extreme (spike)
SCENARIOS.append((
    Spec("volatility_spike", "wait", "C", "C", "high_volatility"),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": spike_bars(80),
            "USD/JPY": make_bars(80, start=150, slope=-0.01),
        },
        news_verdict=None,
    )
))

# 10: Data missing (very few bars)
SCENARIOS.append((
    Spec("data_missing_short_history", "wait", "C", "C", ""),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": make_bars(3, start=1.10, slope=0.0001),
            "USD/JPY": make_bars(80, start=150, slope=-0.01),
        },
        news_verdict=None,
    )
))

# 11: Low volume / poor liquidity
SCENARIOS.append((
    Spec("low_liquidity_session", "wait", "B", "C", ""),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": low_volume_bars(80),
            "USD/JPY": make_bars(80, start=150, slope=-0.01),
        },
        news_verdict=None,
    )
))

# 12: Unexplained gaps in price action
SCENARIOS.append((
    Spec("unexplained_gaps", "wait", "C", "C", "",
         expect_warning_substr="gap"),
    lambda: dict(
        pair="EUR/USD",
        baskets={
            "EUR/USD": gap_bars(80),
            "USD/JPY": make_bars(80, start=150, slope=-0.01),
        },
        news_verdict=None,
    )
))


def main():
    print("=" * 100)
    print("MarketMind V3 — Certification Test (12 سيناريو)")
    print("=" * 100)
    mm = MarketMindV3()
    pass_n = 0
    fail_rows = []
    for i, (spec, build) in enumerate(SCENARIOS, 1):
        cfg = build()
        a = mm.assess(now_utc=NOW, **cfg)

        ok_perm = a.trade_permission == spec.expect_perm
        ok_grade = in_range(a.grade, spec.expect_grade_min, spec.expect_grade_max)
        ok_regime = (not spec.expect_regime) or a.market_regime == spec.expect_regime
        ok_warn = (not spec.expect_warning_substr
                   or any(spec.expect_warning_substr in w for w in a.warnings)
                   or spec.expect_warning_substr in a.reason)
        ok_all = ok_perm and ok_grade and ok_regime and ok_warn
        if ok_all: pass_n += 1
        status = "PASS" if ok_all else "FAIL"

        print(f"\n┌─[{i:02d}] {spec.name}  {'✓ ' + status if ok_all else '✗ ' + status}")
        print(f"│ INPUT      : pair={a.pair}  bars={len(cfg['baskets'].get(a.pair, []))} "
              f"news={('yes:'+cfg['news_verdict'].trade_permission) if cfg['news_verdict'] else 'none'}")
        print(f"│ REGIME     : got={a.market_regime}  direction={a.direction}  "
              f"strength={a.trend_strength:.2f}  expect_regime={spec.expect_regime or 'any'}  {'✓' if ok_regime else '✗'}")
        print(f"│ BIASES     : dollar={a.dollar_bias}  counter={a.counter_currency_bias}  "
              f"risk={a.risk_mode}  news_align={a.news_alignment}")
        print(f"│ MARKET     : vol={a.volatility_level}  liq={a.liquidity_condition}  "
              f"spread={a.spread_condition}  corr={a.correlation_status}")
        print(f"│ DATA Q     : {a.data_quality_status}")
        print(f"│ GRADE      : got={a.grade}  expect=[{spec.expect_grade_min}..{spec.expect_grade_max}]  {'✓' if ok_grade else '✗'}")
        print(f"│ PERMISSION : got={a.trade_permission}  expect={spec.expect_perm}  {'✓' if ok_perm else '✗'}")
        print(f"│ ENVIRONMENT: {a.trade_environment}")
        print(f"│ WARNINGS   : {a.warnings or '()'}")
        print(f"│ REASON     : {a.reason[:200]}")
        if not ok_all:
            why = []
            if not ok_perm: why.append(f"perm {a.trade_permission}!={spec.expect_perm}")
            if not ok_grade: why.append(f"grade {a.grade} not in [{spec.expect_grade_min}..{spec.expect_grade_max}]")
            if not ok_regime: why.append(f"regime {a.market_regime}!={spec.expect_regime}")
            if not ok_warn: why.append(f"missing warning {spec.expect_warning_substr}")
            print(f"│ FIX_NEEDED : {'; '.join(why)}")
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
