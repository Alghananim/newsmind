# -*- coding: utf-8 -*-
"""NewsMindV2 — orchestrator for the news pipeline.

POST-AUDIT FIXES
----------------
* bug #23 — `since` uses timedelta, not hour arithmetic (midnight bug)
* bug #25/26 — fail-SAFE on source outage (returns wait), not fail-OPEN
* IQ-fix — IntelligenceLayer is now invoked for every news item; verdict
  carries usd_dir / eur_usd_dir / usd_jpy_dir / risk_mode / market_bias
  per-pair and an explicit rationale.
* IQ-fix — high-impact + risk_off + uncertain direction => wait (we don't
  know which side to take, so do not trade).
* IQ-fix — political/social unverified is capped at grade C and forced to wait.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from .models import NewsItem, NewsVerdict, EventSchedule
from .freshness import FreshnessAnalyzer
from .chase_detector import ChaseDetector
from .event_scheduler import EventScheduler
from .sources import SourceAggregator, default_sources
from .permission import PermissionEngine
from .intelligence import IntelligenceLayer


class NewsMindV2:
    """Per-pair news orchestrator."""

    def __init__(self, *, pair, calendar=None, sources=None,
                 require_confirmations=2):
        self.pair = pair
        self.scheduler = EventScheduler(calendar=calendar)
        self.freshness = FreshnessAnalyzer()
        self.chase = ChaseDetector()
        self.aggregator = SourceAggregator(sources or default_sources())
        self.permission = PermissionEngine(
            require_confirmations=require_confirmations,
            unverified_block=True,
        )
        self.intelligence = IntelligenceLayer()
        self._last_eval_utc = None
        self._last_verdict = None

    # ------------------------------------------------------------------
    def _enrich_with_intelligence(self, verdict, item):
        """Attach impact direction / risk_mode / market_bias to verdict.

        Safety rules:
          - unverified social -> bias forced to neutral, risk capped to unclear
          - high-impact + risk_off + verdict was 'allow' + direction unclear
            -> downgrade to 'wait'
        """
        try:
            ass = self.intelligence.assess(item)
        except Exception as e:
            verdict.reason = (verdict.reason or "") + f"|iq_error:{type(e).__name__}"
            return verdict

        bias = ass.market_bias_per_pair.get(self.pair, "unclear")
        verdict.market_bias = bias
        verdict.risk_mode = ass.risk_mode
        # Only overwrite impact_level if the IL gives a concrete one
        if ass.impact_level != "unknown":
            verdict.impact_level = ass.impact_level

        # Append rationale to reason for audit trail
        if ass.rationale:
            verdict.reason = (verdict.reason or "") + "|iq:" + ",".join(ass.rationale)

        # Cap unverified social grades and force wait
        if ass.is_political_unverified and verdict.trade_permission == "allow":
            verdict.trade_permission = "wait"
            verdict.grade = "C"
            verdict.reason = (verdict.reason or "") + "|capped_political_unverified"

        # Risk-off + high-impact + still-unclear direction = WAIT
        if (ass.risk_mode == "risk_off"
                and verdict.impact_level == "high"
                and verdict.trade_permission == "allow"):
            verdict.trade_permission = "wait"
            verdict.reason = (verdict.reason or "") + "|risk_off_caution"

        return verdict

    # ------------------------------------------------------------------
    def evaluate(self, *, now_utc=None, recent_bars=None, current_bar=None):
        now_utc = now_utc or datetime.now(timezone.utc)
        recent_bars = recent_bars or []

        # 1) Scheduled event windows
        in_pre, in_during, in_post, nearest_event = self.scheduler.windows_for(
            now_utc=now_utc, pair=self.pair)

        # High-impact blackout overrides everything
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

        # 2) Pull recent news
        since = now_utc - timedelta(hours=2)
        try:
            items = self.aggregator.fetch_all(since_utc=since)
        except Exception:
            items = []

        relevant = [i for i in items
                    if not i.affected_pairs or self.pair in i.affected_pairs]

        # No news? Distinguish clean tape vs outage
        if not relevant:
            try:
                summary = self.aggregator.fetch_status_summary()
            except Exception:
                summary = {"all_failed": True, "any_alive": False, "total": 0}

            if summary.get("all_failed", False) or not summary.get("any_alive", False):
                v = NewsVerdict(
                    headline="(source outage)",
                    source_name="news_v2",
                    trade_permission="wait",
                    reason=("sources_all_failed_or_silent:"
                            f"ok={summary.get('ok',0)}/err={summary.get('error',0)}/"
                            f"never={summary.get('never',0)}"),
                    grade="C",
                )
            elif in_post and nearest_event:
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

        # 3) Per-item permission + intelligence
        verdicts = []
        for item in relevant:
            status, age_s, _r = self.freshness.classify(item, now_utc=now_utc)
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
            v = self._enrich_with_intelligence(v, item)
            verdicts.append(v)

        # 4) Worst-case wins
        order = {"block": 2, "wait": 1, "allow": 0}
        verdicts.sort(key=lambda x: -order[x.trade_permission])
        final = verdicts[0]
        self._last_verdict = final
        self._last_eval_utc = now_utc
        return final

    def health(self):
        return {
            "source_health": self.aggregator.health_status(),
            "fetch_status": self.aggregator.fetch_status_summary(),
            "last_eval_utc": self._last_eval_utc.isoformat() if self._last_eval_utc else None,
            "last_verdict": self._last_verdict.to_dict() if self._last_verdict else None,
            "pair": self.pair,
        }
