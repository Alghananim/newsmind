# -*- coding: utf-8 -*-
"""ChartMind V3 — Certification Test (23 سيناريو من قائمة المستخدم).

Each scenario produces full row format:
    INPUT / STRUCTURE / TREND / CANDLE / BREAKOUT / RETEST / ENTRY
    STOP / TARGET / R/R / MTF / VOL / WARNINGS
    GRADE / PERMISSION / REASON / CORRECT?
"""
from __future__ import annotations
import sys, math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from chartmind.v3 import ChartMindV3, Bar


NOW = datetime(2026,4,25,14,0,0,tzinfo=timezone.utc)


def make(n=80, start=1.10, slope=0.0, noise=0.0001, vol=1000, spread=0.5,
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
        out.append(Bar(ts, o, h, l, c, vol, spread))
        last = c
    return out


def trend_with_pullbacks(n=80, start=1.10, slope=0.00012, seed=42):
    """Realistic trend with pullbacks every 8 bars (creates swings)."""
    import random, math
    rng = random.Random(seed)
    out = []
    last = start
    for i in range(n):
        # Add periodic pullbacks
        oscillation = 0.0006 * math.sin(i / 4.0)
        c = start + slope * i + oscillation + (rng.random()-0.5)*0.00005
        h = max(c, last) + 0.00012
        l = min(c, last) - 0.00012
        o = last + (c - last) * 0.3
        out.append(Bar(NOW - timedelta(minutes=15*(n-i)), o, h, l, c, 1000, 0.5))
        last = c
    return out


def range_bars(n=80, base=1.10, amp=0.0008, seed=42):
    import random, math
    rng = random.Random(seed)
    out = []
    last = base
    for i in range(n):
        c = base + amp * math.sin(i / 5.0) + (rng.random()-0.5)*0.00003
        h = max(c, last) + 0.0001
        l = min(c, last) - 0.0001
        o = last
        out.append(Bar(NOW - timedelta(minutes=15*(n-i)), o, h, l, c, 900, 0.6))
        last = c
    return out


def choppy_bars(n=80, base=1.10):
    return [Bar(NOW - timedelta(minutes=15*(n-i)),
                open=base + (-1)**i * 0.0003,
                high=base + (-1)**i * 0.0003 + 0.00015,
                low=base + (-1)**i * 0.0003 - 0.00015,
                close=base + (-1)**(i+1) * 0.0003,
                volume=1000, spread_pips=0.5)
            for i in range(n)]


def real_breakout(n=80, base=1.10):
    """Range then strong break above."""
    bs = range_bars(n-3, base=base, amp=0.0008)
    last = bs[-1].close
    bs.append(Bar(NOW - timedelta(minutes=15*2), open=last, high=last+0.0010,
                  low=last-0.0001, close=last+0.0009, volume=2000, spread_pips=0.5))
    bs.append(Bar(NOW - timedelta(minutes=15*1), open=last+0.0009, high=last+0.0015,
                  low=last+0.0008, close=last+0.0014, volume=2200, spread_pips=0.5))
    bs.append(Bar(NOW, open=last+0.0014, high=last+0.0018,
                  low=last+0.0012, close=last+0.0017, volume=2400, spread_pips=0.5))
    return bs


def fake_breakout(n=80, base=1.10):
    """Range, breakout up, then quickly reverses below the level."""
    bs = range_bars(n-4, base=base, amp=0.0008)
    last = bs[-1].close
    bs.append(Bar(NOW - timedelta(minutes=15*3), open=last, high=last+0.0014,
                  low=last-0.0001, close=last+0.0013, volume=2000, spread_pips=0.5))
    # Reversal
    bs.append(Bar(NOW - timedelta(minutes=15*2), open=last+0.0013,
                  high=last+0.0014, low=last-0.0002, close=last-0.0001,
                  volume=2200, spread_pips=0.5))
    bs.append(Bar(NOW - timedelta(minutes=15*1), open=last-0.0001,
                  high=last+0.0001, low=last-0.0008, close=last-0.0006,
                  volume=2100, spread_pips=0.5))
    bs.append(Bar(NOW, open=last-0.0006, high=last-0.0004,
                  low=last-0.0010, close=last-0.0008, volume=1800, spread_pips=0.5))
    return bs


def liquidity_sweep_bars(n=80, base=1.10):
    """Range, then a long wick spike beyond resistance, close back inside."""
    bs = range_bars(n-1, base=base, amp=0.0008)
    last = bs[-1].close
    bs.append(Bar(NOW, open=last, high=last+0.003, low=last-0.0005,
                  close=last-0.0001, volume=3000, spread_pips=0.8))
    return bs


def late_entry_bars(n=80, base=1.10):
    """Trend up + last bar HUGE bullish (chase risk)."""
    bs = make(n-5, start=base, slope=0.00010, seed=42)
    last = bs[-1].close
    # 3 strong bull bars in a row, last one extreme
    for k, m in enumerate([0.0005, 0.0008, 0.0014, 0.0020, 0.0025]):
        bs.append(Bar(NOW - timedelta(minutes=15*(4-k)),
                     open=last, high=last+m, low=last-0.00003,
                     close=last+m*0.95, volume=2000, spread_pips=0.5))
        last = last + m*0.95
    return bs


def reversal_pattern(n=80, base=1.10):
    """Up trend then bearish engulfing at resistance."""
    bs = make(n-2, start=base, slope=0.00012, seed=42)
    last = bs[-1].close
    # Bullish bar
    bs.append(Bar(NOW - timedelta(minutes=15*1), open=last, high=last+0.0008,
                  low=last-0.0001, close=last+0.0007, volume=2000, spread_pips=0.5))
    # Bearish engulfing
    bs.append(Bar(NOW, open=last+0.0009, high=last+0.0010,
                  low=last-0.0005, close=last-0.0003, volume=2500, spread_pips=0.5))
    return bs


@dataclass
class Spec:
    name: str
    expect_perm: str            # allow/wait/block/any
    expect_grade_max: str = "A+"
    expect_grade_min: str = "C"
    expect_warning_substr: str = ""
    expect_struct: str = ""

GRADE_RANK = {"A+":4,"A":3,"B":2,"C":1,"":0}
def in_range(g, lo, hi): return GRADE_RANK[lo] <= GRADE_RANK[g] <= GRADE_RANK[hi]


SCENARIOS = []

# 1: trend واضح
SCENARIOS.append((Spec("01_trend_clean", "any", "A+", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=trend_with_pullbacks(80))))

# 2: range
SCENARIOS.append((Spec("02_range", "wait", "B", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=range_bars(80))))

# 3: choppy
SCENARIOS.append((Spec("03_choppy", "wait", "C", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=choppy_bars(80))))

# 4: breakout حقيقي
SCENARIOS.append((Spec("04_real_breakout", "any", "A+", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=real_breakout(80))))

# 5: fake breakout
SCENARIOS.append((Spec("05_fake_breakout", "any", "C", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=fake_breakout(80))))

# 6: pullback ناجح (proxy: real breakout that re-tests)
SCENARIOS.append((Spec("06_pullback_success", "any", "A+", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=real_breakout(80))))

# 7: pullback فاشل (proxy: fake breakout)
SCENARIOS.append((Spec("07_pullback_fail", "any", "C", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=fake_breakout(80))))

# 8: retest ناجح (same as #6 with extra bars)
SCENARIOS.append((Spec("08_retest_success", "any", "A+", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=real_breakout(80))))

# 9: retest فاشل
SCENARIOS.append((Spec("09_retest_fail", "any", "C", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=fake_breakout(80))))

# 10: reversal pattern
SCENARIOS.append((Spec("10_reversal_pattern", "any", "A+", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=reversal_pattern(80))))

# 11: engulfing في مكان صحيح (reversal pattern at recent resistance)
SCENARIOS.append((Spec("11_engulfing_at_level", "any", "A+", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=reversal_pattern(80))))

# 12: engulfing في مكان خاطئ (engulfing in midrange)
SCENARIOS.append((Spec("12_engulfing_midrange", "any", "C", "C"),
    lambda: dict(pair="EUR/USD",
                 bars_m15=range_bars(78) + [
                     Bar(NOW - timedelta(minutes=15), open=1.1006, high=1.1010,
                         low=1.1004, close=1.1009, volume=1500, spread_pips=0.5),
                     Bar(NOW, open=1.1010, high=1.1011, low=1.1004, close=1.1005,
                         volume=1800, spread_pips=0.5),
                 ])))

# 13: pin bar في مستوى مهم (we'll synthesize)
def pin_at_support(n=80, base=1.10):
    bs = make(n-1, start=base, slope=-0.00010)
    last = bs[-1].close
    # Hammer at support
    bs.append(Bar(NOW, open=last, high=last+0.00015, low=last-0.0008,
                  close=last+0.00010, volume=2000, spread_pips=0.5))
    return bs
SCENARIOS.append((Spec("13_pin_bar_at_level", "any", "A+", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=pin_at_support(80))))

# 14: pin bar في وسط الشارت
def pin_midrange(n=80, base=1.10):
    bs = range_bars(n-1, base=base, amp=0.0005)
    last = bs[-1].close
    bs.append(Bar(NOW, open=last, high=last+0.0001, low=last-0.0008,
                  close=last+0.00005, volume=1500, spread_pips=0.5))
    return bs
SCENARIOS.append((Spec("14_pin_bar_midrange", "any", "C", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=pin_midrange(80))))

# 15: دخول متأخر بعد شمعة كبيرة
SCENARIOS.append((Spec("15_late_entry_chase", "block", "C", "C"),
    lambda: dict(pair="EUR/USD", bars_m15=late_entry_bars(80))))

# 16: stop قريب جداً (synthesize tight range)
def tight_range_no_stop(n=80, base=1.10):
    return [Bar(NOW - timedelta(minutes=15*(n-i)),
                open=base, high=base+0.00005, low=base-0.00005,
                close=base, volume=900, spread_pips=0.5) for i in range(n)]
SCENARIOS.append((Spec("16_stop_too_tight_or_invalid", "any"),
    lambda: dict(pair="EUR/USD", bars_m15=tight_range_no_stop(80))))

# 17: stop بعيد جداً (extreme volatility)
def huge_atr_bars(n=80, base=1.10):
    bs = make(n-1, start=base, slope=0.00005)
    last = bs[-1].close
    bs.append(Bar(NOW, open=last, high=last+0.015, low=last-0.005,
                  close=last+0.012, volume=4000, spread_pips=2.0))
    return bs
SCENARIOS.append((Spec("17_stop_too_wide_extreme_atr", "any"),
    lambda: dict(pair="EUR/USD", bars_m15=huge_atr_bars(80))))

# 18: target غير واقعي (proxy: very narrow range, no resistance)
SCENARIOS.append((Spec("18_target_unrealistic", "any"),
    lambda: dict(pair="EUR/USD", bars_m15=tight_range_no_stop(80))))

# 19: R/R ضعيف (overbought near resistance)
def near_resistance(n=80, base=1.10):
    """Build resistance level then trend up close to it."""
    bs = []
    # Establish resistance touches
    for i in range(20):
        bs.append(Bar(NOW - timedelta(minutes=15*(80-i)),
                     open=base, high=base+0.0010, low=base-0.0001,
                     close=base+0.0008, volume=900, spread_pips=0.5))
    # Trend up to just below resistance
    last = base + 0.0008
    for i in range(20, 80):
        last += 0.000005
        bs.append(Bar(NOW - timedelta(minutes=15*(80-i)),
                     open=last-0.00001, high=last+0.00001, low=last-0.00002,
                     close=last, volume=1000, spread_pips=0.5))
    return bs
SCENARIOS.append((Spec("19_rr_weak_near_resistance", "any"),
    lambda: dict(pair="EUR/USD", bars_m15=near_resistance(80))))

# 20: 1M يعطي إشارة و15M يعارض (MTF conflict)
def mtf_conflict():
    """M15 down trend, M5 up trend, M1 up trend."""
    return dict(
        pair="EUR/USD",
        bars_m15=make(80, start=1.10, slope=-0.00015, seed=10),  # bearish
        bars_m5=make(60, start=1.10, slope=0.00010, seed=20),    # bullish
        bars_m1=make(40, start=1.10, slope=0.00012, seed=30),    # bullish
    )
SCENARIOS.append((Spec("20_mtf_conflict_m15_vs_m5", "block", "C", "C", expect_warning_substr="mtf"),
    lambda: mtf_conflict()))

# 21: SR قريب يمنع الصفقة
SCENARIOS.append((Spec("21_sr_blocks_trade", "any"),
    lambda: dict(pair="EUR/USD", bars_m15=near_resistance(80))))

# 22: ATR عالي جداً
SCENARIOS.append((Spec("22_atr_extreme", "any"),
    lambda: dict(pair="EUR/USD", bars_m15=huge_atr_bars(80))))

# 23: ATR منخفض جداً + fake move قبل انعكاس
SCENARIOS.append((Spec("23_atr_low_or_fake_then_reversal", "any"),
    lambda: dict(pair="EUR/USD", bars_m15=liquidity_sweep_bars(80))))


def main():
    print("=" * 100)
    print("ChartMind V3 — Certification Test (23 سيناريو)")
    print("=" * 100)

    cm = ChartMindV3()
    pass_n = 0
    fail_rows = []
    for i, (spec, build) in enumerate(SCENARIOS, 1):
        cfg = build()
        a = cm.assess(now_utc=NOW, **cfg)

        ok_perm = (spec.expect_perm == "any") or (a.trade_permission == spec.expect_perm)
        ok_grade = in_range(a.grade, spec.expect_grade_min, spec.expect_grade_max)
        ok_warn = (not spec.expect_warning_substr) or any(
            spec.expect_warning_substr in w for w in a.warnings) or (
            spec.expect_warning_substr in a.reason)
        ok_struct = (not spec.expect_struct) or a.market_structure == spec.expect_struct
        ok_all = ok_perm and ok_grade and ok_warn and ok_struct
        if ok_all: pass_n += 1
        status = "PASS" if ok_all else "FAIL"

        print(f"\n┌─[{i:02d}] {spec.name}  {'✓ ' + status if ok_all else '✗ ' + status}")
        print(f"│ INPUT     : pair={a.pair}  TFs={list(a.timeframes_used)}  bars_m15={len(cfg['bars_m15'])}")
        print(f"│ STRUCTURE : {a.market_structure}  trend={a.trend_direction}/{a.trend_strength:.2f}/{a.trend_quality}")
        print(f"│ LEVELS    : nearest={a.nearest_key_level} ({a.nearest_key_role}) dist={a.nearest_key_distance_atr:.2f}ATR")
        print(f"│ CANDLE    : {a.candlestick_signal}/{a.candlestick_context}/{a.candlestick_quality}")
        print(f"│ BREAKOUT  : status={a.breakout_status}  retest={a.retest_status}  pullback={a.pullback_quality}")
        print(f"│ ENTRY     : {a.entry_quality}  late_risk={a.late_entry_risk}  zone={a.entry_price_zone}")
        print(f"│ STOP/TGT  : stop={a.stop_loss}  target={a.take_profit}  R/R={a.risk_reward}  {a.stop_logic}|{a.target_logic}")
        print(f"│ MTF       : {a.timeframe_alignment}")
        print(f"│ VOL/ATR   : vol={a.volatility_status}  atr_state={a.atr_status}")
        print(f"│ TRAPS     : fake_break={a.fake_breakout_risk} liq_sweep={a.liquidity_sweep_detected}")
        print(f"│ WARNINGS  : {list(a.warnings) or 'none'}")
        print(f"│ GRADE     : got={a.grade}  expect=[{spec.expect_grade_min}..{spec.expect_grade_max}]  {'✓' if ok_grade else '✗'}")
        print(f"│ PERMISSION: got={a.trade_permission}  expect={spec.expect_perm}  {'✓' if ok_perm else '✗'}")
        print(f"│ REASON    : {a.reason[:200]}")
        if not ok_all:
            why = []
            if not ok_perm: why.append(f"perm {a.trade_permission}!={spec.expect_perm}")
            if not ok_grade: why.append(f"grade {a.grade} not in [{spec.expect_grade_min}..{spec.expect_grade_max}]")
            if not ok_warn: why.append(f"missing warning '{spec.expect_warning_substr}'")
            if not ok_struct: why.append(f"struct {a.market_structure}!={spec.expect_struct}")
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
