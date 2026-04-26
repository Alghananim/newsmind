# -*- coding: utf-8 -*-
"""NewsMindV2 — orchestrator for the news pipeline.

Contract:
    nm = NewsMindV2(pair="EUR/USD", calendar=historical_calendar)
    verdict = nm.evaluate(now_utc=..., recent_bars=[...], current_bar=...)

The verdict.trade_permission is what GateMind reads. "block" means
no trade may fire; "wait" means hold off; "allow" means cleared.

Pipeline per evaluate():
    1. Pull recent items from sources (rate-limited)
    2. Score scheduled-event windows (always available, no network)
    3. For each item: classify freshness, detect chase
    4. Run permission engine; collect verdicts
    5. If any item -> "block", return that
    6. Else if any item -> "wait", return that
    7. Else return "allow" (or "block" if no scheduled-event signal)
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from .models import NewsItem, NewsVerdict, EventSchedule
from .freshness import FreshnessAnalyzer
from .chase_detector import ChaseDetector
from .event_scheduler import EventScheduler
from .sources import SourceAggregator, default_sources
from .permission import PermissionEngine


class NewsMindV2:
    """Per-pair news orchestrator."""

    def __init__(self, *, pair: str, calendar=None,
                 sources: Optional[list] = None,
                 require_confirmations: int = 2):
        self.pair = pair
        self.scheduler = EventScheduler(calendar=calendar)
        self.freshness = FreshnessAnalyzer()
        self.chase = ChaseDetector()
        self.aggregator = SourceAggregator(sources or default_sources())
        self.permission = PermissionEngine(
            require_confirmations=require_confirmations,
            unverified_block=True,
        )
        # Tracking
        self._last_eval_utc: Optional[datetime] = None
        self._last_verdict: Optional[NewsVerdict] = None

    # ------------------------------------------------------------------
    # Public API.
    # ------------------------------------------------------------------
    def evaluate(self, *, now_utc: Optional[datetime] = None,
                 recent_bars: list = None,
                 current_bar=None) -> NewsVerdict:
        """Produce one verdict for the current moment."""
        now_utc = now_utc or datetime.now(timezone.utc)
        recent_bars = recent_bars or []

        # 1) Scheduled event windows for THIS pair
        in_pre, in_during, in_post, nearest_event = self.scheduler.windows_for(
            now_utc=now_utc, pair=self.pair)

        # If we're inside a high-impact blackout, return immediately
        if (in_during or in_pre) and nearest_event and nearest_event.impact_level == "high":
            v = NewsVerdict(
                headline=f"[scheduled] {nearest_event.name}",
                source_name="event_scheduler",
                source_type="calendar",
                normalized_utc_time=nearest_event.when_utc,
                trade_permission="block",
                reason=f"scheduled_high_impact_{nearest_event.name}_"
                       f"{'during' if in_during else 'pre'}_window",
                impact_level=nearest_event.impact_level,
                affected_assets=nearest_event.affected_pairs,
                event_id=nearest_event.event_id,
                is_scheduled_event=True,
                pre_event_window=in_pre,
                post_event_window=False,
            )
            self._last_verdict = v
            self._last_eval_utc = now_utc
            return v

        # 2) Pull recent news from all sources (in production)
        since = now_utc.replace(hour=now_utc.hour - 2 if now_utc.hour >= 2 else 0)
        try:
            items = self.aggregator.fetch_all(since_utc=since)
        except Exception:
            items = []

        # Filter to pair's currencies
        relevant = [i for i in items
                    if not i.affected_pairs or self.pair in i.affected_pairs]

        # No news to act on? Apply post-event cool-down or allow.
        if not relevant:
            if in_post and nearest_event:
                v = NewsVerdict(
                    headline=f"[post-cooldown] {nearest_event.name}",
                    source_name="event_scheduler",
                    trade_permission="wait",
                    reason=f"post_event_cooldown:{nearest_event.name}",
                    impact_level=nearest_event.impact_level,
                    event_id=nearest_event.event_id,
                    is_scheduled_event=True,
                    post_event_window=True,
                )
            else:
                v = NewsVerdict(
                    headline="(no news)",
                    source_name="news_v2",
                    trade_permission="allow",
                    reason="no_blocking_news",
                    grade="C",
                )
            self._last_verdict = v
            self._last_eval_utc = now_utc
            return v

        # 3) For each relevant item, run freshness + chase + permission
        verdicts: list[NewsVerdict] = []
        for item in relevant:
            status, age_s, _reason = self.freshness.classify(item, now_utc=now_utc)

            # Chase check (needs recent bars)
            if recent_bars and current_bar is not None:
                ca = self.chase.assess(recent_bars, current_bar)
                chase_dec, chase_reason = ca.decision, ca.reason
            else:
                chase_dec, chase_reason = "allow", "no_bar_context"

            v = self.permission.decide(
                item=item, freshness_status=status, age_seconds=age_s,
                in_pre_event=in_pre, in_during_event=in_during,
                in_post_event=in_post, nearest_event=nearest_event,
                chase_decision=chase_dec, chase_reason=chase_reason,
            )
            verdicts.append(v)

        # 4) Worst-case wins (block > wait > allow)
        order = {"block": 2, "wait": 1, "allow": 0}
        verdicts.sort(key=lambda v: -order[v.trade_permission])
        final = verdicts[0]
        self._last_verdict = final
        self._last_eval_utc = now_utc
        return final

    def health(self) -> dict:
        """Return source health + last verdict for diagnostics."""
        return {
            "source_health": self.aggregator.health_status(),
            "last_eval_utc": self._last_eval_utc.isoformat() if self._last_eval_utc else None,
            "last_verdict": self._last_verdict.to_dict() if self._last_verdict else None,
            "pair": self.pair,
        }
