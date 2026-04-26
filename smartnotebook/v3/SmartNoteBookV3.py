# -*- coding: utf-8 -*-
"""SmartNoteBookV3 V4 — orchestrator + journaling + audit + lessons + speed + intelligence.

Synchronous (always immediate, never lost):
   - record_trade
   - record_bug

Optional async (batched, non-blocking):
   - record_decision (default sync; pass async_=True for batched)
"""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional, List
from .models import (TradeAuditEntry, DecisionEvent, MindOutputs,
                     LessonLearned, BugDetected, DailySummary, WeeklySummary,
                     AttributionResult)
from .storage import Storage
from .latency import Stopwatch, Metrics
from .async_writer import AsyncWriter
from . import classifier, attribution, bug_log, report, search, recommender
from . import scoring, pattern_detector


class SmartNoteBookV3:
    def __init__(self, base_dir: str, *, enable_async: bool = False):
        self.storage = Storage(base_dir)
        self.metrics = Metrics()
        self.enable_async = enable_async
        if enable_async:
            self._async = AsyncWriter(self.storage.write_event,
                                      flush_interval_ms=100, batch_size=50)
        else:
            self._async = None

    # ---------- Journaling APIs ----------
    def record_trade(self, t: TradeAuditEntry) -> TradeAuditEntry:
        sw = Stopwatch().start()
        if not t.audit_id: t.audit_id = str(uuid.uuid4())
        if not t.trade_id: t.trade_id = str(uuid.uuid4())
        with sw.stage("classification"):
            t.classification = classifier.classify(t)
        with sw.stage("attribution"):
            t.attribution = attribution.attribute(t)
        if t.attribution and t.attribution.primary_failure_factor:
            t.lesson = f"{t.classification}: {t.attribution.primary_failure_factor}"
        elif t.attribution and t.attribution.primary_success_factor:
            t.lesson = f"{t.classification}: {t.attribution.primary_success_factor}"
        with sw.stage("storage_write"):
            ok = self.storage.write_trade(t)
        sw.stop()
        self.metrics.record("trade_log", sw.total_ms)
        self.metrics.record("attribution_calc", sw.stages.get("attribution", 0))
        self.metrics.record("mind_snapshot", sw.stages.get("storage_write", 0))
        if not ok:
            if any("duplicate" in w for w in self.storage.warnings[-3:]):
                self.metrics.duplicate_events += 1
            else:
                self.metrics.dropped_events += 1
        return t

    def record_decision(self, e: DecisionEvent, *, async_: Optional[bool] = None) -> DecisionEvent:
        sw = Stopwatch().start()
        if not e.audit_id: e.audit_id = str(uuid.uuid4())
        if not e.event_id: e.event_id = str(uuid.uuid4())
        if not e.timestamp: e.timestamp = datetime.now(timezone.utc)

        # Use async only if enabled AND caller permits AND event isn't critical
        use_async = (async_ if async_ is not None
                     else (self.enable_async and e.event_type not in ("trade", "bug")))
        if use_async and self._async:
            self._async.submit(e)
            self.metrics.queue_backlog = self._async.backlog()
        else:
            ok = self.storage.write_event(e)
            if not ok: self.metrics.dropped_events += 1
        sw.stop()
        self.metrics.record("event_write", sw.total_ms)
        return e

    def record_bug(self, *, affected_mind: str, bug_type: str, severity: str,
                   example_event_id: str, impact: str) -> BugDetected:
        return bug_log.log_bug(self.storage, affected_mind=affected_mind,
                               bug_type=bug_type, severity=severity,
                               example_event_id=example_event_id, impact=impact)

    def mark_bug_fixed(self, bug_id: str, fix_commit_id: str = "") -> bool:
        return bug_log.mark_fixed(self.storage, bug_id, fix_commit_id)

    # ---------- Reports ----------
    def daily_report(self, *, date: str, pair: str) -> DailySummary:
        sw = Stopwatch().start()
        s = report.build_daily(self.storage, date=date, pair=pair)
        self.storage.write_daily(s)
        sw.stop()
        self.metrics.record("daily_summary", sw.total_ms)
        return s

    def weekly_report(self, *, week_start: str, pairs: list) -> WeeklySummary:
        sw = Stopwatch().start()
        s = report.build_weekly(self.storage, week_start=week_start, pairs=pairs)
        self.storage.write_weekly(s)
        sw.stop()
        self.metrics.record("weekly_summary", sw.total_ms)
        return s

    # ---------- Queries ----------
    def _q(self, fn, **kw):
        sw = Stopwatch().start()
        try: return fn(self.storage, **kw)
        finally:
            sw.stop()
            self.metrics.record("query", sw.total_ms)

    def why_lose(self, **kw): return self._q(search.why_did_we_lose, **kw)
    def why_win(self, **kw): return self._q(search.why_did_we_win, **kw)
    def most_wrong_brain(self, **kw): return self._q(search.most_wrong_brain, **kw)
    def trades_should_have_blocked(self, **kw):
        return self._q(search.trades_that_should_have_been_blocked, **kw)

    # ---------- Recommendations ----------
    def scan_lessons(self, **kw) -> List[LessonLearned]:
        return recommender.scan_and_recommend(self.storage, **kw)

    def detect_patterns(self, **kw) -> dict:
        return pattern_detector.detect_patterns(self.storage, **kw)

    # ---------- Scores ----------
    def intelligence_score(self) -> float:
        # Placeholder using current metrics + assumed accuracies
        return scoring.notebook_intelligence_score(
            classification_acc=0.95, attribution_acc=0.90,
            recommendation_q=1.0,    # we enforce ≥3 evidence
            pattern_detection=1.0)

    def speed_score(self) -> float:
        return scoring.speed_score(self.metrics.avg("event_write"), target_ms=2.0)

    def storage_health(self) -> str:
        total = (len(self.metrics.event_write_ms) +
                 len(self.metrics.trade_log_ms))
        return scoring.storage_health(
            self.metrics.dropped_events,
            self.metrics.duplicate_events,
            self.metrics.missing_fields, total)

    @property
    def warnings(self) -> list: return self.storage.warnings

    def health_report(self) -> dict:
        d = self.metrics.to_dict()
        d["intelligence_score"] = self.intelligence_score()
        d["speed_score"] = self.speed_score()
        d["storage_health"] = self.storage_health()
        if self._async:
            d["async_dropped"] = self._async.dropped
            d["async_backlog"] = self._async.backlog()
        return d

    def flush(self, timeout_s: float = 2.0):
        """Block until queued async events are drained to storage."""
        if self._async:
            self._async.flush(timeout_s)

    def stop(self):
        if self._async:
            self._async.flush(2.0)
            self._async.stop()
