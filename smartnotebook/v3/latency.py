# -*- coding: utf-8 -*-
"""Stopwatch instrumentation for SmartNoteBook."""
from __future__ import annotations
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Stopwatch:
    stages: dict = field(default_factory=dict)
    started_at_ns: int = 0
    finished_at_ns: int = 0

    def start(self) -> "Stopwatch":
        self.started_at_ns = time.perf_counter_ns()
        return self
    def stop(self) -> "Stopwatch":
        self.finished_at_ns = time.perf_counter_ns()
        return self
    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter_ns()
        try: yield
        finally: self.stages[name] = (time.perf_counter_ns() - t0) / 1e6
    @property
    def total_ms(self) -> float:
        if not self.finished_at_ns: return 0.0
        return (self.finished_at_ns - self.started_at_ns) / 1e6
    @property
    def bottleneck_stage(self) -> str:
        if not self.stages: return ""
        return max(self.stages.items(), key=lambda x: x[1])[0]


# Module-level metrics aggregator
class Metrics:
    def __init__(self):
        self.event_write_ms = []
        self.trade_log_ms = []
        self.mind_snapshot_ms = []
        self.attribution_calc_ms = []
        self.daily_summary_ms = []
        self.weekly_summary_ms = []
        self.query_ms = []
        self.dropped_events = 0
        self.duplicate_events = 0
        self.missing_fields = 0
        self.queue_backlog = 0
        self.storage_health = "ok"

    def record(self, name: str, ms: float):
        getattr(self, name + "_ms", []).append(ms)

    def avg(self, name: str) -> float:
        lst = getattr(self, name + "_ms", [])
        return round(sum(lst)/len(lst), 4) if lst else 0.0

    def to_dict(self) -> dict:
        return {
            "event_write_avg_ms": self.avg("event_write"),
            "trade_log_avg_ms": self.avg("trade_log"),
            "mind_snapshot_avg_ms": self.avg("mind_snapshot"),
            "attribution_calc_avg_ms": self.avg("attribution_calc"),
            "daily_summary_avg_ms": self.avg("daily_summary"),
            "weekly_summary_avg_ms": self.avg("weekly_summary"),
            "query_avg_ms": self.avg("query"),
            "dropped_events_count": self.dropped_events,
            "duplicate_events_count": self.duplicate_events,
            "missing_fields_count": self.missing_fields,
            "queue_backlog_size": self.queue_backlog,
            "storage_health_status": self.storage_health,
        }
