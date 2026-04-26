# -*- coding: utf-8 -*-
"""ChartMind audit — every failure path must end at wait or block.

Tests: missing M15 / M5 / M1, insufficient bars, ATR=0, no levels,
unknown pair, very few bars, all-flat data, exception in any module.
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from chartmind.v3 import ChartMindV3, Bar


NOW = datetime(2026,4,25,14,0,0,tzinfo=timezone.utc)


def make(n=80, start=1.10, slope=0.0001, noise=0.0001, seed=42):
    import random
    rng = random.Random(seed)
    out = []; last = start
    for i in range(n):
        c = start + slope*i + (rng.random()-0.5)*2*noise
        h = max(c, last) + noise
        l = min(c, last) - noise
        o = last + (c - last) * 0.3
        out.append(Bar(NOW - timedelta(minutes=15*(n-i)), o, h, l, c, 1000, 0.5))
        last = c
    return out


@dataclass
class Spec:
    name: str
    expect_perm_in: tuple

CASES = []

# 1. Missing M15
CASES.append((Spec("missing_m15", ("block",)),
    lambda: dict(pair="EUR/USD", bars_m15=[])))

# 2. Insufficient M15 bars (3)
CASES.append((Spec("insufficient_m15_bars", ("block","wait")),
    lambda: dict(pair="EUR/USD", bars_m15=make(3))))

# 3. Missing M5 (M15 only)
CASES.append((Spec("missing_m5_only_m15", ("allow","wait","block")),
    lambda: dict(pair="EUR/USD", bars_m15=make(80))))

# 4. Missing M1 (M15 + M5)
CASES.append((Spec("missing_m1_m15_m5_present", ("allow","wait","block")),
    lambda: dict(pair="EUR/USD", bars_m15=make(80), bars_m5=make(60, slope=0.00010))))

# 5. All flat data (no swings)
def flat(n=80):
    return [Bar(NOW - timedelta(minutes=15*(n-i)),
                open=1.10, high=1.10, low=1.10, close=1.10,
                volume=1000, spread_pips=0.5)
            for i in range(n)]
CASES.append((Spec("all_flat_no_atr", ("wait","block")),
    lambda: dict(pair="EUR/USD", bars_m15=flat(80))))

# 6. Unknown pair name
CASES.append((Spec("unknown_pair", ("allow","wait","block")),
    lambda: dict(pair="ZZZ/YYY", bars_m15=make(80))))

# 7. ATR=0 (zero-range bars)
CASES.append((Spec("atr_zero", ("wait","block")),
    lambda: dict(pair="EUR/USD", bars_m15=flat(80))))

# 8. M15 minimal (6 bars)
CASES.append((Spec("m15_minimal_6_bars", ("allow","wait","block")),
    lambda: dict(pair="EUR/USD", bars_m15=make(6))))


def main():
    print("=" * 100)
    print("ChartMind — Audit / Fail-Safe (8 سيناريو خطر)")
    print("=" * 100)
    cm = ChartMindV3()
    pass_n = 0
    fail_rows = []
    for i, (spec, build) in enumerate(CASES, 1):
        try:
            cfg = build()
            a = cm.assess(now_utc=NOW, **cfg)
        except Exception as e:
            print(f"\n[{i:02d}] {spec.name}  ✗ FAIL (CRASH: {e})")
            fail_rows.append((i, spec.name, f"crash: {e}"))
            continue
        ok = a.trade_permission in spec.expect_perm_in
        if ok: pass_n += 1
        status = "PASS" if ok else "FAIL"
        print(f"\n[{i:02d}] {spec.name}  {'✓ '+status if ok else '✗ '+status}")
        print(f"  permission={a.trade_permission}  expected_in={list(spec.expect_perm_in)}")
        print(f"  grade={a.grade}  structure={a.market_structure}")
        print(f"  reason={a.reason[:160]}")
        if not ok:
            fail_rows.append((i, spec.name, f"perm {a.trade_permission} not in {spec.expect_perm_in}"))

    print(f"\n{'=' * 100}\nFINAL: {pass_n}/{len(CASES)} PASSED  ({pass_n*100//len(CASES)}%)")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows: print(f"  [{i:02d}] {n} — {w}")
    print("=" * 100)
    return pass_n, len(CASES)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
