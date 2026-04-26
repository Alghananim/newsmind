# -*- coding: utf-8 -*-
"""Bug tracking — append-only log of detected issues."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional
from .models import BugDetected
from .storage import Storage


def log_bug(storage: Storage, *, affected_mind: str, bug_type: str,
            severity: str, example_event_id: str, impact: str,
            fix_required: bool = True) -> BugDetected:
    b = BugDetected(
        bug_id=str(uuid.uuid4()),
        affected_mind=affected_mind,
        bug_type=bug_type,
        severity=severity,
        example_event_id=example_event_id,
        impact_on_result=impact,
        detected_at=datetime.now(timezone.utc),
        fix_required=fix_required,
        fixed=False,
        retest_required=fix_required,
    )
    storage.write_bug(b)
    return b


def mark_fixed(storage: Storage, bug_id: str, fix_commit_id: str = ""):
    """Update an existing bug as fixed."""
    bugs = storage.all_bugs()
    for b in bugs:
        if b.get("bug_id") == bug_id:
            b["fixed"] = True
            b["fix_commit_id"] = fix_commit_id
            new_b = BugDetected(**{k: v for k, v in b.items()
                                   if k in BugDetected.__dataclass_fields__})
            new_b.detected_at = datetime.fromisoformat(b["detected_at"]) \
                if isinstance(b.get("detected_at"), str) else new_b.detected_at
            storage.write_bug(new_b)
            return True
    return False
