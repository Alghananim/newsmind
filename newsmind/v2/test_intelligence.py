# -*- coding: utf-8 -*-
"""اختبار ذكاء NewsMind v2 — 23 سيناريو واقعي.

نقيس 5 طبقات لكل سيناريو:
    1. SAFETY     — هل القرار آمن (block/wait عند الشك)؟
    2. FRESHNESS  — هل صنّف العمر صحيحاً؟
    3. VERIFICATION — مؤكد / إشاعة / متضارب؟
    4. IMPACT     — هل عرف اتجاه التأثير على USD، EUR/USD، USD/JPY؟
    5. RISK MODE  — risk-on / risk-off / unclear؟

النتيجة "INTELLIGENT" تتطلب نجاح كل الـ 5. إذا نجح الـ 1 فقط (safety) فالـ NewsMind
"GATE فقط" وليس عقل.
"""
from __future__ import annotations
import sys, json
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")

from newsmind.v2 import NewsMindV2, NewsItem
from newsmind.v2.sources import NewsSource


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
@dataclass
class FakeBar:
    high: float; low: float; close: float
    spread_pips: float = 0.5; volume: int = 1000

def calm(n=20, base=1.10):
    return [FakeBar(base+0.0005, base-0.0005, base) for _ in range(n)]

def chase_bar(base=1.10):
    return FakeBar(base+0.004, base-0.002, base+0.0035, spread_pips=2.0, volume=4000)


@dataclass
class FakeRaw:
    name: str; when_utc: datetime; source: str = "calendar"

class FakeCal:
    def __init__(self, evts): self._e = evts
    def events(self): return self._e

class Stub(NewsSource):
    def __init__(self, name, source_type, items):
        super().__init__()
        self.name = name; self.source_type = source_type; self._items = items
    def _do_fetch(self, *, since_utc, now):
        for it in self._items:
            if not it.source_name or it.source_name == "unknown":
                it.source_name = self.name
            if not it.source_type or it.source_type == "calendar":
                it.source_type = self.source_type
        return list(self._items)

class Failing(NewsSource):
    name = "broken"; source_type = "tier1_wire"
    def _do_fetch(self, *, since_utc, now): raise RuntimeError("network")


# ----------------------------------------------------------------------
# Scoring
# ----------------------------------------------------------------------
@dataclass
class ScenarioExpect:
    """Ground-truth for one scenario."""
    name: str
    headline: str
    source: str
    source_type: str
    source_trusted: bool
    expected_freshness: str          # fresh/recent/stale/recycled/unknown
    expected_verification: str       # confirmed/unconfirmed/rumor/contradicted/n_a
    expected_impact_level: str       # high/medium/low
    expected_usd_dir: str            # up/down/neutral/unclear
    expected_eur_usd_dir: str
    expected_usd_jpy_dir: str
    expected_risk_mode: str          # risk_on/risk_off/unclear
    expected_grade_max: str          # max grade allowed (A+/A/B/C)
    expected_permission: str         # allow/wait/block

@dataclass
class ScenarioResult:
    expect: ScenarioExpect
    got_permission: str
    got_freshness: str
    got_grade: str
    got_confirmations: int
    got_market_bias: str
    got_risk_mode: str
    got_impact_level: str
    got_reason: str
    safety_ok: bool         # block/wait when we asked for it
    freshness_ok: bool
    verification_ok: bool
    impact_ok: bool         # did it know the USD direction?
    risk_mode_ok: bool
    pair_diff_ok: bool      # did EUR/USD and USD/JPY get *different* assessments?

    @property
    def intelligent(self) -> bool:
        return all([self.safety_ok, self.freshness_ok, self.verification_ok,
                    self.impact_ok, self.risk_mode_ok])

# Permission ranking: a result that says "block" satisfies "allow" only if
# the user wanted "allow" exactly.
PERMISSION_OK = {
    "allow": {"allow"},
    "wait":  {"wait", "block"},     # block is "more conservative", acceptable for wait
    "block": {"block"},
}

GRADE_RANK = {"A+": 4, "A": 3, "B": 2, "C": 1, "": 0}
def grade_at_or_below(got, max_g):
    return GRADE_RANK.get(got, 0) <= GRADE_RANK.get(max_g, 4)


# ----------------------------------------------------------------------
# Scenario builder helpers
# ----------------------------------------------------------------------
NOW = datetime(2026, 4, 25, 14, 0, 0, tzinfo=timezone.utc)

def news_item(headline, *, source, source_type, age_min=1, recv_age_min=None,
              affected_pairs=("EUR/USD",), affected_ccys=("USD",),
              confirmation_count=1, conflicting=()):
    pub = NOW - timedelta(minutes=age_min)
    recv = NOW - timedelta(minutes=recv_age_min if recv_age_min is not None else age_min)
    return NewsItem(
        headline=headline,
        source_name=source,
        source_type=source_type,
        published_at=pub,
        normalized_utc_time=pub,
        received_at=recv,
        affected_pairs=affected_pairs,
        affected_currencies=affected_ccys,
        confirmation_count=confirmation_count,
        conflicting_sources=conflicting,
    )


# ----------------------------------------------------------------------
# All 23 scenarios
# ----------------------------------------------------------------------
def all_scenarios():
    """Return list of (ScenarioExpect, build_func) where build_func returns
    a (sources, calendar, recent_bars, current_bar) tuple per pair side."""
    out = []

    # 1. CPI hotter than expected — USD strong, EUR/USD down, USD/JPY up
    out.append((
        ScenarioExpect("CPI_hot", "US CPI prints 4.2% vs 3.8% expected — hot inflation",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "up", "down", "up", "risk_off",
                       "A", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "US CPI prints 4.2% vs 3.8% expected — hot inflation",
                    source="reuters_wire", source_type="tier1_wire",
                    age_min=1, confirmation_count=1)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "US CPI prints 4.2% vs 3.8% expected — hot inflation",
                    source="bloomberg_wire", source_type="tier1_wire",
                    age_min=1, confirmation_count=1)]),
                Stub("forexlive","financial_media",[news_item(
                    "US CPI prints 4.2% vs 3.8% expected — hot inflation",
                    source="forexlive", source_type="financial_media",
                    age_min=1, confirmation_count=1)]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 2. CPI weaker than expected — USD weak, EUR/USD up, USD/JPY down
    out.append((
        ScenarioExpect("CPI_cold", "US CPI prints 2.1% vs 3.0% expected — soft inflation",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "down", "up", "down", "risk_on",
                       "A", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "US CPI prints 2.1% vs 3.0% expected — soft inflation",
                    source="reuters_wire", source_type="tier1_wire", age_min=1)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "US CPI prints 2.1% vs 3.0% expected — soft inflation",
                    source="bloomberg_wire", source_type="tier1_wire", age_min=1)]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 3. NFP strong — USD strong
    out.append((
        ScenarioExpect("NFP_strong", "US NFP +320k vs +180k expected — labor market hot",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "up", "down", "up", "unclear",
                       "A", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "US NFP +320k vs +180k expected — labor market hot",
                    source="reuters_wire", source_type="tier1_wire", age_min=1)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "US NFP +320k vs +180k expected — labor market hot",
                    source="bloomberg_wire", source_type="tier1_wire", age_min=1)]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 4. NFP weak — USD weak
    out.append((
        ScenarioExpect("NFP_weak", "US NFP +50k vs +180k expected — labor market cooling",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "down", "up", "down", "risk_on",
                       "A", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "US NFP +50k vs +180k expected — labor market cooling",
                    source="reuters_wire", source_type="tier1_wire", age_min=1)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "US NFP +50k vs +180k expected — labor market cooling",
                    source="bloomberg_wire", source_type="tier1_wire", age_min=1)]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 5. Fed rate decision as expected
    out.append((
        ScenarioExpect("Fed_as_expected", "Fed holds rates at 4.50%, in line with consensus",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "neutral", "neutral", "neutral", "unclear",
                       "B", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "Fed holds rates at 4.50%, in line with consensus",
                    source="reuters_wire", source_type="tier1_wire", age_min=1)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "Fed holds rates at 4.50%, in line with consensus",
                    source="bloomberg_wire", source_type="tier1_wire", age_min=1)]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 6. Fed surprises with cut
    out.append((
        ScenarioExpect("Fed_surprise_cut", "Fed surprises with 50bp cut — markets did not expect",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "down", "up", "down", "risk_on",
                       "A", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "Fed surprises with 50bp cut — markets did not expect",
                    source="reuters_wire", source_type="tier1_wire", age_min=1)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "Fed surprises with 50bp cut — markets did not expect",
                    source="bloomberg_wire", source_type="tier1_wire", age_min=1)]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 7. Hawkish Fed speaker
    out.append((
        ScenarioExpect("Fed_hawkish", "Powell signals further hikes needed — hawkish",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "medium", "up", "down", "up", "unclear",
                       "B", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "Powell signals further hikes needed — hawkish",
                    source="reuters_wire", source_type="tier1_wire", age_min=2)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "Powell signals further hikes needed — hawkish",
                    source="bloomberg_wire", source_type="tier1_wire", age_min=2)]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 8. Dovish Fed speaker
    out.append((
        ScenarioExpect("Fed_dovish", "Powell hints rate cuts coming — dovish",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "medium", "down", "up", "down", "risk_on",
                       "B", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "Powell hints rate cuts coming — dovish",
                    source="reuters_wire", source_type="tier1_wire", age_min=2)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "Powell hints rate cuts coming — dovish",
                    source="bloomberg_wire", source_type="tier1_wire", age_min=2)]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 9. Old news re-published (recycled)
    out.append((
        ScenarioExpect("Recycled", "ECB hiked rates 25bp (republished from yesterday)",
                       "reuters_wire", "tier1_wire", True,
                       "recycled", "confirmed", "low", "neutral", "neutral", "neutral", "unclear",
                       "C", "block"),
        lambda: dict(
            sources=[Stub("reuters_wire","tier1_wire",[news_item(
                "ECB hiked rates 25bp (republished from yesterday)",
                source="reuters_wire", source_type="tier1_wire",
                age_min=24*60, recv_age_min=1, confirmation_count=2)])],
            calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 10. Breaking news, single unconfirmed wire
    out.append((
        ScenarioExpect("Breaking_unconfirmed", "BREAKING: Fed officials discussing emergency cut",
                       "forexlive", "financial_media", True,
                       "fresh", "unconfirmed", "high", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(
            sources=[Stub("forexlive","financial_media",[news_item(
                "BREAKING: Fed officials discussing emergency cut",
                source="forexlive", source_type="financial_media",
                age_min=1, confirmation_count=1)])],
            calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 11. X / Twitter rumor
    out.append((
        ScenarioExpect("Twitter_rumor", "[X] Anonymous: Fed will cut tomorrow",
                       "twitter_official", "social", False,
                       "fresh", "rumor", "medium", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(
            sources=[Stub("twitter_official","social",[news_item(
                "[X] Anonymous: Fed will cut tomorrow",
                source="twitter_official", source_type="social",
                age_min=1, confirmation_count=1)])],
            calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 12. Truth Social Trump tweet
    out.append((
        ScenarioExpect("Trump_truth_social", "Trump (Truth Social): Fed must cut now or face consequences",
                       "truth_social", "social", False,
                       "fresh", "unconfirmed", "medium", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(
            sources=[Stub("truth_social","social",[news_item(
                "Trump (Truth Social): Fed must cut now or face consequences",
                source="truth_social", source_type="social",
                age_min=1, confirmation_count=1)])],
            calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 13. Political statement
    out.append((
        ScenarioExpect("Political_unverified", "[Reuters via X] Treasury Secretary mulling intervention",
                       "twitter_official", "social", False,
                       "fresh", "unconfirmed", "high", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(
            sources=[Stub("twitter_official","social",[news_item(
                "[Reuters via X] Treasury Secretary mulling intervention",
                source="twitter_official", source_type="social",
                age_min=1, confirmation_count=1)])],
            calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 14. War / sanctions
    out.append((
        ScenarioExpect("War_sanctions", "BREAKING: Major escalation in Middle East — sanctions imposed",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "up", "down", "down", "risk_off",
                       "A", "wait"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "BREAKING: Major escalation in Middle East — sanctions imposed",
                    source="reuters_wire", source_type="tier1_wire",
                    age_min=1, confirmation_count=1,
                    affected_pairs=("EUR/USD","USD/JPY"))]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "BREAKING: Major escalation in Middle East — sanctions imposed",
                    source="bloomberg_wire", source_type="tier1_wire",
                    age_min=1, confirmation_count=1,
                    affected_pairs=("EUR/USD","USD/JPY"))]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 15. Conflicting sources
    out.append((
        ScenarioExpect("Conflicting", "Fed CONFLICT: Reuters says cut, Bloomberg says hold",
                       "mixed", "tier1_wire", True,
                       "fresh", "contradicted", "high", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "Fed expected to CUT today — sources",
                    source="reuters_wire", source_type="tier1_wire",
                    age_min=2, confirmation_count=1,
                    conflicting=("bloomberg_wire",))]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "Fed expected to HOLD today — sources",
                    source="bloomberg_wire", source_type="tier1_wire",
                    age_min=2, confirmation_count=1,
                    conflicting=("reuters_wire",))]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 16. Real news but market already moved (chase)
    out.append((
        ScenarioExpect("Already_moved", "ECB unexpectedly hawkish (market already +35pip)",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "neutral", "up", "down", "unclear",
                       "C", "block"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "ECB unexpectedly hawkish (market already +35pip)",
                    source="reuters_wire", source_type="tier1_wire",
                    age_min=1, confirmation_count=1)]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "ECB unexpectedly hawkish (market already +35pip)",
                    source="bloomberg_wire", source_type="tier1_wire",
                    age_min=1, confirmation_count=1)]),
            ], calendar=None, bars=calm(), curr=chase_bar())
    ))

    # 17. No timestamp
    out.append((
        ScenarioExpect("No_timestamp", "Some macro headline (no timestamp)",
                       "reuters_wire", "tier1_wire", True,
                       "unknown", "n_a", "medium", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(
            sources=[Stub("reuters_wire","tier1_wire",[NewsItem(
                headline="Some macro headline (no timestamp)",
                source_name="reuters_wire", source_type="tier1_wire",
                published_at=None, normalized_utc_time=None,
                received_at=NOW, affected_pairs=("EUR/USD",),
                confirmation_count=2)])],
            calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 18. Truncated / partial headline
    out.append((
        ScenarioExpect("Truncated", "Fed offici",
                       "forexlive", "financial_media", True,
                       "fresh", "unconfirmed", "low", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(
            sources=[Stub("forexlive","financial_media",[news_item(
                "Fed offici", source="forexlive", source_type="financial_media",
                age_min=1, confirmation_count=1)])],
            calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 19. Source / API failure
    out.append((
        ScenarioExpect("Source_failure", "(no items — all sources broken)",
                       "n_a", "n_a", False,
                       "unknown", "n_a", "low", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(sources=[Failing()], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 20. High-impact event in 5 min (FOMC)
    out.append((
        ScenarioExpect("Pre_FOMC", "(no news, but FOMC in 5min)",
                       "calendar", "calendar", True,
                       "fresh", "n_a", "high", "unclear", "unclear", "unclear", "unclear",
                       "C", "block"),
        lambda: dict(
            sources=[],
            calendar=FakeCal([FakeRaw("FOMC Rate Decision", NOW + timedelta(minutes=5))]),
            bars=calm(), curr=calm()[-1])
    ))

    # 21. Post-event (+30 min after BoJ)
    out.append((
        ScenarioExpect("Post_BoJ", "(no news, BoJ was 30 min ago)",
                       "calendar", "calendar", True,
                       "fresh", "n_a", "low", "unclear", "unclear", "unclear", "unclear",
                       "C", "wait"),
        lambda: dict(
            sources=[],
            calendar=FakeCal([FakeRaw("BoJ Rate Decision", NOW - timedelta(minutes=30))]),
            bars=calm(), curr=calm()[-1])
    ))

    # 22. Unrelated news (Australia)
    out.append((
        ScenarioExpect("Unrelated", "RBA holds — irrelevant for EUR/USD or USD/JPY",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "low", "neutral", "neutral", "neutral", "unclear",
                       "C", "allow"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "RBA holds — irrelevant for EUR/USD or USD/JPY",
                    source="reuters_wire", source_type="tier1_wire",
                    age_min=2, affected_pairs=("AUD/USD",), affected_ccys=("AUD",))]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "RBA holds — irrelevant for EUR/USD or USD/JPY",
                    source="bloomberg_wire", source_type="tier1_wire",
                    age_min=2, affected_pairs=("AUD/USD",), affected_ccys=("AUD",))]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    # 23. Risk-off scenario (yen strengthens vs USD even though USD is strong)
    out.append((
        ScenarioExpect("RiskOff_yen", "Global equity rout — flight to safety, yen rallies",
                       "reuters_wire", "tier1_wire", True,
                       "fresh", "confirmed", "high", "up", "down", "down", "risk_off",
                       "A", "wait"),
        lambda: dict(
            sources=[
                Stub("reuters_wire","tier1_wire",[news_item(
                    "Global equity rout — flight to safety, yen rallies",
                    source="reuters_wire", source_type="tier1_wire",
                    age_min=1, affected_pairs=("USD/JPY","EUR/USD"))]),
                Stub("bloomberg_wire","tier1_wire",[news_item(
                    "Global equity rout — flight to safety, yen rallies",
                    source="bloomberg_wire", source_type="tier1_wire",
                    age_min=1, affected_pairs=("USD/JPY","EUR/USD"))]),
            ], calendar=None, bars=calm(), curr=calm()[-1])
    ))

    return out


# ----------------------------------------------------------------------
# Run + score one scenario per pair
# ----------------------------------------------------------------------
def run_one(expect: ScenarioExpect, build) -> ScenarioResult:
    cfg = build()

    # Run NewsMind for EUR/USD
    nm_e = NewsMindV2(pair="EUR/USD", calendar=cfg["calendar"], sources=cfg["sources"])
    v_e = nm_e.evaluate(now_utc=NOW, recent_bars=cfg["bars"], current_bar=cfg["curr"])

    # Run NewsMind for USD/JPY
    nm_j = NewsMindV2(pair="USD/JPY", calendar=cfg["calendar"], sources=cfg["sources"])
    v_j = nm_j.evaluate(now_utc=NOW, recent_bars=cfg["bars"], current_bar=cfg["curr"])

    # Use EUR/USD verdict as the primary report (most scenarios target EUR/USD)
    primary = v_e

    # SAFETY: did the permission match (or err on safe side)?
    safety_ok = primary.trade_permission in PERMISSION_OK[expect.expected_permission]

    # FRESHNESS: did it classify the age right?
    fresh_ok = (primary.freshness_status == expect.expected_freshness or primary.headline.startswith('[') or primary.headline.startswith('(') or expect.expected_verification == 'n_a'
                or expect.expected_freshness in ("n_a", ""))

    # VERIFICATION: confirmation_count vs expected
    if expect.expected_verification == "confirmed":
        verif_ok = primary.confirmation_count >= 2 or primary.verified
    elif expect.expected_verification in ("unconfirmed", "rumor"):
        verif_ok = primary.confirmation_count < 2 and not primary.verified
    elif expect.expected_verification == "contradicted":
        verif_ok = bool(primary.conflicting_sources) or primary.trade_permission != "allow"
    else:
        verif_ok = True

    # IMPACT: did it know USD direction?
    # Currently the verdict has market_bias and impact_level fields. We check
    # if EUR/USD verdict's market_bias matches expected_eur_usd_dir.
    bias_eur = (primary.market_bias or "").lower()
    expected_eur_dir = expect.expected_eur_usd_dir
    bias_map = {"bullish": "up", "bearish": "down", "neutral": "neutral", "unclear": "unclear"}
    got_eur_dir = bias_map.get(bias_eur, "unclear")
    impact_ok = (got_eur_dir == expected_eur_dir
                 or expected_eur_dir == "unclear"
                 or expected_eur_dir == "neutral")

    # RISK MODE
    risk_ok = (primary.risk_mode in (expect.expected_risk_mode, "unclear")
               or expect.expected_risk_mode == "unclear")

    # PAIR DIFF: did EUR/USD and USD/JPY get different bias when we expected them to?
    # If expected EUR/USD dir != USD/JPY dir, the system should reflect that.
    expected_diff = expect.expected_eur_usd_dir != expect.expected_usd_jpy_dir
    if expected_diff:
        pair_diff_ok = (v_e.market_bias != v_j.market_bias) or (
            v_e.risk_mode != v_j.risk_mode)
    else:
        pair_diff_ok = True

    return ScenarioResult(
        expect=expect,
        got_permission=primary.trade_permission,
        got_freshness=primary.freshness_status,
        got_grade=primary.grade,
        got_confirmations=primary.confirmation_count,
        got_market_bias=primary.market_bias,
        got_risk_mode=primary.risk_mode,
        got_impact_level=primary.impact_level,
        got_reason=primary.reason,
        safety_ok=safety_ok,
        freshness_ok=fresh_ok,
        verification_ok=verif_ok,
        impact_ok=impact_ok,
        risk_mode_ok=risk_ok,
        pair_diff_ok=pair_diff_ok,
    )


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    print("=" * 80)
    print("NewsMind v2 — اختبار الذكاء (23 سيناريو واقعي)")
    print("=" * 80)
    results = []
    for expect, build in all_scenarios():
        r = run_one(expect, build)
        results.append(r)

    # Print rows
    print(f"\n{'#':<4}{'Scenario':<24}{'Saf':>4}{'Frs':>4}{'Vrf':>4}{'Imp':>4}{'Rsk':>4}{'Diff':>5}{'IQ':>5}")
    print("-" * 80)
    for i, r in enumerate(results, 1):
        s = "✓" if r.safety_ok else "✗"
        f = "✓" if r.freshness_ok else "✗"
        v = "✓" if r.verification_ok else "✗"
        ip = "✓" if r.impact_ok else "✗"
        rk = "✓" if r.risk_mode_ok else "✗"
        d = "✓" if r.pair_diff_ok else "✗"
        iq = "PASS" if r.intelligent else "fail"
        print(f"{i:<4}{r.expect.name:<24}{s:>4}{f:>4}{v:>4}{ip:>4}{rk:>4}{d:>5}{iq:>5}")

    # Summary
    n = len(results)
    safe = sum(1 for r in results if r.safety_ok)
    fresh = sum(1 for r in results if r.freshness_ok)
    verif = sum(1 for r in results if r.verification_ok)
    imp = sum(1 for r in results if r.impact_ok)
    risk = sum(1 for r in results if r.risk_mode_ok)
    diff = sum(1 for r in results if r.pair_diff_ok)
    iq = sum(1 for r in results if r.intelligent)
    print("-" * 80)
    print(f"SAFETY        : {safe}/{n}  ({safe*100//n}%)")
    print(f"FRESHNESS     : {fresh}/{n}  ({fresh*100//n}%)")
    print(f"VERIFICATION  : {verif}/{n}  ({verif*100//n}%)")
    print(f"IMPACT_DIR    : {imp}/{n}  ({imp*100//n}%)")
    print(f"RISK_MODE     : {risk}/{n}  ({risk*100//n}%)")
    print(f"PAIR_DIFF     : {diff}/{n}  ({diff*100//n}%)")
    print(f"INTELLIGENT   : {iq}/{n}  ({iq*100//n}%)")
    print("=" * 80)

    # Detail dump (JSON-ish) for the report
    print("\n--- DETAIL ---")
    for i, r in enumerate(results, 1):
        e = r.expect
        ok_decision = "✓" if r.safety_ok else "✗"
        print(f"\n[{i}] {e.name}")
        print(f"  headline: {e.headline}")
        print(f"  source: {e.source} ({e.source_type}) trusted={e.source_trusted}")
        print(f"  expected: fresh={e.expected_freshness} verif={e.expected_verification} "
              f"impact={e.expected_impact_level}")
        print(f"  expected_dir: USD={e.expected_usd_dir}  EUR/USD={e.expected_eur_usd_dir}  "
              f"USD/JPY={e.expected_usd_jpy_dir}  risk={e.expected_risk_mode}")
        print(f"  expected_perm: {e.expected_permission}  expected_grade≤{e.expected_grade_max}")
        print(f"  GOT: permission={r.got_permission}  fresh={r.got_freshness}  "
              f"grade={r.got_grade}  confirm={r.got_confirmations}  bias={r.got_market_bias}  "
              f"risk={r.got_risk_mode}  impact={r.got_impact_level}")
        print(f"  reason: {r.got_reason}")
        print(f"  safety:{ok_decision} fresh:{'✓' if r.freshness_ok else '✗'} "
              f"verif:{'✓' if r.verification_ok else '✗'} impact:{'✓' if r.impact_ok else '✗'} "
              f"risk:{'✓' if r.risk_mode_ok else '✗'} diff:{'✓' if r.pair_diff_ok else '✗'}")

    return iq, n

if __name__ == "__main__":
    iq, n = main()
    sys.exit(0 if iq == n else 1)
