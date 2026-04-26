# -*- coding: utf-8 -*-
"""Anti-overfitting recommender.

Rules:
   - NEVER recommend based on 1 trade
   - Require ≥3 instances of same pattern to suggest
   - Require ≥5 instances to mark as confident
   - Lessons remain `requires_more_evidence=True` until 5 instances
"""
from __future__ import annotations
import uuid
from collections import Counter
from typing import List, Optional
from .models import LessonLearned
from .storage import Storage


MIN_EVIDENCE_FOR_SUGGESTION = 3
MIN_EVIDENCE_FOR_CONFIDENT = 5


def scan_and_recommend(storage: Storage, *, pair: Optional[str] = None) -> List[LessonLearned]:
    """Scan trade audit + decision events; produce evidence-based lessons."""
    trades = storage.query_trades(pair=pair, limit=10000)
    losses = [t for t in trades if t.get("pnl", 0) < 0]

    # Pattern: classification x responsible_mind
    patterns = Counter()
    for t in losses:
        cls = t.get("classification", "unknown")
        attr = t.get("attribution", {}) or {}
        mind = attr.get("responsible_mind", "unknown")
        patterns[(cls, mind, t.get("pair","?"))] += 1

    lessons = []
    for (cls, mind, pr), count in patterns.items():
        if count < MIN_EVIDENCE_FOR_SUGGESTION:
            continue   # silently skip — not enough evidence
        rec = f"Pattern: {cls} attributed to {mind}; tighten {mind} rules"
        l = LessonLearned(
            lesson_id=str(uuid.uuid4()),
            source_event_ids=tuple(t.get("trade_id","") for t in losses
                                    if t.get("classification") == cls)[:5],
            pair=pr,
            pattern=cls,
            observed_count=count,
            recommendation=rec,
            requires_more_evidence=(count < MIN_EVIDENCE_FOR_CONFIDENT),
        )
        storage.write_lesson(l)
        lessons.append(l)
    return lessons
