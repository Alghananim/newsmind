# -*- coding: utf-8 -*-
"""MarketMind V3 — Final Certification Test (17 سيناريو من قائمة المستخدم).

For each scenario produces the full row format the user requested:
    INPUT
    REGIME / DIRECTION / STRENGTH
    BIASES (dollar / counter / risk_mode / news_alignment)
    MARKET (vol / liq / spread / corr)
    DATA QUALITY
    CROSS-MARKET (confirmation / contradictions)
    SCORES (trend / vol / liq / dq / mi / speed)
    LATENCY (decision_ms / data_ms / bottleneck)
    GRADE / PERMISSION / REASON
    CORRECT? (PASS/FAIL vs ground truth)
"""
from __future__ import annotations
import sys, time
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from marketmind.v3 import MarketMindV3, Bar, cache as mm_cache


NOW = datetime(2026,4,25,14,0,0,tzinfo=timezone.utc)
TARGET_LATENCY_MS = 50


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


def choppy_bars(n=80, base=1.10):
    return [Bar(timestamp=NOW - timedelta(minutes=15*(n-i)),
                open=base + (-1)**i * 0.0003,
                high=base + (-1)**i * 0.0003 + 0.0001,
                low=base + (-1)**i * 0.0003 - 0.0001,
                close=base + (-1)**(i+1) * 0.0003,
                volume=1000, spread_pips=0.5)
            for i in range(n)]


def range_bars(n=80, base=1.10, amp=0.00025):
    import math
    out = []
    last = base
    for i in range(n):
        c = base + amp * math.sin(i / 2.5)
        h = max(c, last) + amp*0.2
        l = min(c, last) - amp*0.2
        o = last
        out.append(Bar(timestamp=NOW - timedelta(minutes=15*(n-i)),
                       open=o, high=h, low=l, close=c,
                       volume=900, spread_pips=0.6))
        last = c
    return out


def fake_breakout_bars(n=80, base=1.10):
    bs = make_bars(n-4, start=base, slope=0.0001) + [
        Bar(timestamp=NOW - timedelta(minutes=15*3), open=base+0.001, high=base+0.011,
            low=base+0.001, close=base+0.010, volume=2000, spread_pips=0.5),
        Bar(timestamp=NOW - timedelta(minutes=15*2), open=base+0.010, high=base+0.011,
            low=base+0.003, close=base+0.004, volume=1500, spread_pips=0.5),
        Bar(timestamp=NOW - timedelta(minutes=15*1), open=base+0.004, high=base+0.005,
            low=base+0.001, close=base+0.002, volume=1500, spread_pips=0.5),
        Bar(timestamp=NOW, open=base+0.002, high=base+0.003, low=base-0.002,
            close=base-0.001, volume=1800, spread_pips=0.5),
    ]
    return bs


def spike_bars(n=80, base=1.10):
    bs = make_bars(n-1, start=base, slope=0)
    last = bs[-1].close
    bs.append(Bar(NOW, open=last, high=last+0.01, low=last-0.001,
                  close=last+0.01, volume=4000, spread_pips=2.5))
    return bs


def wide_spread_bars(n=80, base=1.10):
    bs = make_bars(n, start=base, slope=0.0001)
    bs[-1] = Bar(bs[-1].timestamp, bs[-1].open, bs[-1].high, bs[-1].low,
                 bs[-1].close, bs[-1].volume, spread_pips=5.0)
    return bs


class MockNews:
    def __init__(self, perm="allow", bias="unclear", risk="unclear", reason=""):
        self.trade_permission = perm
        self.market_bias = bias
        self.risk_mode = risk
        self.reason = reason


@dataclass
class Spec:
    name: str
    expect_perm: str           # acceptable answer (or "any" for permissive)
    expect_grade_max: str = "A+"
    expect_grade_min: str = "C"
    expect_warning_substr: str = ""
    expect_min_intelligence: float = 0.0


GRADE_RANK = {"A+":4,"A":3,"B":2,"C":1,"":0}
def grade_in_range(g, lo, hi): return GRADE_RANK[lo] <= GRADE_RANK[g] <= GRADE_RANK[hi]


SCENARIOS = []

# 1: EUR/USD trend واضح
SCENARIOS.append((
    Spec("eurusd_trend_clean", "allow", "A+", "A", "", 0.55),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.00012, noise=0.000008),
                          "USD/JPY": make_bars(80, start=150, slope=-0.012, noise=0.001, seed=11),
                          "GBP/USD": make_bars(80, start=1.25, slope=0.00010, seed=22)},
                 bars_xau=make_bars(80, start=2400, slope=-0.05, seed=33),
                 bars_spx=make_bars(80, start=5500, slope=0.5, seed=44),
                 news_verdict=MockNews("allow", "bullish", "risk_on", "fresh_verified"))
))

# 2: EUR/USD choppy
SCENARIOS.append((
    Spec("eurusd_choppy", "wait", "C", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": choppy_bars(),
                          "USD/JPY": make_bars(80, start=150, slope=0, noise=0.05, seed=22)},
                 news_verdict=None)
))

# 3: USD/JPY مع risk-off قوي
SCENARIOS.append((
    Spec("usdjpy_strong_riskoff", "wait", "C", "C", expect_warning_substr="haven_violated"),
    lambda: dict(pair="USD/JPY",
                 baskets={"EUR/USD": make_bars(80, slope=-0.00005),
                          "USD/JPY": make_bars(80, start=150, slope=0.025, noise=0.005, seed=55),
                          "GBP/USD": make_bars(80, start=1.25, slope=-0.00005, seed=66)},
                 bars_spx=make_bars(80, start=5500, slope=-3, noise=2, seed=77),
                 bars_xau=make_bars(80, start=2400, slope=1.5, noise=1, seed=88),
                 news_verdict=MockNews("wait", "bearish", "risk_off", "fresh_verified|risk_off_caution"))
))

# 4: USD/JPY مع yields صاعدة (proxy: USD/JPY trending up, dollar strong)
SCENARIOS.append((
    Spec("usdjpy_yields_rising_aligned", "allow", "A+", "B"),
    lambda: dict(pair="USD/JPY",
                 baskets={"EUR/USD": make_bars(80, slope=-0.00010, seed=11),
                          "USD/JPY": make_bars(80, start=150, slope=0.012, noise=0.003, seed=22),
                          "GBP/USD": make_bars(80, start=1.25, slope=-0.00008, seed=33)},
                 news_verdict=MockNews("allow", "bullish", "unclear", "fresh_verified"))
))

# 5: DXY صاعد و EUR/USD صاعد بدون سبب واضح
SCENARIOS.append((
    Spec("dxy_up_eurusd_up_inconsistent", "wait", "B", "C", expect_warning_substr="inconsistent_usd"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.00018, noise=0.000005),
                          "USD/JPY": make_bars(80, start=150, slope=0.025, noise=0.0001, seed=99),
                          "GBP/USD": make_bars(80, start=1.25, slope=0.00015, seed=88)},
                 news_verdict=None)
))

# 6: DXY هابط و EUR/USD لا يستجيب (DXY weak but EUR/USD flat)
SCENARIOS.append((
    Spec("dxy_down_eurusd_flat_divergence", "wait", "B", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0, noise=0.000003, seed=10),
                          "USD/JPY": make_bars(80, start=150, slope=-0.020, noise=0.0001, seed=11),
                          "GBP/USD": make_bars(80, start=1.25, slope=0.00018, seed=12)},
                 news_verdict=None)
))

# 7: yields صاعدة (USD up) و USD/JPY لا يستجيب
SCENARIOS.append((
    Spec("yields_up_usdjpy_flat", "wait", "B", "C"),
    lambda: dict(pair="USD/JPY",
                 baskets={"EUR/USD": make_bars(80, slope=-0.00012, noise=0.000005, seed=20),
                          "USD/JPY": make_bars(80, start=150, slope=0.0, noise=0.001, seed=21),
                          "GBP/USD": make_bars(80, start=1.25, slope=-0.00010, seed=22)},
                 news_verdict=None)
))

# 8: الذهب والدولار يتحركان معاً (abnormal regime)
SCENARIOS.append((
    Spec("gold_and_dollar_both_up_abnormal", "wait", "B", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=-0.00012, noise=0.000005, seed=30),
                          "USD/JPY": make_bars(80, start=150, slope=0.018, noise=0.001, seed=31),
                          "GBP/USD": make_bars(80, start=1.25, slope=-0.00010, seed=32)},
                 bars_xau=make_bars(80, start=2400, slope=2.0, noise=0.5, seed=33),  # gold rising hard
                 news_verdict=None)
))

# 9: spread عالي (5 pips على EUR/USD)
SCENARIOS.append((
    Spec("spread_dangerous", "block", "C", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": wide_spread_bars(),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=40)},
                 news_verdict=None)
))

# 10: volatility extreme (last bar ATR spike)
SCENARIOS.append((
    Spec("volatility_extreme", "wait", "C", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": spike_bars(),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=50)},
                 news_verdict=None)
))

# 11: بيانات ناقصة (3 bars only)
SCENARIOS.append((
    Spec("data_missing", "wait", "C", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(3, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=60)},
                 news_verdict=None)
))

# 12: source متأخر (>500ms على ≥50% sources)
SCENARIOS.append((
    Spec("sources_stale", "wait", "C", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=70)},
                 news_verdict=None,
                 source_latencies_ms={"EUR/USD": 1200, "USD/JPY": 950, "NewsMind": 2000})
))

# 13: وقت خارج التداول (Sunday 22 UTC, session=off)
SCENARIOS.append((
    Spec("session_off_handled", "any"),    # Engine decides; MarketMind doesn't enforce session
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=80)},
                 news_verdict=None,
                 _now_override=datetime(2026,4,26,22,0,0,tzinfo=timezone.utc))
))

# 14: pair غير معروف
SCENARIOS.append((
    Spec("unknown_pair", "block", "C", "C"),
    lambda: dict(pair="ZZZ/YYY",
                 baskets={"EUR/USD": make_bars(80, slope=0.0001)},
                 news_verdict=None)
))

# 15: حركة سريعة لكن غير قابلة للتنفيذ (chase risk + news allow)
SCENARIOS.append((
    Spec("rapid_move_chase_risk", "wait", "B", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": make_bars(70, slope=0, noise=0.000005, seed=90) + [
                     Bar(timestamp=NOW - timedelta(minutes=15*(10-i)),
                         open=1.10 + 0.0003*i, high=1.10 + 0.0003*(i+1),
                         low=1.10 + 0.0003*(i-0.2), close=1.10 + 0.0003*(i+0.8),
                         volume=2000, spread_pips=0.5)
                     for i in range(10)
                 ],
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=91)},
                 news_verdict=MockNews("allow", "bullish", "unclear", "fresh_verified"))
))

# 16: fake breakout (broke up, then reversed)
SCENARIOS.append((
    Spec("fake_breakout", "wait", "C", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": fake_breakout_bars(),
                          "USD/JPY": make_bars(80, start=150, slope=-0.01, seed=100)},
                 news_verdict=None)
))

# 17: range ضيق لا يستحق التداول
SCENARIOS.append((
    Spec("range_narrow_no_edge", "wait", "B", "C"),
    lambda: dict(pair="EUR/USD",
                 baskets={"EUR/USD": range_bars(),
                          "USD/JPY": range_bars(base=150, amp=0.05)},
                 news_verdict=MockNews("allow", "neutral", "unclear", "no_blocking_news"))
))


def main():
    print("=" * 100)
    print("MarketMind V3 — Final Acceptance Certification (17 سيناريو)")
    print("=" * 100)

    mm = MarketMindV3()
    pass_n = 0
    fail_rows = []
    cold_lats, warm_lats = [], []

    for i, (spec, build) in enumerate(SCENARIOS, 1):
        cfg = build()
        now = cfg.pop("_now_override", NOW)

        # cold + warm latency
        mm_cache.clear()
        t0 = time.perf_counter_ns()
        a = mm.assess(now_utc=now, **cfg)
        cold_ms = (time.perf_counter_ns() - t0) / 1e6
        cold_lats.append(cold_ms)

        t0 = time.perf_counter_ns()
        a = mm.assess(now_utc=now, **cfg)   # second pass uses cache
        warm_ms = (time.perf_counter_ns() - t0) / 1e6
        warm_lats.append(warm_ms)

        # Validate
        ok_perm = (spec.expect_perm == "any") or (a.trade_permission == spec.expect_perm)
        ok_grade = grade_in_range(a.grade, spec.expect_grade_min, spec.expect_grade_max)
        ok_warn = (not spec.expect_warning_substr) or any(
            spec.expect_warning_substr in w for w in a.warnings
        ) or spec.expect_warning_substr in a.reason
        ok_intel = a.market_intelligence_score >= spec.expect_min_intelligence
        ok_lat = a.decision_latency_ms <= TARGET_LATENCY_MS
        ok_all = ok_perm and ok_grade and ok_warn and ok_intel and ok_lat
        if ok_all: pass_n += 1
        status = "PASS" if ok_all else "FAIL"

        print(f"\n┌─[{i:02d}] {spec.name}  {'✓ ' + status if ok_all else '✗ ' + status}")
        print(f"│ INPUT       : pair={a.pair}  bars={len(cfg['baskets'].get(a.pair, []))} "
              f"news={'yes:'+cfg['news_verdict'].trade_permission if cfg['news_verdict'] else 'none'}  "
              f"session={a.session}")
        print(f"│ REGIME      : got={a.market_regime}  direction={a.direction}  trend_strength={a.trend_strength:.2f}")
        print(f"│ BIASES      : dollar={a.dollar_bias}  counter={a.counter_currency_bias}  "
              f"risk={a.risk_mode}  news_align={a.news_alignment}")
        print(f"│ MARKET      : vol={a.volatility_level}  liq={a.liquidity_condition}  "
              f"spread={a.spread_condition}  corr={a.correlation_status}")
        print(f"│ DATA Q      : {a.data_quality_status}")
        print(f"│ CROSS-MKT   : confirmation={a.cross_market_confirmation}  "
              f"contradictions={list(a.contradictions_detected) or 'none'}")
        print(f"│ SCORES      : trend={a.trend_score} vol={a.volatility_score} "
              f"liq={a.liquidity_score} dq={a.data_quality_score} "
              f"speed={a.speed_score} mi={a.market_intelligence_score} "
              f"confidence={a.confidence}")
        print(f"│ LATENCY     : decision={a.decision_latency_ms}ms  data={a.data_latency_ms}ms  "
              f"cold={cold_ms:.2f}ms  warm={warm_ms:.2f}ms  bottleneck={a.bottleneck_stage}")
        print(f"│ SOURCES     : used={list(a.sources_used)}  stale={list(a.stale_sources) or 'none'}")
        print(f"│ GRADE       : got={a.grade}  expect=[{spec.expect_grade_min}..{spec.expect_grade_max}]  {'✓' if ok_grade else '✗'}")
        print(f"│ PERMISSION  : got={a.trade_permission}  expect={spec.expect_perm}  {'✓' if ok_perm else '✗'}")
        print(f"│ REASON      : {a.reason[:200]}")
        if not ok_all:
            why = []
            if not ok_perm: why.append(f"perm {a.trade_permission}!={spec.expect_perm}")
            if not ok_grade: why.append(f"grade {a.grade} not in [{spec.expect_grade_min}..{spec.expect_grade_max}]")
            if not ok_warn: why.append(f"missing warning '{spec.expect_warning_substr}'")
            if not ok_intel: why.append(f"intel {a.market_intelligence_score} < {spec.expect_min_intelligence}")
            if not ok_lat: why.append(f"latency {a.decision_latency_ms}ms > {TARGET_LATENCY_MS}ms")
            print(f"│ FIX_NEEDED  : {'; '.join(why)}")
            fail_rows.append((i, spec.name, "; ".join(why)))
        print(f"└─ correct? {status}")

    print("\n" + "=" * 100)
    print(f"FINAL: {pass_n}/{len(SCENARIOS)} PASSED  ({pass_n*100//len(SCENARIOS)}%)")
    print(f"LATENCY AVG : cold={sum(cold_lats)/len(cold_lats):.2f}ms  "
          f"warm={sum(warm_lats)/len(warm_lats):.2f}ms")
    print(f"LATENCY MAX : cold={max(cold_lats):.2f}ms  warm={max(warm_lats):.2f}ms")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("=" * 100)
    return pass_n, len(SCENARIOS)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
