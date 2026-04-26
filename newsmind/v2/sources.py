# -*- coding: utf-8 -*-
"""News source adapters (post-audit clean rewrite).

POST-AUDIT FIXES
----------------
* bug #11 — _make_id no longer includes source_name, so the same
  headline from Reuters + Bloomberg collides into one item, enabling
  cross-source confirmation counting.
* bug #11 (deeper) — confirmation count only increments for DISTINCT
  source_names, not for items the same source returns twice.
* bug #25/26 — every source tracks last_fetch_status (ok/empty/error/never)
  and SourceAggregator exposes fetch_status_summary() so the orchestrator
  can fail-SAFE (wait) when all sources are dead, not fail-OPEN (allow).
"""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from typing import Optional
from .models import NewsItem, SourceType


def _make_id(headline: str, source: str, when: datetime) -> str:
    """Cross-source-collision-friendly hash."""
    minute_bucket = (when.minute // 5) * 5
    bucketed = when.replace(minute=minute_bucket, second=0, microsecond=0)
    payload = f"{headline.lower().strip()}|{bucketed.isoformat()[:16]}"
    return hashlib.sha1(payload.encode()).hexdigest()[:12]


class NewsSource:
    name: str = "abstract"
    source_type: SourceType = "calendar"
    rate_limit_seconds: int = 60

    def __init__(self):
        self._last_fetch_utc: Optional[datetime] = None
        self._cache: list[NewsItem] = []
        self.last_fetch_status: str = "never"   # ok | empty | error | never

    def fetch_recent(self, *, since_utc: datetime) -> list[NewsItem]:
        now = datetime.now(timezone.utc)
        if (self._last_fetch_utc is not None
                and (now - self._last_fetch_utc).total_seconds()
                < self.rate_limit_seconds):
            return [i for i in self._cache
                    if i.normalized_utc_time and i.normalized_utc_time >= since_utc]
        try:
            items = self._do_fetch(since_utc=since_utc, now=now)
            self._cache = items
            self._last_fetch_utc = now
            self.last_fetch_status = "ok" if items else "empty"
            return items
        except Exception:
            self.last_fetch_status = "error"
            return []

    def _do_fetch(self, *, since_utc: datetime, now: datetime) -> list[NewsItem]:
        return []


# --- adapter stubs --------------------------------------------------
class ReutersWireSource(NewsSource):
    name = "reuters_wire"; source_type = "tier1_wire"; rate_limit_seconds = 30
    rss_url = "https://www.reuters.com/markets/currencies/rss"
    def _do_fetch(self, *, since_utc, now): return []

class BloombergWireSource(NewsSource):
    name = "bloomberg_wire"; source_type = "tier1_wire"; rate_limit_seconds = 30
    rss_url = "https://feeds.bloomberg.com/markets/news.rss"
    def _do_fetch(self, *, since_utc, now): return []

class ForexliveSource(NewsSource):
    name = "forexlive"; source_type = "financial_media"; rate_limit_seconds = 30
    rss_url = "https://www.forexlive.com/feed/"
    def _do_fetch(self, *, since_utc, now): return []

class InvestingCalendarSource(NewsSource):
    name = "investing_calendar"; source_type = "calendar"; rate_limit_seconds = 3600
    def _do_fetch(self, *, since_utc, now): return []

class TwitterOfficialSource(NewsSource):
    name = "twitter_official"; source_type = "social"; rate_limit_seconds = 60
    def __init__(self, monitored_handles: tuple = ()):
        super().__init__()
        self.handles = monitored_handles
    def _do_fetch(self, *, since_utc, now): return []


# --- aggregator -----------------------------------------------------
class SourceAggregator:
    def __init__(self, sources: list[NewsSource]):
        self.sources = sources

    def fetch_all(self, *, since_utc: datetime) -> list[NewsItem]:
        merged: dict[str, NewsItem] = {}
        for src in self.sources:
            for item in src.fetch_recent(since_utc=since_utc):
                if not item.item_id:
                    when = (item.normalized_utc_time or item.published_at
                            or datetime.now(timezone.utc))
                    item.item_id = _make_id(item.headline, item.source_name, when)
                if item.item_id in merged:
                    existing = merged[item.item_id]
                    seen = existing.raw.setdefault("_seen_sources", set())
                    if item.source_name not in seen:
                        existing.confirmation_count += 1
                        seen.add(item.source_name)
                else:
                    item.raw.setdefault("_seen_sources", set()).add(item.source_name)
                    merged[item.item_id] = item
        return list(merged.values())

    def fetch_status_summary(self) -> dict:
        ok = sum(1 for s in self.sources if s.last_fetch_status == "ok")
        empty = sum(1 for s in self.sources if s.last_fetch_status == "empty")
        error = sum(1 for s in self.sources if s.last_fetch_status == "error")
        never = sum(1 for s in self.sources if s.last_fetch_status == "never")
        total = len(self.sources)
        return {
            "ok": ok, "empty": empty, "error": error, "never": never, "total": total,
            "all_failed": (error + never) == total and total > 0,
            "any_alive": ok > 0 or empty > 0,
        }

    def health_status(self) -> dict:
        out = {}
        now = datetime.now(timezone.utc)
        for src in self.sources:
            if src._last_fetch_utc is None:
                out[src.name] = "never_fetched"
            else:
                age = (now - src._last_fetch_utc).total_seconds()
                out[src.name] = f"{int(age)}s_ago"
        return out


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
