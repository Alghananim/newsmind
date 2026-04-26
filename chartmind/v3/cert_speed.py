# -*- coding: utf-8 -*-
"""ChartMind V4 — Speed + Intelligence Cert (15 سيناريو من قائمة المستخدم).

Each scenario produces:
    INPUT
    LATENCY breakdown (per stage)
    INTELLIGENCE scores
    DECISION (grade / permission / reason)
    CORRECT? (PASS/FAIL)
"""
from __future__ import annotations
import sys, time, math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from chartmind.v3 import ChartMindV3, Bar, cache as cm_cache


NOW = datetime(2026,4,25,14,0,0,tzinfo=timezone.utc)
TARGET_LATENCY_MS = 50


def make(n=80, slope=0.0, noise=0.0001, seed=42, base=1.10):
    import random
    rng = random.Random(seed)
    out = []; last = base
    for i in range(n):
        c = base + slope*i + (rng.random()-0.5)*2*noise
        rg = max(noise, abs(c-last)) * 1.2
        h = max(c,last)+rg/2; l = min(c,last)-rg/2; o = last + (c-last)*0.3
        out.append(Bar(NOW-timedelta(minutes=15*(n-i)), o, h, l, c, 1000, 0.5))
        last = c
    return out


def real_breakout_bars():
    bs = []
    # Build resistance with multiple touches around 1.1010
    for i in range(60):
        bs.append(Bar(NOW-timedelta(minutes=15*(80-i)), 1.099, 1.1010, 1.099, 1.0995, 900, 0.5))
    # Then a strong break
    last = bs[-1].close
    for k, m in enumerate([0.0006, 0.0010, 0.0015]):
        bs.append(Bar(NOW-timedelta(minutes=15*(20-k)), last, last+m, last-0.0001, last+m*0.95, 2000, 0.5))
        last += m * 0.95
    # Pullback to retest
    bs.append(Bar(NOW-timedelta(minutes=15*16), last, last+0.0002, last-0.0008, 1.1012, 1500, 0.5))
    bs.append(Bar(NOW-timedelta(minutes=15*15), 1.1012, 1.1014, 1.1011, 1.1013, 1500, 0.5))
    # Continuation
    last = 1.1013
    for k in range(15):
        c = last + 0.00005
        bs.append(Bar(NOW-timedelta(minutes=15*(14-k)), last, c+0.00008, last-0.00005, c, 1000, 0.5))
        last = c
    return bs


def fake_breakout_bars():
    """Range, breakout up, then close back below within 2 bars."""
    bs = []
    for i in range(75):
        bs.append(Bar(NOW-timedelta(minutes=15*(80-i)), 1.099, 1.1010, 1.099, 1.0995, 900, 0.5))
    bs.append(Bar(NOW-timedelta(minutes=15*5), 1.0995, 1.1025, 1.0995, 1.1020, 2500, 0.5))
    bs.append(Bar(NOW-timedelta(minutes=15*4), 1.1020, 1.1022, 1.0998, 1.1000, 2000, 0.5))
    bs.append(Bar(NOW-timedelta(minutes=15*3), 1.1000, 1.1002, 1.0985, 1.0987, 1800, 0.5))
    bs.append(Bar(NOW-timedelta(minutes=15*2), 1.0987, 1.0990, 1.0982, 1.0985, 1500, 0.5))
    bs.append(Bar(NOW-timedelta(minutes=15*1), 1.0985, 1.0988, 1.0980, 1.0983, 1500, 0.5))
    bs.append(Bar(NOW, 1.0983, 1.0985, 1.0978, 1.0982, 1500, 0.5))
    return bs


def chase_bars():
    bs = make(75, slope=0.00010, seed=42)
    last = bs[-1].close
    for k, m in enumerate([0.0005, 0.0008, 0.0014, 0.0020, 0.0025]):
        bs.append(Bar(NOW-timedelta(minutes=15*(4-k)), last, last+m, last-0.00003,
                     last+m*0.95, 2000, 0.5))
        last += m * 0.95
    return bs


def pin_at_support_bars():
    bs = make(78, slope=-0.00010, seed=42)
    last = bs[-1].close
    bs.append(Bar(NOW-timedelta(minutes=15), last, last+0.0001, last-0.0003, last-0.0002, 1500, 0.5))
    # Hammer: low wick, close near top
    bs.append(Bar(NOW, last-0.0002, last-0.00005, last-0.0010, last-0.0002, 2200, 0.5))
    return bs


def pin_midrange_bars():
    bs = []
    last = 1.10
    for i in range(78):
        c = 1.10 + math.sin(i/5.0) * 0.0005
        bs.append(Bar(NOW-timedelta(minutes=15*(80-i)), last, c+0.0001, c-0.0001, c, 900, 0.5))
        last = c
    bs.append(Bar(NOW-timedelta(minutes=15), last, last+0.0001, last-0.0001, last+0.00005, 1000, 0.5))
    bs.append(Bar(NOW, last+0.00005, last+0.0001, last-0.0008, last+0.00003, 1500, 0.5))
    return bs


def trend_then_weakening():
    bs = make(60, slope=0.00012, seed=42)
    # Trend continues but pullbacks deepening
    last = bs[-1].close
    for k in range(20):
        # Each pullback bigger than the last
        if k % 3 == 0:
            bs.append(Bar(NOW-timedelta(minutes=15*(20-k)), last, last-0.0002,
                         last-0.0005, last-0.0004, 1500, 0.5))
            last -= 0.0004
        else:
            bs.append(Bar(NOW-timedelta(minutes=15*(20-k)), last, last+0.0001,
                         last-0.0001, last+0.00005, 1000, 0.5))
            last += 0.00005
    return bs


def choppy_bars():
    return [Bar(NOW-timedelta(minutes=15*(80-i)),
                1.10 + (-1)**i * 0.0003,
                1.10 + (-1)**i * 0.0003 + 0.00015,
                1.10 + (-1)**i * 0.0003 - 0.00015,
                1.10 + (-1)**(i+1) * 0.0003, 1000, 0.5)
            for i in range(80)]


def huge_atr_bars():
    bs = make(79, slope=0.00005)
    last = bs[-1].close
    bs.append(Bar(NOW, last, last+0.015, last-0.005, last+0.012, 4000, 2.0))
    return bs


def low_atr_bars():
    return [Bar(NOW-timedelta(minutes=15*(80-i)),
                1.10, 1.10+0.000003, 1.10-0.000003, 1.10, 900, 0.5)
            for i in range(80)]


def mtf_conflict_bars():
    return dict(
        pair="EUR/USD",
        bars_m15=make(80, slope=-0.00015, seed=10),
        bars_m5=make(60, slope=0.00010, seed=20),
        bars_m1=make(40, slope=0.00012, seed=30),
    )


@dataclass
class Spec:
    name: str
    expect_perm: str           # any/allow/wait/block
    max_latency_ms: float = TARGET_LATENCY_MS
    expect_min_intelligence: float = 0.0


SCENARIOS = []

# 1: real breakout (clean, with retest + continuation)
SCENARIOS.append((Spec("01_real_breakout_fast", "any", 5.0, 0.30),
    lambda: dict(pair="EUR/USD", bars_m15=real_breakout_bars())))

# 2: fake breakout
SCENARIOS.append((Spec("02_fake_breakout_fast", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=fake_breakout_bars())))

# 3: late entry / chase
SCENARIOS.append((Spec("03_late_entry_chase", "block", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=chase_bars())))

# 4: candle قوية في مكان خطأ (engulfing midrange)
SCENARIOS.append((Spec("04_candle_in_midrange", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=pin_midrange_bars())))

# 5: pin bar عند دعم حقيقي
SCENARIOS.append((Spec("05_pin_at_real_support", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=pin_at_support_bars())))

# 6: pin bar وسط range
SCENARIOS.append((Spec("06_pin_midrange", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=pin_midrange_bars())))

# 7: fake breakout + reversal
SCENARIOS.append((Spec("07_fake_then_reverse", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=fake_breakout_bars())))

# 8: trend واضح
SCENARIOS.append((Spec("08_clear_trend", "any", 5.0, 0.25),
    lambda: dict(pair="EUR/USD", bars_m15=make(80, slope=0.00012, noise=0.000005))))

# 9: trend بدأ يضعف
SCENARIOS.append((Spec("09_trend_weakening", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=trend_then_weakening())))

# 10: سوق choppy
SCENARIOS.append((Spec("10_choppy", "wait", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=choppy_bars())))

# 11: ATR عالي جداً
SCENARIOS.append((Spec("11_atr_extreme_high", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=huge_atr_bars())))

# 12: ATR منخفض جداً
SCENARIOS.append((Spec("12_atr_extremely_low", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=low_atr_bars())))

# 13: 1M يعطي إشارة و15M يعارض
SCENARIOS.append((Spec("13_mtf_conflict", "block", 5.0),
    lambda: mtf_conflict_bars()))

# 14: SR قريبة تمنع الصفقة (price right at resistance)
def near_resistance_bars():
    bs = []
    for i in range(20):
        bs.append(Bar(NOW-timedelta(minutes=15*(80-i)), 1.10, 1.1010, 1.099, 1.1008, 900, 0.5))
    last = 1.1008
    for i in range(20, 80):
        last += 0.000005
        bs.append(Bar(NOW-timedelta(minutes=15*(80-i)), last-0.00001, last+0.00001, last-0.00002, last, 1000, 0.5))
    return bs
SCENARIOS.append((Spec("14_sr_near_blocks_trade", "any", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=near_resistance_bars())))

# 15: حركة سريعة تنتهي قبل القرار (proxy: chase)
SCENARIOS.append((Spec("15_rapid_move_chase", "block", 5.0),
    lambda: dict(pair="EUR/USD", bars_m15=chase_bars())))


def main():
    print("=" * 100)
    print("ChartMind V4 — Speed + Intelligence Cert (15 سيناريو)")
    print("=" * 100)

    cm = ChartMindV3()
    pass_n = 0
    cold_lats, warm_lats = [], []
    fail_rows = []

    for i, (spec, build) in enumerate(SCENARIOS, 1):
        cfg = build()

        cm_cache.clear()
        t0 = time.perf_counter_ns()
        a_cold = cm.assess(now_utc=NOW, **cfg)
        cold_ms = (time.perf_counter_ns() - t0) / 1e6
        cold_lats.append(cold_ms)

        t0 = time.perf_counter_ns()
        a = cm.assess(now_utc=NOW, **cfg)
        warm_ms = (time.perf_counter_ns() - t0) / 1e6
        warm_lats.append(warm_ms)

        ok_perm = (spec.expect_perm == "any") or (a.trade_permission == spec.expect_perm)
        ok_lat  = a.chart_analysis_latency_ms <= spec.max_latency_ms
        ok_intel = a.chart_intelligence_score >= spec.expect_min_intelligence
        ok_all = ok_perm and ok_lat and ok_intel
        if ok_all: pass_n += 1
        status = "PASS" if ok_all else "FAIL"

        print(f"\n┌─[{i:02d}] {spec.name}  {'✓ '+status if ok_all else '✗ '+status}")
        print(f"│ INPUT       : pair={a.pair}  bars_m15={len(cfg['bars_m15'])} TFs={list(a.timeframes_used)}")
        print(f"│ LATENCY     : total={a.chart_analysis_latency_ms}ms  cold={cold_ms:.3f}ms  warm={warm_ms:.3f}ms")
        print(f"│ STAGES      : data_load={a.data_load_latency_ms} struct={a.structure_analysis_latency_ms} "
              f"feature={a.feature_calc_latency_ms} sr={a.support_resistance_latency_ms}")
        print(f"│              candle={a.candlestick_analysis_latency_ms} break={a.breakout_detection_latency_ms} rr={a.risk_reward_calc_latency_ms}")
        print(f"│ BOTTLENECK  : {a.bottleneck_stage}")
        print(f"│ INTELLIGENCE: chart={a.chart_intelligence_score} entry={a.entry_quality_score} "
              f"mtf={a.timeframe_alignment_score} dq={a.data_quality_score} speed={a.speed_score}")
        print(f"│ STRUCT/TREND: struct={a.market_structure} trend={a.trend_direction}/{a.trend_strength:.2f}/{a.trend_quality}")
        print(f"│ FAKE/LATE   : fake_break={a.fake_breakout_risk} late={a.late_entry_risk} liq_sweep={a.liquidity_sweep_detected}")
        print(f"│ DECISION    : grade={a.grade}  permission={a.trade_permission}  expect={spec.expect_perm}")
        print(f"│ REASON      : {a.reason[:200]}")
        if not ok_all:
            why = []
            if not ok_perm: why.append(f"perm {a.trade_permission}!={spec.expect_perm}")
            if not ok_lat: why.append(f"latency {a.chart_analysis_latency_ms}ms > {spec.max_latency_ms}ms")
            if not ok_intel: why.append(f"intel {a.chart_intelligence_score} < {spec.expect_min_intelligence}")
            print(f"│ FIX_NEEDED  : {'; '.join(why)}")
            fail_rows.append((i, spec.name, "; ".join(why)))
        print(f"└─ correct? {status}")

    print("\n" + "=" * 100)
    print(f"FINAL: {pass_n}/{len(SCENARIOS)} PASSED  ({pass_n*100//len(SCENARIOS)}%)")
    avg_cold = sum(cold_lats)/len(cold_lats)
    avg_warm = sum(warm_lats)/len(warm_lats)
    print(f"LATENCY AVG : cold={avg_cold:.3f}ms  warm={avg_warm:.3f}ms  speedup={avg_cold/max(avg_warm,0.001):.1f}x")
    print(f"LATENCY MAX : cold={max(cold_lats):.3f}ms  warm={max(warm_lats):.3f}ms")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("=" * 100)
    return pass_n, len(SCENARIOS)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
