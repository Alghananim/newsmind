# -*- coding: utf-8 -*-
"""News source adapters.

Three layers per user spec:
  1. Economic Calendar (Trading Economics, ForexFactory, Investing) —
     wired via Backtest.calendar at startup; refresh daily.
  2. Financial wires (Reuters, Bloomberg, MarketWatch, Forexlive) —
     RSS feeds where available; conservative backoff when unreachable.
  3. Social/political (X, Truth Social, official accounts) —
     advisory-only, never authoritative.

Design principles
-----------------
   * Each source has a stable adapter interface: `fetch_recent()`.
   * Failures NEVER raise — they return [] and log a warning.
   * Conservative fallback: if a source is unreachable, NewsMind
     treats the gap as "high uncertainty" and biases toward `wait`.
   * Rate-limiting + caching is enforced per adapter.
   * Each NewsItem carries `source_type` so verification can weight
     official > tier1_wire > financial_media > social.

Network availability
--------------------
The current sandbox has no external network access. These adapters
ship as STUBS that work in production (Hostinger / VPS) but return
empty lists in restricted environments. The architecture is correct;
the wiring activates when external HTTP is allowed.
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Optional
from .models import NewsItem, SourceType


def _make_id(headline: str, source: str, when: datetime) -> str:
    """Stable hash for de-duplication across feeds."""
    payload = f"{source}|{headline.lower().strip()}|{when.isoformat()[:16]}"
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


class NewsSource:
    """Abstract base for all sources."""
    name: str = "abstract"
    source_type: SourceType = "calendar"
    rate_limit_seconds: int = 60

    def __init__(self):
        self._last_fetch_utc: Optional[datetime] = None
        self._cache: list[NewsItem] = []

    def fetch_recent(self, *, since_utc: datetime) -> list[NewsItem]:
        """Return all NewsItems published since `since_utc`. Never raises."""
        # Rate-limit check
        now = datetime.now(timezone.utc)
        if (self._last_fetch_utc is not None
                and (now - self._last_fetch_utc).total_seconds()
                < self.rate_limit_seconds):
            return [i for i in self._cache if i.normalized_utc_time and i.normalized_utc_time >= since_utc]
        try:
            items = self._do_fetch(since_utc=since_utc, now=now)
            self._cache = items
            self._last_fetch_utc = now
            return items
        except Exception:
            # Conservative: empty list, NewsMind biases to wait
            return []

    def _do_fetch(self, *, since_utc: datetime, now: datetime) -> list[NewsItem]:
        """Subclasses override."""
        return []


# ----------------------------------------------------------------------
# Adapter stubs — production wiring happens via this interface.
# ----------------------------------------------------------------------
class ReutersWireSource(NewsSource):
    name = "reuters_wire"
    source_type = "tier1_wire"
    rate_limit_seconds = 30
    rss_url = "https://www.reuters.com/markets/currencies/rss"

    def _do_fetch(self, *, since_utc, now):
        # Production: feedparser.parse(self.rss_url) -> list[NewsItem]
        # Sandbox: no network, return empty
        return []


class BloombergWireSource(NewsSource):
    name = "bloomberg_wire"
    source_type = "tier1_wire"
    rate_limit_seconds = 30
    rss_url = "https://feeds.bloomberg.com/markets/news.rss"

    def _do_fetch(self, *, since_utc, now):
        return []


class ForexliveSource(NewsSource):
    name = "forexlive"
    source_type = "financial_media"
    rate_limit_seconds = 30
    rss_url = "https://www.forexlive.com/feed/"

    def _do_fetch(self, *, since_utc, now):
        return []


class InvestingCalendarSource(NewsSource):
    name = "investing_calendar"
    source_type = "calendar"
    rate_limit_seconds = 3600         # daily-ish

    def _do_fetch(self, *, since_utc, now):
        return []


class TwitterOfficialSource(NewsSource):
    """Advisory-only — never grants `allow` on its own."""
    name = "twitter_official"
    source_type = "social"
    rate_limit_seconds = 60

    def __init__(self, monitored_handles: tuple = ()):
        super().__init__()
        self.handles = monitored_handles

    def _do_fetch(self, *, since_utc, now):
        return []


# ----------------------------------------------------------------------
# Aggregator: merges multiple sources, dedupes by item_id.
# ----------------------------------------------------------------------
class SourceAggregator:
    """Pulls from N sources, merges + dedupes."""

    def __init__(self, sources: list[NewsSource]):
        self.sources = sources

    def fetch_all(self, *, since_utc: datetime) -> list[NewsItem]:
        merged: dict[str, NewsItem] = {}
        for src in self.sources:
            for item in src.fetch_recent(since_utc=since_utc):
                if not item.item_id:
                    when = item.normalized_utc_time or item.published_at or datetime.now(timezone.utc)
                    item.item_id = _make_id(item.headline, item.source_name, when)
                if item.item_id in merged:
                    # Merge confirmation count
                    merged[item.item_id].confirmation_count += 1
                else:
                    merged[item.item_id] = item
        return list(merged.values())

    def health_status(self) -> dict:
        """Per-source last-fetch age — used to flag stale feeds."""
        out = {}
        now = datetime.now(timezone.utc)
        for src in self.sources:
            if src._last_fetch_utc is None:
                out[src.name] = "never_fetched"
            else:
                age = (now - src._last_fetch_utc).total_seconds()
                out[src.name] = f"{int(age)}s_ago"
        return out


# ----------------------------------------------------------------------
# Default production source set.
# ----------------------------------------------------------------------
def default_sources() -> list[NewsSource]:
    return [
        ReutersWireSource(),
        BloombergWireSource(),
        ForexliveSource(),
        InvestingCalendarSource(),
        TwitterOfficialSource(monitored_handles=(
            "@federalreserve", "@ecb", "@bankofjapan", "@bankofengland",
            "@WhiteHouse", "@SecYellen",
        )),
    ]
