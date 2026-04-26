# -*- coding: utf-8 -*-
"""PermissionEngine — converts all signals into allow/wait/block.

Decision matrix (highest priority first; first match wins):

   1. inside scheduled high-impact event window  -> BLOCK
   2. recycled/stale news                         -> BLOCK
   3. chase signals 3+                            -> BLOCK
   4. unverified social-only news                 -> WAIT
   5. unknown timestamp                           -> WAIT
   6. confirmation_count == 1 + medium impact     -> WAIT
   7. fresh + confirmed + scheduled event over    -> ALLOW
   8. fresh + confirmed + non-scheduled           -> ALLOW
   9. recent + confirmed + outside event windows  -> ALLOW
  10. anything else                                -> BLOCK (default deny)

The engine never raises; on any internal error, it returns BLOCK.
This is a SAFETY-FIRST design: when in doubt, do not trade.
"""
from __future__ import annotations
from datetime import datetime, timezone
from .models import NewsItem, NewsVerdict, EventSchedule, FreshnessStatus, TradePermission


class PermissionEngine:
    def __init__(self,
                 require_confirmations: int = 2,
                 unverified_block: bool = True):
        self.require_confirmations = require_confirmations
        self.unverified_block = unverified_block

    def decide(self,
               *,
               item: NewsItem,
               freshness_status: FreshnessStatus,
               age_seconds: float,
               in_pre_event: bool,
               in_during_event: bool,
               in_post_event: bool,
               nearest_event: EventSchedule = None,
               chase_decision: str = "allow",
               chase_reason: str = "",
               ) -> NewsVerdict:
        try:
            return self._decide_inner(
                item, freshness_status, age_seconds,
                in_pre_event, in_during_event, in_post_event,
                nearest_event, chase_decision, chase_reason,
            )
        except Exception as e:
            # Default-deny on any internal error
            return NewsVerdict(
                headline=item.headline if item else "",
                source_name=item.source_name if item else "",
                trade_permission="block",
                reason=f"engine_error:{type(e).__name__}",
                grade="C",
            )

    def _decide_inner(self, item, freshness_status, age_seconds,
                      in_pre, in_during, in_post,
                      nearest_event, chase_decision, chase_reason):
        verdict = NewsVerdict(
            headline=item.headline,
            source_name=item.source_name,
            source_type=item.source_type,
            published_at=item.published_at,
            received_at=item.received_at,
            normalized_utc_time=item.normalized_utc_time,
            news_age_seconds=age_seconds,
            freshness_status=freshness_status,
            confirmation_count=item.confirmation_count,
            conflicting_sources=item.conflicting_sources,
            affected_assets=item.affected_pairs or item.affected_assets,
            sources_checked=(item.source_name,),
            event_id=getattr(nearest_event, "event_id", None) if nearest_event else None,
            is_scheduled_event=item.is_scheduled_event,
            pre_event_window=in_pre,
            post_event_window=in_post,
        )

        # --- Rule 1: inside high-impact event blackout
        if (in_during or in_pre) and nearest_event and nearest_event.impact_level == "high":
            verdict.trade_permission = "block"
            verdict.reason = (
                f"high_impact_event_blackout:{nearest_event.name}_"
                f"{'during' if in_during else 'pre'}"
            )
            verdict.impact_level = "high"
            return verdict

        # --- Rule 2: recycled / stale
        if freshness_status in ("recycled", "stale"):
            verdict.trade_permission = "block"
            verdict.reason = f"news_age:{freshness_status}"
            return verdict

        # --- Rule 3: chase detected
        if chase_decision == "block":
            verdict.trade_permission = "block"
            verdict.reason = f"chasing_market:{chase_reason}"
            return verdict

        # --- Rule 4: unverified social-only
        if item.source_type == "social" and item.confirmation_count < 2:
            verdict.trade_permission = "wait"
            verdict.reason = "social_unconfirmed"
            verdict.verified = False
            return verdict

        # --- Rule 5: unknown timestamp
        if freshness_status == "unknown":
            verdict.trade_permission = "wait"
            verdict.reason = "missing_timestamp"
            return verdict

        # --- Rule 6: chase warning (1-2 signals)
        if chase_decision == "wait":
            verdict.trade_permission = "wait"
            verdict.reason = f"chase_caution:{chase_reason}"
            return verdict

        # --- Rule 7: in post-event cool-down
        if in_post:
            verdict.trade_permission = "wait"
            verdict.reason = (
                f"post_event_cooldown:{nearest_event.name}"
                if nearest_event else "post_event_cooldown"
            )
            return verdict

        # --- Rule 8: confirmation requirement
        verified = item.confirmation_count >= self.require_confirmations
        verdict.verified = verified

        if not verified and self.unverified_block:
            # Single-source for medium/high impact -> wait
            verdict.trade_permission = "wait"
            verdict.reason = (
                f"unverified_{item.source_type}_n{item.confirmation_count}"
            )
            return verdict

        # --- Rule 9: fresh + confirmed -> allow
        if freshness_status == "fresh" and verified:
            verdict.trade_permission = "allow"
            verdict.reason = "fresh_verified"
            verdict.grade = "A" if item.source_type == "official" else "B"
            verdict.confidence = min(1.0, item.confirmation_count / 4.0 + 0.3)
            return verdict

        # --- Rule 10: recent + confirmed -> allow with caution
        if freshness_status == "recent" and verified:
            verdict.trade_permission = "allow"
            verdict.reason = "recent_verified"
            verdict.grade = "B"
            verdict.confidence = min(0.8, item.confirmation_count / 4.0 + 0.2)
            return verdict

        # --- Default: deny
        verdict.trade_permission = "block"
        verdict.reason = "default_deny"
        return verdict
