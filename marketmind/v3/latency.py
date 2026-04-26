# -*- coding: utf-8 -*-
"""Latency instrumentation — Stopwatch wrapping every stage.

Designed to be near-zero-overhead (uses time.perf_counter) and produce a
flat latency report compatible with SmartNoteBook journaling.
"""
from __future__ import annotations
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class Stopwatch:
    stages: dict = field(default_factory=dict)         # name -> ms
    sources_latency: dict = field(default_factory=dict)
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
        try:
            yield
        finally:
            self.stages[name] = (time.perf_counter_ns() - t0) / 1e6

    def record_source(self, source_name: str, latency_ms: float, status: str = "ok"):
        self.sources_latency[source_name] = {
            "ms": round(latency_ms, 2),
            "status": status,    # ok / stale / failed
        }

    @property
    def total_ms(self) -> float:
        if not self.finished_at_ns: return 0.0
        return (self.finished_at_ns - self.started_at_ns) / 1e6

    @property
    def stages_total_ms(self) -> float:
        return sum(self.stages.values())

    def to_dict(self) -> dict:
        return {
            "total_ms": round(self.total_ms, 2),
            "stages_ms": {k: round(v, 2) for k, v in self.stages.items()},
            "sources_latency": self.sources_latency,
            "bottleneck_stage": max(self.stages.items(), key=lambda x: x[1])[0]
                               if self.stages else None,
        }
