# -*- coding: utf-8 -*-
"""FreshnessAnalyzer — classify a NewsItem by age.

Tiers:
    fresh    — published <= 5 minutes ago. ACTIONABLE.
    recent   — 5–30 minutes. ACTIONABLE with caution.
    stale    — 30–60 minutes. NOT actionable as primary signal.
    recycled — older event re-published with new wrapper. NEVER actionable.
    unknown  — missing/invalid timestamp. CONSERVATIVE = treat as stale.

Recycling detection
-------------------
A "recycled" news item has a normalized_utc_time older than 60 minutes
but a received_at timestamp that's recent (just landed in our feed).
This catches:
  * News aggregators replaying old events
  * Bots re-posting headlines hours later
  * Mistimed RSS feeds
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from .models import NewsItem, FreshnessStatus


class FreshnessAnalyzer:
    """Stateless. Configure thresholds; call .classify(item) per news event."""

    def __init__(self,
                 fresh_seconds: int = 5 * 60,
                 recent_seconds: int = 30 * 60,
                 stale_seconds: int = 60 * 60,
                 recycle_lag_seconds: int = 30 * 60,
                 ):
        self.fresh_s = fresh_seconds
        self.recent_s = recent_seconds
        self.stale_s = stale_seconds
        self.recycle_lag_s = recycle_lag_seconds

    def classify(self, item: NewsItem,
                 *, now_utc: datetime = None) -> tuple[FreshnessStatus, float, str]:
        """Return (status, age_seconds, reason).

        `now_utc` is the reference 'now' (defaults to system clock).
        """
        now_utc = now_utc or datetime.now(timezone.utc)

        # 1) Missing or invalid published_at -> unknown (treat as stale)
        if item.normalized_utc_time is None and item.published_at is None:
            return "unknown", -1.0, "no_publish_timestamp"

        pub_time = item.normalized_utc_time or item.published_at
        if pub_time.tzinfo is None:
            pub_time = pub_time.replace(tzinfo=timezone.utc)

        age_seconds = (now_utc - pub_time).total_seconds()

        # 2) Future publish time -> unknown (clock skew)
        if age_seconds < -60:
            return "unknown", age_seconds, "publish_in_future_clock_skew"

        # 3) Recycled detection: old publish + just-received
        if item.received_at is not None:
            recv_time = item.received_at
            if recv_time.tzinfo is None:
                recv_time = recv_time.replace(tzinfo=timezone.utc)
            recv_lag = (recv_time - pub_time).total_seconds()
            if age_seconds > self.stale_s and recv_lag > self.recycle_lag_s:
                return ("recycled", age_seconds,
                        f"published_{int(age_seconds/60)}min_ago_but_received_just_now")

        # 4) Standard tiers
        if age_seconds <= self.fresh_s:
            return "fresh", age_seconds, f"age_{int(age_seconds)}s"
        if age_seconds <= self.recent_s:
            return "recent", age_seconds, f"age_{int(age_seconds/60)}min"
        if age_seconds <= self.stale_s:
            return "stale", age_seconds, f"age_{int(age_seconds/60)}min"
        return "stale", age_seconds, f"old_age_{int(age_seconds/60)}min"

    @staticmethod
    def is_actionable(status: FreshnessStatus) -> bool:
        """Hard rule: only fresh and recent are actionable as PRIMARY signals."""
        return status in ("fresh", "recent")
