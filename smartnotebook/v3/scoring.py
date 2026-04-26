# -*- coding: utf-8 -*-
"""Scoring for SmartNoteBook intelligence + speed."""
from __future__ import annotations


def speed_score(write_ms: float, target_ms: float = 2.0) -> float:
    """1.0 if avg write ≤ target_ms; degrades to 0 at 10× target."""
    if write_ms <= 0: return 1.0
    if write_ms <= target_ms: return 1.0
    if write_ms >= 10 * target_ms: return 0.0
    return round(1 - (write_ms - target_ms) / (9 * target_ms), 3)


def classification_accuracy(correct: int, total: int) -> float:
    return round(correct / total, 3) if total > 0 else 0.0


def attribution_accuracy(correct: int, total: int) -> float:
    return round(correct / total, 3) if total > 0 else 0.0


def recommendation_quality(*, lessons_with_evidence: int,
                           total_lessons: int) -> float:
    """0..1: ratio of lessons with sufficient evidence (≥3 instances)."""
    if total_lessons == 0: return 1.0
    return round(lessons_with_evidence / total_lessons, 3)


def notebook_intelligence_score(*, classification_acc: float,
                                attribution_acc: float,
                                recommendation_q: float,
                                pattern_detection: float = 1.0) -> float:
    """Composite 0..1."""
    return round((classification_acc + attribution_acc + recommendation_q +
                  pattern_detection) / 4.0, 3)


def storage_health(dropped: int, duplicates: int, missing: int,
                   total_writes: int) -> str:
    if total_writes == 0: return "no_data"
    drop_rate = (dropped + missing) / total_writes
    dup_rate = duplicates / total_writes
    if drop_rate > 0.05 or dup_rate > 0.05: return "degraded"
    if drop_rate > 0 or dup_rate > 0: return "warnings"
    return "ok"
