# -*- coding: utf-8 -*-
"""PermissionEngine (V3) — converts all signals into allow/wait/block.

Decision matrix (highest priority first; first match wins):

   1. inside scheduled high-impact event window  -> BLOCK
   2. recycled/stale news                         -> BLOCK
   3. chase signals 3+                            -> BLOCK
   4. unverified social-only news                 -> WAIT
   5. unknown timestamp                           -> WAIT
   6. chase warning (1-2 signals)                 -> WAIT
   7. in post-event cool-down                     -> WAIT
   8. confirmation requirement not met            -> WAIT
   9. fresh + verified                            -> ALLOW + grade ladder
  10. recent + verified                           -> ALLOW + grade B
  11. anything else                                -> BLOCK (default deny)

V3 grade ladder (Rule 9):
   A+  fresh + ≥3 confirmations + (tier1_wire or official) + impact=high
   A   fresh + ≥2 confirmations + (tier1_wire or official)
   B   fresh + ≥2 confirmations + financial_media

Fail-safe: any internal exception => BLOCK with engine_error reason.
"""
from __future__ import annotations
from datetime import datetime, timezone
from .models import NewsItem, NewsVerdict, EventSchedule, FreshnessStatus, TradePermission


class PermissionEngine:
    def __init__(self, require_confirmations: int = 2, unverified_block: bool = True):
        self.require_confirmations = require_confirmations
        self.unverified_block = unverified_block

    def decide(self, *, item, freshness_status, age_seconds,
               in_pre_event, in_during_event, in_post_event,
               nearest_event=None, chase_decision="allow", chase_reason=""):
        try:
            return self._decide_inner(
                item, freshness_status, age_seconds,
                in_pre_event, in_during_event, in_post_event,
                nearest_event, chase_decision, chase_reason)
        except Exception as e:
            return NewsVerdict(
                headline=item.headline if item else "",
                source_name=item.source_name if item else "",
                trade_permission="block",
                reason=f"engine_error:{type(e).__name__}",
                grade="C")

    def _decide_inner(self, item, freshness_status, age_seconds,
                      in_pre, in_during, in_post,
                      nearest_event, chase_decision, chase_reason):
        v = NewsVerdict(
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

        # Rule 1: high-impact blackout
        if (in_during or in_pre) and nearest_event and nearest_event.impact_level == "high":
            v.trade_permission = "block"
            v.reason = (f"high_impact_event_blackout:{nearest_event.name}_"
                        f"{'during' if in_during else 'pre'}")
            v.impact_level = "high"
            return v

        # Rule 2: recycled / stale
        if freshness_status in ("recycled", "stale"):
            v.trade_permission = "block"
            v.reason = f"news_age:{freshness_status}"
            return v

        # Rule 3: chase block
        if chase_decision == "block":
            v.trade_permission = "block"
            v.reason = f"chasing_market:{chase_reason}"
            return v

        # Rule 4: unverified social
        if item.source_type == "social" and item.confirmation_count < 2:
            v.trade_permission = "wait"
            v.reason = "social_unconfirmed"
            v.verified = False
            return v

        # Rule 5: unknown timestamp
        if freshness_status == "unknown":
            v.trade_permission = "wait"
            v.reason = "missing_timestamp"
            return v

        # Rule 6: chase warning
        if chase_decision == "wait":
            v.trade_permission = "wait"
            v.reason = f"chase_caution:{chase_reason}"
            return v

        # Rule 7: post-event cool-down
        if in_post:
            v.trade_permission = "wait"
            v.reason = (f"post_event_cooldown:{nearest_event.name}"
                        if nearest_event else "post_event_cooldown")
            return v

        # Rule 8: confirmation requirement
        verified = item.confirmation_count >= self.require_confirmations
        v.verified = verified
        if not verified and self.unverified_block:
            v.trade_permission = "wait"
            v.reason = f"unverified_{item.source_type}_n{item.confirmation_count}"
            return v

        # Rule 9: fresh + verified -> allow + grade ladder
        if freshness_status == "fresh" and verified:
            v.trade_permission = "allow"
            v.reason = "fresh_verified"
            tier1 = item.source_type in ("official", "tier1_wire")
            confs = item.confirmation_count
            # high-impact: either intelligence has set verdict.impact_level OR scheduled event nearby
            # NOTE: intelligence enrichment runs AFTER this; we also check the
            # source headline for high-impact event keywords as a backup.
            high_impact = (
                v.impact_level == "high"
                or (nearest_event and nearest_event.impact_level == "high")
                or _headline_implies_high_impact(item.headline)
            )
            if confs >= 3 and tier1 and high_impact:
                v.grade = "A+"
            elif tier1:
                v.grade = "A"
            else:
                v.grade = "B"
            v.confidence = min(1.0, confs / 4.0 + 0.3)
            return v

        # Rule 10: recent + verified
        if freshness_status == "recent" and verified:
            v.trade_permission = "allow"
            v.reason = "recent_verified"
            v.grade = "B"
            v.confidence = min(0.8, item.confirmation_count / 4.0 + 0.2)
            return v

        # Default deny
        v.trade_permission = "block"
        v.reason = "default_deny"
        return v


# Lightweight backup high-impact detector (mirror of intelligence keywords)
_HIGH_IMPACT_HINTS = (
    "cpi", "nfp", "nonfarm", "fomc", "fed rate", "ecb rate", "boj rate",
    "boe rate", "rate decision", "rate cut", "rate hike", "powell",
    "war", "sanctions", "escalation", "intervention",
)
def _headline_implies_high_impact(headline: str) -> bool:
    if not headline: return False
    h = headline.lower()
    return any(k in h for k in _HIGH_IMPACT_HINTS)
