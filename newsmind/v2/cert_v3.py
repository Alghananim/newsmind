# -*- coding: utf-8 -*-
"""V3 Certification Test — produces the full row format the user asked for.

For each scenario, prints:
    INPUT       headline + source + source_type
    SOURCE      trusted? confirmation_count
    FRESHNESS   classified status
    VERIFY      confirmed / unconfirmed / contradicted / n_a
    IMPACT      level + market_bias + risk_mode
    GRADE       A+/A/B/C
    PERMISSION  allow/wait/block
    REASON      machine-readable verdict.reason
    CORRECT?    PASS/FAIL vs ground-truth
    FIX         if FAIL, what would be needed
"""
from __future__ import annotations
import sys, json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")
from newsmind.v2 import NewsMindV2, NewsItem
from newsmind.v2.sources import NewsSource


@dataclass
class Bar:
    high: float; low: float; close: float
    spread_pips: float = 0.5; volume: int = 1000

def calm(n=20, b=1.10): return [Bar(b+0.0005, b-0.0005, b) for _ in range(n)]
def chase(b=1.10): return Bar(b+0.004, b-0.002, b+0.0035, spread_pips=2.0, volume=4000)


@dataclass
class FakeRaw:
    name: str; when_utc: datetime; source: str = "calendar"
class FakeCal:
    def __init__(self, e): self._e=e
    def events(self): return self._e

class Stub(NewsSource):
    def __init__(self, name, st, items):
        super().__init__(); self.name=name; self.source_type=st; self._items=items
    def _do_fetch(self, *, since_utc, now):
        for it in self._items:
            if it.source_name in (None, "", "unknown"): it.source_name = self.name
            if it.source_type in (None, "", "calendar"): it.source_type = self.source_type
        return list(self._items)

class FailSource(NewsSource):
    name="failing"; source_type="tier1_wire"
    def _do_fetch(self, *, since_utc, now): raise RuntimeError("network down")


NOW = datetime(2026,4,25,14,0,0,tzinfo=timezone.utc)

def item(headline, *, src, st, age_min=1, recv_age_min=None, pairs=("EUR/USD",), confs=1, conflict=()):
    pub = NOW - timedelta(minutes=age_min)
    recv = NOW - timedelta(minutes=recv_age_min if recv_age_min is not None else age_min)
    return NewsItem(headline=headline, source_name=src, source_type=st,
                    published_at=pub, normalized_utc_time=pub, received_at=recv,
                    affected_pairs=pairs, confirmation_count=confs, conflicting_sources=conflict)


# ----------------------------------------------------------------------
# 15 certification scenarios
# ----------------------------------------------------------------------
@dataclass
class Spec:
    name: str
    expect_perm: str          # allow/wait/block
    expect_grade_max: str     # max acceptable grade (A+, A, B, C)
    expect_grade_min: str = "C"  # min acceptable grade
    expect_freshness: str = ""
    expect_market_bias: str = ""   # bullish/bearish/neutral/unclear/any
    expect_risk_mode: str = ""     # risk_on/risk_off/unclear/any

GRADE_RANK = {"A+":4, "A":3, "B":2, "C":1, "":0}

def grade_in_range(got, mn, mx):
    return GRADE_RANK[mn] <= GRADE_RANK[got] <= GRADE_RANK[mx]


SCENARIOS = []

def add(spec, build):
    SCENARIOS.append((spec, build))

# 1
add(Spec("CPI_strong", "allow", "A+", "A", "fresh", "bearish", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("US CPI prints 4.2% vs 3.5% expected — hot inflation",
              src="reuters_wire", st="tier1_wire", confs=1)]),
        Stub("bloomberg_wire","tier1_wire",[item("US CPI prints 4.2% vs 3.5% expected — hot inflation",
              src="bloomberg_wire", st="tier1_wire", confs=1)]),
        Stub("forexlive","financial_media",[item("US CPI prints 4.2% vs 3.5% expected — hot inflation",
              src="forexlive", st="financial_media", confs=1)]),
    ], bars=calm(), curr=calm()[-1]))

# 2
add(Spec("CPI_weak", "allow", "A+", "A", "fresh", "bullish", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("US CPI prints 2.0% vs 3.0% expected — soft inflation",
              src="reuters_wire", st="tier1_wire", confs=1)]),
        Stub("bloomberg_wire","tier1_wire",[item("US CPI prints 2.0% vs 3.0% expected — soft inflation",
              src="bloomberg_wire", st="tier1_wire", confs=1)]),
        Stub("forexlive","financial_media",[item("US CPI prints 2.0% vs 3.0% expected — soft inflation",
              src="forexlive", st="financial_media", confs=1)]),
    ], bars=calm(), curr=calm()[-1]))

# 3
add(Spec("NFP_strong", "allow", "A+", "A", "fresh", "bearish", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("US NFP +320k vs +180k expected — hot labor market",
              src="reuters_wire", st="tier1_wire", confs=1)]),
        Stub("bloomberg_wire","tier1_wire",[item("US NFP +320k vs +180k expected — hot labor market",
              src="bloomberg_wire", st="tier1_wire", confs=1)]),
        Stub("forexlive","financial_media",[item("US NFP +320k vs +180k expected — hot labor market",
              src="forexlive", st="financial_media", confs=1)]),
    ], bars=calm(), curr=calm()[-1]))

# 4
add(Spec("NFP_weak", "allow", "A+", "A", "fresh", "bullish", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("US NFP +50k vs +180k expected — labor cooling",
              src="reuters_wire", st="tier1_wire", confs=1)]),
        Stub("bloomberg_wire","tier1_wire",[item("US NFP +50k vs +180k expected — labor cooling",
              src="bloomberg_wire", st="tier1_wire", confs=1)]),
        Stub("forexlive","financial_media",[item("US NFP +50k vs +180k expected — labor cooling",
              src="forexlive", st="financial_media", confs=1)]),
    ], bars=calm(), curr=calm()[-1]))

# 5
add(Spec("Fed_surprise_cut", "allow", "A", "B", "fresh", "bullish", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("Fed surprises with 50bp cut — markets did not expect",
              src="reuters_wire", st="tier1_wire", confs=1)]),
        Stub("bloomberg_wire","tier1_wire",[item("Fed surprises with 50bp cut — markets did not expect",
              src="bloomberg_wire", st="tier1_wire", confs=1)]),
    ], bars=calm(), curr=calm()[-1]))

# 6
add(Spec("Fed_hawkish", "allow", "A", "B", "fresh", "bearish", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("Powell signals further hikes needed — hawkish",
              src="reuters_wire", st="tier1_wire", confs=1, age_min=2)]),
        Stub("bloomberg_wire","tier1_wire",[item("Powell signals further hikes needed — hawkish",
              src="bloomberg_wire", st="tier1_wire", confs=1, age_min=2)]),
    ], bars=calm(), curr=calm()[-1]))

# 7
add(Spec("Fed_dovish", "allow", "A", "B", "fresh", "bullish", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("Powell hints rate cuts coming — dovish",
              src="reuters_wire", st="tier1_wire", confs=1, age_min=2)]),
        Stub("bloomberg_wire","tier1_wire",[item("Powell hints rate cuts coming — dovish",
              src="bloomberg_wire", st="tier1_wire", confs=1, age_min=2)]),
    ], bars=calm(), curr=calm()[-1]))

# 8 — recycled
add(Spec("Recycled_news", "block", "C", "C", "recycled", "any", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("ECB hiked rates 25bp (republished from yesterday)",
              src="reuters_wire", st="tier1_wire", confs=2, age_min=24*60, recv_age_min=1)])
    ], bars=calm(), curr=calm()[-1]))

# 9 — Twitter rumor
add(Spec("Twitter_rumor", "wait", "C", "C", "fresh", "any", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("twitter_official","social",[item("[X] Anonymous: Fed will cut tomorrow",
              src="twitter_official", st="social", confs=1)])
    ], bars=calm(), curr=calm()[-1]))

# 10 — Trump on Truth Social
add(Spec("Trump_truth_social", "wait", "C", "C", "fresh", "any", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("truth_social","social",[item("Trump (Truth Social): Fed must cut now",
              src="truth_social", st="social", confs=1)])
    ], bars=calm(), curr=calm()[-1]))

# 11 — War / sanctions
add(Spec("War_sanctions", "wait", "A+", "B", "fresh", "any", "risk_off"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("BREAKING: Major escalation in Middle East — sanctions imposed",
              src="reuters_wire", st="tier1_wire", confs=1, pairs=("EUR/USD","USD/JPY"))]),
        Stub("bloomberg_wire","tier1_wire",[item("BREAKING: Major escalation in Middle East — sanctions imposed",
              src="bloomberg_wire", st="tier1_wire", confs=1, pairs=("EUR/USD","USD/JPY"))]),
    ], bars=calm(), curr=calm()[-1]))

# 12 — Conflicting wires
add(Spec("Conflicting_news", "wait", "C", "C", "fresh", "any", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("Fed expected to CUT today — sources",
              src="reuters_wire", st="tier1_wire", confs=1, conflict=("bloomberg_wire",), age_min=2)]),
        Stub("bloomberg_wire","tier1_wire",[item("Fed expected to HOLD today — sources",
              src="bloomberg_wire", st="tier1_wire", confs=1, conflict=("reuters_wire",), age_min=2)]),
    ], bars=calm(), curr=calm()[-1]))

# 13 — No timestamp
add(Spec("No_timestamp", "wait", "C", "C", "unknown", "any", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[NewsItem(
            headline="Some macro headline (no timestamp)",
            source_name="reuters_wire", source_type="tier1_wire",
            published_at=None, normalized_utc_time=None, received_at=NOW,
            affected_pairs=("EUR/USD",), confirmation_count=2)])
    ], bars=calm(), curr=calm()[-1]))

# 14 — Source failure
add(Spec("Source_failure", "wait", "C", "C", "any", "any", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[FailSource()],
                 bars=calm(), curr=calm()[-1]))

# 15 — Real news but market already moved
add(Spec("Already_moved", "block", "C", "C", "fresh", "any", "any"),
    lambda: dict(pair="EUR/USD", calendar=None, sources=[
        Stub("reuters_wire","tier1_wire",[item("ECB unexpectedly hawkish",
              src="reuters_wire", st="tier1_wire", confs=1)]),
        Stub("bloomberg_wire","tier1_wire",[item("ECB unexpectedly hawkish",
              src="bloomberg_wire", st="tier1_wire", confs=1)]),
    ], bars=calm(), curr=chase()))


# ----------------------------------------------------------------------
def main():
    print("=" * 100)
    print("NewsMind V3 — Certification Test (15 سيناريو)")
    print("=" * 100)

    pass_n = 0
    fail_rows = []
    for i, (spec, build) in enumerate(SCENARIOS, 1):
        cfg = build()
        nm = NewsMindV2(pair=cfg["pair"], calendar=cfg["calendar"], sources=cfg["sources"])
        v = nm.evaluate(now_utc=NOW, recent_bars=cfg["bars"], current_bar=cfg["curr"])

        # Source row
        src_name = v.source_name or "n_a"
        src_type = v.source_type or "n_a"

        # Verification verdict
        if v.confirmation_count >= 2: verif = "confirmed"
        elif v.confirmation_count == 1: verif = "unconfirmed"
        elif v.confirmation_count == 0: verif = "n_a"
        else: verif = "unknown"
        if v.conflicting_sources: verif = "contradicted"

        # Validation
        ok_perm = v.trade_permission == spec.expect_perm
        ok_grade = grade_in_range(v.grade or "C", spec.expect_grade_min, spec.expect_grade_max)
        ok_fresh = (spec.expect_freshness in ("any","")
                    or v.freshness_status == spec.expect_freshness)
        ok_bias = (spec.expect_market_bias in ("any","")
                   or v.market_bias == spec.expect_market_bias)
        ok_risk = (spec.expect_risk_mode in ("any","")
                   or v.risk_mode == spec.expect_risk_mode)

        ok_all = ok_perm and ok_grade and ok_fresh and ok_bias and ok_risk
        if ok_all: pass_n += 1
        status = "PASS" if ok_all else "FAIL"

        # Print row
        print(f"\n┌─[{i:02d}] {spec.name}  {'✓ '+status if ok_all else '✗ '+status}")
        print(f"│ INPUT      : {v.headline}")
        print(f"│ SOURCE     : {src_name} ({src_type})  trusted={src_type in ('tier1_wire','official','calendar')}  confirmations={v.confirmation_count}")
        print(f"│ FRESHNESS  : got={v.freshness_status}  expect={spec.expect_freshness or 'any'}  {'✓' if ok_fresh else '✗'}")
        print(f"│ VERIFY     : {verif}  conflicts={v.conflicting_sources or '()'}  ")
        print(f"│ IMPACT     : level={v.impact_level}  bias={v.market_bias}  risk={v.risk_mode}  {'✓' if ok_bias and ok_risk else '✗'}")
        print(f"│ GRADE      : got={v.grade}  expect=[{spec.expect_grade_min}..{spec.expect_grade_max}]  {'✓' if ok_grade else '✗'}")
        print(f"│ PERMISSION : got={v.trade_permission}  expect={spec.expect_perm}  {'✓' if ok_perm else '✗'}")
        print(f"│ REASON     : {v.reason}")
        if not ok_all:
            why = []
            if not ok_perm: why.append(f"permission {v.trade_permission}!={spec.expect_perm}")
            if not ok_grade: why.append(f"grade {v.grade} not in [{spec.expect_grade_min}..{spec.expect_grade_max}]")
            if not ok_fresh: why.append(f"freshness {v.freshness_status}!={spec.expect_freshness}")
            if not ok_bias: why.append(f"bias {v.market_bias}!={spec.expect_market_bias}")
            if not ok_risk: why.append(f"risk {v.risk_mode}!={spec.expect_risk_mode}")
            print(f"│ FIX_NEEDED : {'; '.join(why)}")
            fail_rows.append((i, spec.name, "; ".join(why)))
        print(f"└─ correct? {status}")

    print("\n" + "=" * 100)
    print(f"FINAL: {pass_n}/{len(SCENARIOS)} PASSED  ({pass_n*100//len(SCENARIOS)}%)")
    if fail_rows:
        print("FAILS:")
        for i, n, w in fail_rows:
            print(f"  [{i:02d}] {n} — {w}")
    print("=" * 100)
    return pass_n, len(SCENARIOS)


if __name__ == "__main__":
    p, n = main()
    sys.exit(0 if p == n else 1)
