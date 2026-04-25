# -*- coding: utf-8 -*-
"""Lessons — distill recurring journal patterns into persistent rules.

`patterns.py` discovers statistically-honest cohorts; `lessons.py`
turns the strongest of those into *named, persisted, actionable rules*
that survive across sessions and feed the brains via the memory
injector.

Why the separation?
-------------------
Patterns are ephemeral — every journal scan re-tests every hypothesis.
Lessons are *committed knowledge* — once we agree "Setup X has
expectancy +0.6R over 60 trades, three holdouts confirm it; favor it",
that lesson should be available to the brains tomorrow even if a noisy
recent week pushes the pattern's p-value briefly above the threshold.

Carver makes this argument explicitly in *Systematic Trading* (ch.18):
diagnostics inform rule revisions, but rules are revised deliberately,
not on every refresh. Lopez de Prado's *AFML* (ch.11) goes further:
treating every refresh as a new model is a recipe for backtest
overfitting. We persist lessons in a single JSON file (`lessons.json`)
with a small schema and conservative add/update rules.

Lifecycle of a lesson
---------------------
    1. Pattern is discovered (patterns.py, passes Bonferroni, holdout
       preserved).
    2. `distill_lessons` proposes a Lesson object with:
        * an Arabic + English short headline
        * a "favor" or "avoid" action
        * a confidence score that combines effect size and sample size
        * a stake count (how many trades support it)
    3. The lesson is added to the LessonBook if not already present;
       if a related lesson exists it is *updated* (stake count rises,
       evidence refreshed).
    4. Every M sessions (default 30) lessons are re-validated. A
       lesson that fails validation twice in a row is downgraded to
       "watch" status — kept on file but no longer injected into
       brain prompts.
    5. Lessons can be marked "permanent" by the trader (Mansur),
       overriding automatic downgrades.

The file format is plain JSON with a single top-level dict, easy to
version-control and human-readable in a console.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .journal import TradeRecord
from .patterns import DiscoveredPattern


# ----------------------------------------------------------------------
# Lesson type.
# ----------------------------------------------------------------------
@dataclass
class Lesson:
    """One persistent rule-of-thumb extracted from journal evidence."""
    slug: str                       # stable identifier (machine)
    headline_en: str
    headline_ar: str
    action: str                     # "favor" | "avoid" | "watch"
    confidence: float               # 0.0 - 1.0, see _confidence_score
    stake_n: int                    # trades supporting the lesson
    expectancy_delta_r: float       # cohort vs rest
    win_rate_delta: float
    p_value: float
    feature: str                    # raw pattern feature string
    first_seen_at: datetime
    last_validated_at: datetime
    failed_validations: int = 0
    is_permanent: bool = False
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["first_seen_at"] = self.first_seen_at.isoformat()
        d["last_validated_at"] = self.last_validated_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Lesson":
        d = dict(d)
        for k in ("first_seen_at", "last_validated_at"):
            v = d.get(k)
            if isinstance(v, str):
                d[k] = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return cls(**d)


# ----------------------------------------------------------------------
# Persistent store.
# ----------------------------------------------------------------------
class LessonBook:
    """Atomic, file-backed dictionary of Lesson objects keyed by slug.

    All public methods are thread-safe. Writes use the temp-file +
    rename pattern so a crash mid-write cannot leave a half-written
    JSON file.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data: dict[str, Lesson] = self._read()

    # ----- read side -------------------------------------------------
    def all(self) -> list[Lesson]:
        with self._lock:
            return list(self._data.values())

    def active(self) -> list[Lesson]:
        """Lessons currently injectable into brain prompts (action !=
        'watch' OR is_permanent=True).
        """
        with self._lock:
            return [l for l in self._data.values()
                    if l.is_permanent or l.action != "watch"]

    def by_slug(self, slug: str) -> Optional[Lesson]:
        with self._lock:
            return self._data.get(slug)

    # ----- write side ------------------------------------------------
    def upsert(self, lesson: Lesson) -> None:
        """Add a lesson, or update an existing one (refreshing stats).

        We never silently overwrite headlines — if a lesson with the
        same slug already exists, the existing headlines are preserved
        unless the action has flipped (favor <-> avoid).
        """
        with self._lock:
            existing = self._data.get(lesson.slug)
            if existing:
                existing.confidence = lesson.confidence
                existing.stake_n = lesson.stake_n
                existing.expectancy_delta_r = lesson.expectancy_delta_r
                existing.win_rate_delta = lesson.win_rate_delta
                existing.p_value = lesson.p_value
                existing.last_validated_at = lesson.last_validated_at
                if existing.action != lesson.action:
                    existing.action = lesson.action
                    existing.headline_en = lesson.headline_en
                    existing.headline_ar = lesson.headline_ar
                    existing.failed_validations = 0
                else:
                    existing.failed_validations = 0
            else:
                self._data[lesson.slug] = lesson
            self._flush()

    def mark_failed_validation(self, slug: str) -> None:
        with self._lock:
            l = self._data.get(slug)
            if not l:
                return
            l.failed_validations += 1
            if l.failed_validations >= 2 and not l.is_permanent:
                l.action = "watch"
            self._flush()

    def make_permanent(self, slug: str, note: str = "") -> bool:
        with self._lock:
            l = self._data.get(slug)
            if not l:
                return False
            l.is_permanent = True
            if note:
                l.notes = (l.notes + " | " if l.notes else "") + note
            self._flush()
            return True

    def remove(self, slug: str) -> bool:
        with self._lock:
            if slug not in self._data:
                return False
            del self._data[slug]
            self._flush()
            return True

    # ----- internals -------------------------------------------------
    def _read(self) -> dict[str, Lesson]:
        if not self._path.exists():
            return {}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {k: Lesson.from_dict(v) for k, v in raw.items()}
        except Exception:
            return {}

    def _flush(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {k: v.to_dict() for k, v in self._data.items()},
                f, ensure_ascii=False, indent=2,
            )
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, self._path)


# ----------------------------------------------------------------------
# Distillation: turn patterns into lessons.
# ----------------------------------------------------------------------
def distill_lessons(patterns: Iterable[DiscoveredPattern],
                    book: LessonBook,
                    min_confidence: float = 0.55) -> list[Lesson]:
    """Convert qualifying patterns to Lesson objects, upsert into the
    book, and return the resulting list.

    A pattern qualifies when:
        * passes_bonferroni is True
        * holdout_preserved is True (or None, meaning no holdout requested)
        * cohort_n >= 10 (already enforced upstream, double-checked)
        * computed confidence >= min_confidence
    """
    now = datetime.now(timezone.utc)
    out: list[Lesson] = []
    for pat in patterns:
        if not pat.passes_bonferroni:
            continue
        if pat.holdout_preserved is False:
            continue
        if pat.cohort_n < 10:
            continue
        conf = _confidence_score(pat)
        if conf < min_confidence:
            continue

        slug = _slug_from_feature(pat.feature, pat.direction)
        action = "favor" if pat.direction == "favourable" else "avoid"
        en = _headline_en(pat, action)
        ar = _headline_ar(pat, action)

        lesson = Lesson(
            slug=slug,
            headline_en=en,
            headline_ar=ar,
            action=action,
            confidence=conf,
            stake_n=pat.cohort_n,
            expectancy_delta_r=pat.expectancy_delta_r,
            win_rate_delta=pat.win_rate_delta,
            p_value=pat.p_value,
            feature=pat.feature,
            first_seen_at=now,
            last_validated_at=now,
        )
        book.upsert(lesson)
        out.append(lesson)
    return out


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _slug_from_feature(feature: str, direction: str) -> str:
    sanitised = feature.replace("=", "_eq_").replace(" ", "_")\
                       .replace("(", "_").replace(")", "_")\
                       .replace("[", "_").replace("]", "_")\
                       .replace(",", "_").replace(".", "p")\
                       .replace("/", "_")
    return f"{direction}__{sanitised}"


def _confidence_score(pat: DiscoveredPattern) -> float:
    """Map (p-value, sample size, effect size) -> single 0-1 score.

    Components, multiplied together so weakness on any axis pulls the
    score down:

        * p_factor: stronger as p shrinks; clipped to [0, 1].
        * n_factor: rises with sample size, saturates at 100.
        * e_factor: rises with absolute expectancy delta, saturates at 1R.

    No magical formula here — the goal is a single readable number for
    the briefing layer; the ground truth remains the underlying stats.
    """
    p = max(pat.p_value, 1e-9)
    p_factor = max(0.0, min(1.0, 1.0 + (max(-3.0, _log10(p)) / 3.0)))
    n_factor = min(1.0, pat.cohort_n / 100.0)
    e_factor = min(1.0, abs(pat.expectancy_delta_r))
    return round(p_factor * n_factor * e_factor, 3)


def _log10(x: float) -> float:
    import math
    return math.log10(x)


def _headline_en(pat: DiscoveredPattern, action: str) -> str:
    verb = "favor" if action == "favor" else "avoid"
    return (
        f"{verb} cohort '{pat.feature}': expectancy "
        f"{pat.cohort_expectancy_r:+.2f}R vs rest "
        f"{pat.rest_expectancy_r:+.2f}R "
        f"(n={pat.cohort_n}, p={pat.p_value:.3f})"
    )


def _headline_ar(pat: DiscoveredPattern, action: str) -> str:
    verb = "فضّل" if action == "favor" else "تجنّب"
    return (
        f"{verb} '{pat.feature}': التوقع "
        f"{pat.cohort_expectancy_r:+.2f}R مقابل الباقي "
        f"{pat.rest_expectancy_r:+.2f}R "
        f"(عدد={pat.cohort_n}, p={pat.p_value:.3f})"
    )
