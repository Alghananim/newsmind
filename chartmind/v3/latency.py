# -*- coding: utf-8 -*-
"""Stopwatch instrumentation — near-zero overhead per stage."""
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
        try:
            yield
        finally:
            self.stages[name] = (time.perf_counter_ns() - t0) / 1e6

    @property
    def total_ms(self) -> float:
        if not self.finished_at_ns: return 0.0
        return (self.finished_at_ns - self.started_at_ns) / 1e6

    @property
    def bottleneck_stage(self) -> str:
        if not self.stages: return ""
        return max(self.stages.items(), key=lambda x: x[1])[0]

    def to_dict(self) -> dict:
        return {
            "total_ms": round(self.total_ms, 3),
            "stages_ms": {k: round(v, 3) for k, v in self.stages.items()},
            "bottleneck_stage": self.bottleneck_stage,
        }
