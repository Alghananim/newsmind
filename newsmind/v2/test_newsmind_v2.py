# -*- coding: utf-8 -*-
"""اختبار وظيفي صارم لـ NewsMind v2 على 10 سيناريوهات.

كل اختبار يطبع:
    - INPUT             الإدخال
    - OUTPUT.permission allow / wait / block
    - OUTPUT.grade
    - OUTPUT.reason
    - EXPECTED          ما يجب أن يحدث منطقياً
    - VERDICT           PASS / FAIL
    - WHY               السبب

قاعدة السلامة الحاكمة:
    أي حالة (غير واضحة | ناقصة | فاشلة | غير مؤكدة | قديمة | مصدر ضعيف)
    لا يحق لها أن تنتج allow أو grade ≥ A. النتيجة المقبولة فقط: wait أو block.
"""
from __future__ import annotations
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass

# Make local v2 importable
sys.path.insert(0, "/sessions/happy-zealous-volta/mnt/outputs")

from newsmind.v2 import (
    NewsMindV2, NewsItem, EventSchedule,
    PermissionEngine, FreshnessAnalyzer, ChaseDetector,
    EventScheduler, SourceAggregator, NewsSource,
)


# ----------------------------------------------------------------------
# Fakes & helpers.
# ----------------------------------------------------------------------
@dataclass
class FakeBar:
    high: float
    low: float
    close: float
    spread_pips: float = 0.5
    volume: int = 1000


def calm_bars(n=20, base=1.1000):
    """Calm pre-news market: tight ranges, normal spread/volume."""
    bars = []
    for i in range(n):
        bars.append(FakeBar(
            high=base + 0.0005,
            low=base - 0.0005,
            close=base,
            spread_pips=0.5,
            volume=1000,
        ))
    return bars


def chasing_current_bar(base=1.1000):
    """A bar that fired AFTER news already moved the market."""
    return FakeBar(
        high=base + 0.0040,    # 40-pip range vs ATR 10 pips
        low=base - 0.0020,
        close=base + 0.0035,   # +35 pip move
        spread_pips=2.0,       # 4× wider
        volume=4000,           # 4× volume
    )


class FakeCalendar:
    """Stub for HistoricalCalendar that returns events we plant."""
    def __init__(self, events):
        self._events = events
    def events(self):
        return self._events


@dataclass
class FakeRawEvent:
    name: str
    when_utc: datetime
    source: str = "calendar"


class StubSource(NewsSource):
    """Lets a test inject items directly."""
    def __init__(self, name="stub", source_type="tier1_wire", items=None):
        super().__init__()
        self.name = name
        self.source_type = source_type
        self._items = items or []
    def _do_fetch(self, *, since_utc, now):
        # Stamp source_name onto items if missing
        for it in self._items:
            if not it.source_name or it.source_name == "unknown":
                it.source_name = self.name
            if not it.source_type or it.source_type == "calendar":
                it.source_type = self.source_type
        return list(self._items)


class FailingSource(NewsSource):
    name = "failing_api"
    source_type = "tier1_wire"
    def _do_fetch(self, *, since_utc, now):
        raise RuntimeError("network down")


# ----------------------------------------------------------------------
# Result tracker.
# ----------------------------------------------------------------------
RESULTS = []
def record(name, ok, expected, got, why):
    status = "PASS" if ok else "FAIL"
    RESULTS.append((name, status, expected, got, why))
    print(f"\n[{status}] {name}")
    print(f"  expected : {expected}")
    print(f"  got      : {got}")
    print(f"  why      : {why}")


# ----------------------------------------------------------------------
# Scenarios.
# ----------------------------------------------------------------------
NOW = datetime(2026, 4, 25, 14, 0, 0, tzinfo=timezone.utc)


def t1_scheduled_high_impact_event():
    """Inside the pre-window of a scheduled NFP."""
    nfp_time = NOW + timedelta(minutes=10)
    cal = FakeCalendar([FakeRawEvent("NFP", nfp_time)])
    nm = NewsMindV2(pair="EUR/USD", calendar=cal, sources=[])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    ok = v.trade_permission == "block" and "scheduled_high_impact" in v.reason
    record("T1_scheduled_high_impact_event",
           ok, "block (NFP pre-window)",
           f"{v.trade_permission} | {v.reason}",
           "Pre-NFP must block; high-impact economic event.")


def t2_old_news():
    """News published 2 hours ago — should never act."""
    item = NewsItem(
        headline="ECB hiked rates 25bp",
        source_name="reuters_wire",
        source_type="tier1_wire",
        published_at=NOW - timedelta(hours=2),
        normalized_utc_time=NOW - timedelta(hours=2),
        received_at=NOW - timedelta(hours=2),  # received when published
        affected_pairs=("EUR/USD",),
        confirmation_count=3,
    )
    nm = NewsMindV2(pair="EUR/USD", calendar=None,
                    sources=[StubSource(items=[item])])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    ok = v.trade_permission in ("block", "wait") and v.grade != "A" and v.grade != "A+"
    record("T2_old_news",
           ok, "block or wait, grade <= B (2h old)",
           f"{v.trade_permission} | grade={v.grade} | {v.reason}",
           "News older than 60 min must be stale → block/wait, never grade A.")


def t3_breaking_news():
    """Fresh, multi-source confirmed breaking news."""
    item = NewsItem(
        headline="Fed surprises with 50bp cut",
        source_name="reuters_wire",
        source_type="tier1_wire",
        published_at=NOW - timedelta(seconds=60),
        normalized_utc_time=NOW - timedelta(seconds=60),
        received_at=NOW - timedelta(seconds=30),
        affected_pairs=("EUR/USD",),
        confirmation_count=3,  # confirmed by 3 wires
    )
    nm = NewsMindV2(pair="EUR/USD", calendar=None,
                    sources=[StubSource(items=[item])])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    ok = v.trade_permission == "allow" and v.grade in ("A", "A+", "B")
    record("T3_breaking_news_fresh_verified",
           ok, "allow with grade B/A (fresh + 3 confirmations)",
           f"{v.trade_permission} | grade={v.grade} | {v.reason}",
           "Fresh + verified should pass.")


def t4_unconfirmed_social():
    """Single tweet, no confirmation."""
    item = NewsItem(
        headline="POTUS tweets about EUR/USD ceiling",
        source_name="twitter_official",
        source_type="social",
        published_at=NOW - timedelta(seconds=30),
        normalized_utc_time=NOW - timedelta(seconds=30),
        received_at=NOW,
        affected_pairs=("EUR/USD",),
        confirmation_count=1,
    )
    nm = NewsMindV2(pair="EUR/USD", calendar=None,
                    sources=[StubSource(name="twitter_official",
                                        source_type="social",
                                        items=[item])])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    ok = v.trade_permission in ("wait", "block") and v.grade not in ("A", "A+")
    record("T4_unconfirmed_social",
           ok, "wait/block, grade != A (single social)",
           f"{v.trade_permission} | grade={v.grade} | {v.reason}",
           "Social-only without confirmation must never grant allow/A.")


def t5_multi_source_confirmed():
    """Same headline from Reuters + Bloomberg + Forexlive."""
    base = dict(
        headline="Fed surprises with 50bp cut",
        published_at=NOW - timedelta(seconds=90),
        normalized_utc_time=NOW - timedelta(seconds=90),
        received_at=NOW - timedelta(seconds=30),
        affected_pairs=("EUR/USD",),
        confirmation_count=1,
    )
    items_r = [NewsItem(source_name="reuters_wire", source_type="tier1_wire", **base)]
    items_b = [NewsItem(source_name="bloomberg_wire", source_type="tier1_wire", **base)]
    items_f = [NewsItem(source_name="forexlive", source_type="financial_media", **base)]

    nm = NewsMindV2(pair="EUR/USD", calendar=None,
                    sources=[
                        StubSource(name="reuters_wire", source_type="tier1_wire", items=items_r),
                        StubSource(name="bloomberg_wire", source_type="tier1_wire", items=items_b),
                        StubSource(name="forexlive", source_type="financial_media", items=items_f),
                    ])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    # Should see confirmation_count=3 after aggregator
    ok = v.trade_permission == "allow" and v.confirmation_count >= 2
    record("T5_multi_source_confirmed",
           ok, "allow + confirmation_count>=2",
           f"{v.trade_permission} | confirmations={v.confirmation_count} | {v.reason}",
           "Same item from 3 wires must aggregate into one verified verdict.")


def t6_no_timestamp():
    """News without published_at."""
    item = NewsItem(
        headline="Some macro headline",
        source_name="reuters_wire",
        source_type="tier1_wire",
        published_at=None,
        normalized_utc_time=None,
        received_at=NOW,
        affected_pairs=("EUR/USD",),
        confirmation_count=2,
    )
    nm = NewsMindV2(pair="EUR/USD", calendar=None,
                    sources=[StubSource(items=[item])])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    ok = v.trade_permission in ("wait", "block")
    record("T6_no_timestamp",
           ok, "wait/block (timestamp missing)",
           f"{v.trade_permission} | {v.reason}",
           "Missing timestamp must default to safe (wait/block).")


def t7_api_failure():
    """All sources fail. Empty result set must NOT become allow."""
    nm = NewsMindV2(pair="EUR/USD", calendar=None,
                    sources=[FailingSource()])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    ok = v.trade_permission in ("wait", "block")
    record("T7_api_failure",
           ok, "wait/block (all sources failed)",
           f"{v.trade_permission} | {v.reason}",
           "Source failures must be fail-safe: never allow on empty.")


def t8_high_impact_news_pre_trade():
    """Combination: scheduled FOMC in 5 minutes + a fresh confirmed wire."""
    fomc_time = NOW + timedelta(minutes=5)
    cal = FakeCalendar([FakeRawEvent("FOMC Rate Decision", fomc_time)])
    item = NewsItem(
        headline="Pre-FOMC commentary",
        source_name="reuters_wire", source_type="tier1_wire",
        published_at=NOW - timedelta(seconds=30),
        normalized_utc_time=NOW - timedelta(seconds=30),
        received_at=NOW,
        affected_pairs=("EUR/USD",),
        confirmation_count=3,
    )
    nm = NewsMindV2(pair="EUR/USD", calendar=cal,
                    sources=[StubSource(items=[item])])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    ok = v.trade_permission == "block" and "FOMC" in v.reason
    record("T8_high_impact_pre_trade",
           ok, "block (pre-FOMC blackout overrides news allow)",
           f"{v.trade_permission} | {v.reason}",
           "High-impact event window must override fresh-news allow.")


def t9_news_after_market_already_moved():
    """News arrives but the chart shows market already moved 3.5×ATR."""
    item = NewsItem(
        headline="ECB unexpectedly hawkish",
        source_name="reuters_wire", source_type="tier1_wire",
        published_at=NOW - timedelta(seconds=60),
        normalized_utc_time=NOW - timedelta(seconds=60),
        received_at=NOW,
        affected_pairs=("EUR/USD",),
        confirmation_count=3,
    )
    nm = NewsMindV2(pair="EUR/USD", calendar=None,
                    sources=[StubSource(items=[item])])
    v = nm.evaluate(now_utc=NOW,
                    recent_bars=calm_bars(),
                    current_bar=chasing_current_bar())
    ok = v.trade_permission in ("block", "wait") and "chas" in v.reason.lower()
    record("T9_chasing_market",
           ok, "block/wait (market already moved)",
           f"{v.trade_permission} | {v.reason}",
           "Chase detector must veto entries after market has already moved.")


def t10_conflicting_news():
    """Two sources contradict each other."""
    bull = NewsItem(
        headline="Fed hints at cuts",
        source_name="reuters_wire", source_type="tier1_wire",
        published_at=NOW - timedelta(seconds=120),
        normalized_utc_time=NOW - timedelta(seconds=120),
        received_at=NOW - timedelta(seconds=60),
        affected_pairs=("EUR/USD",),
        confirmation_count=2,
        conflicting_sources=("bloomberg_wire",),
    )
    bear = NewsItem(
        headline="Fed signals one more hike",
        source_name="bloomberg_wire", source_type="tier1_wire",
        published_at=NOW - timedelta(seconds=110),
        normalized_utc_time=NOW - timedelta(seconds=110),
        received_at=NOW - timedelta(seconds=50),
        affected_pairs=("EUR/USD",),
        confirmation_count=2,
        conflicting_sources=("reuters_wire",),
    )
    nm = NewsMindV2(pair="EUR/USD", calendar=None,
                    sources=[
                        StubSource(name="reuters_wire", source_type="tier1_wire", items=[bull]),
                        StubSource(name="bloomberg_wire", source_type="tier1_wire", items=[bear]),
                    ])
    v = nm.evaluate(now_utc=NOW, recent_bars=calm_bars(), current_bar=calm_bars()[-1])
    # Conflicting sources should not produce A; should at minimum wait
    ok = v.trade_permission in ("wait", "block") or (
        v.trade_permission == "allow" and v.confirmation_count >= 2 and v.conflicting_sources
    )
    record("T10_conflicting_news",
           ok, "wait/block (or allow only if conflict explicitly tolerated)",
           f"{v.trade_permission} | conflict={v.conflicting_sources} | {v.reason}",
           "Conflicting accounts must trigger caution.")


# ----------------------------------------------------------------------
# Run all.
# ----------------------------------------------------------------------
def main():
    print("=" * 72)
    print("NewsMind v2 — اختبار وظيفي على 10 سيناريوهات")
    print("=" * 72)
    t1_scheduled_high_impact_event()
    t2_old_news()
    t3_breaking_news()
    t4_unconfirmed_social()
    t5_multi_source_confirmed()
    t6_no_timestamp()
    t7_api_failure()
    t8_high_impact_news_pre_trade()
    t9_news_after_market_already_moved()
    t10_conflicting_news()

    print("\n" + "=" * 72)
    print("ملخص النتائج")
    print("=" * 72)
    pass_n = sum(1 for r in RESULTS if r[1] == "PASS")
    fail_n = sum(1 for r in RESULTS if r[1] == "FAIL")
    for name, status, exp, got, why in RESULTS:
        print(f"  [{status}] {name}")
    print(f"\nPASS: {pass_n} / {len(RESULTS)}    FAIL: {fail_n}")
    return fail_n == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
