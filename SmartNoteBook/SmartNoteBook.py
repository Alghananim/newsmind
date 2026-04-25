# -*- coding: utf-8 -*-
"""SmartNoteBook orchestrator — the public face of the institutional memory.

Engine.py composes ChartMind, MarketMind, NewsMind, and GateMind on
each cycle. Those four brains do not import from each other; they all
talk to one shared object — `SmartNoteBook` — which:

    * accepts a closed trade and runs the post-mortem,
    * appends the trade (with review fields) to the journal,
    * triggers re-mining of patterns and re-distillation of lessons
      on a configurable cadence,
    * builds the daily briefing on demand,
    * builds per-brain injection blocks before each decision cycle,
    * runs a pre-mortem on a proposed trade.

The orchestrator owns the `Journal` and `LessonBook` instances; the
sub-modules are stateless functions over those stores. This is the
"thin orchestrator over thick stateless functions" pattern Carver
recommends in *Systematic Trading* (ch.2): individual analytical
pieces stay independently testable, the orchestrator stays small.

Lifecycle
---------
1. `SmartNoteBook(config)` — constructed once at process start.
2. Before each decision cycle, GateMind/Engine calls
   `nb.injection_for("gatemind")` (or per brain) to get the
   augmentation block for that brain's prompt.
3. Before submitting an order, GateMind calls
   `nb.run_pre_mortem(ctx)` and stores the report.
4. After a trade closes, Engine calls `nb.record_trade(record)` —
   the orchestrator runs the post-mortem, attaches the review, and
   appends to the journal.
5. On a schedule (default every 50 trades), the orchestrator runs
   `refresh_lessons()` to mine patterns and update the lesson book.

Thread safety
-------------
The Journal and LessonBook are individually thread-safe. The
orchestrator's own caches (last_briefing, last_pattern_scan_at) are
guarded by an internal lock. Multiple brains may safely call
`injection_for(...)` concurrently; concurrent `record_trade(...)`
calls are serialised by the journal's append lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

from .bias_detector import BiasFlag, scan_for_biases
from .briefing import DailyBriefing, build_daily_briefing
from .journal import Journal, TradeRecord
from .lessons import Lesson, LessonBook, distill_lessons
from .memory_injector import (
    KNOWN_BRAINS,
    InjectionBlock,
    inject_into_all_brains,
    inject_into_brain,
)
from .metrics import MetricsSummary, compute_metrics
from .patterns import DiscoveredPattern, mine_patterns
from .post_mortem import PostMortemReport, run_post_mortem
from .pre_mortem import PreMortemContext, PreMortemReport, run_pre_mortem


# ----------------------------------------------------------------------
# Config.
# ----------------------------------------------------------------------
@dataclass
class SmartNoteBookConfig:
    """Tunables for the orchestrator. Defaults are chosen so a brand-
    new install works out of the box — change only with reason.
    """
    # ---- storage ----------------------------------------------------
    state_dir: str = "/data/newsmind_state/notebook"
    journal_subdir: str = "journal"
    lessons_filename: str = "lessons.json"

    # ---- pair / scope ----------------------------------------------
    pair: str = "EUR/USD"

    # ---- briefing cadence ------------------------------------------
    briefing_lookback_days: int = 30
    bias_lookback_trades: int = 30

    # ---- pattern / lesson cadence ----------------------------------
    refresh_every_n_trades: int = 50    # re-mine after this many new closes
    refresh_min_total_trades: int = 30  # don't bother below this
    pattern_alpha: float = 0.05
    pattern_min_n: int = 10
    pattern_holdout_fraction: float = 0.30
    lesson_min_confidence: float = 0.55

    # ---- injection -------------------------------------------------
    injection_max_lessons_per_brain: int = 5
    injection_max_warnings_per_brain: int = 3
    injection_max_chars_per_brain: int = 1200
    injection_language: str = "en"      # "en" | "ar"


# ----------------------------------------------------------------------
# Orchestrator.
# ----------------------------------------------------------------------
class SmartNoteBook:
    """Single-entry orchestrator for the SmartNoteBook subsystem.

    Construct one per process. All public methods are thread-safe.
    """

    def __init__(self, config: Optional[SmartNoteBookConfig] = None):
        self.config = config or SmartNoteBookConfig()
        state_root = Path(self.config.state_dir)
        state_root.mkdir(parents=True, exist_ok=True)

        self.journal = Journal(state_root / self.config.journal_subdir)
        self.lesson_book = LessonBook(state_root / self.config.lessons_filename)

        self._lock = threading.Lock()
        self._last_briefing: Optional[DailyBriefing] = None
        self._last_briefing_at: Optional[datetime] = None
        self._trades_since_last_refresh: int = 0
        self._last_refresh_at: Optional[datetime] = None
        self._last_patterns: list[DiscoveredPattern] = []

    # ==================================================================
    # Decision-time API (read).
    # ==================================================================
    def briefing(self,
                 *,
                 minutes_to_next_high_impact_news: float = float("inf"),
                 force_rebuild: bool = False,
                 max_age_seconds: int = 600,
                 ) -> DailyBriefing:
        """Return the current daily briefing.

        Cached for `max_age_seconds` (default 10 min) so repeated calls
        within the same poll cycle do not re-scan the journal. Pass
        `force_rebuild=True` after writing a trade to invalidate.
        """
        now = datetime.now(timezone.utc)
        with self._lock:
            stale = (
                self._last_briefing is None
                or self._last_briefing_at is None
                or (now - self._last_briefing_at).total_seconds() > max_age_seconds
            )
            if force_rebuild or stale:
                self._last_briefing = build_daily_briefing(
                    self.journal, self.lesson_book,
                    pair=self.config.pair,
                    lookback_days=self.config.briefing_lookback_days,
                    bias_lookback_trades=self.config.bias_lookback_trades,
                    minutes_to_next_high_impact_news=minutes_to_next_high_impact_news,
                    now=now,
                )
                self._last_briefing_at = now
            return self._last_briefing

    def injection_for(self,
                      brain: str,
                      *,
                      pre_mortem: Optional[PreMortemReport] = None,
                      minutes_to_next_high_impact_news: float = float("inf"),
                      ) -> InjectionBlock:
        """Build the per-brain augmentation block for the next cycle.

        Equivalent to `briefing()` followed by `inject_into_brain()`,
        but threads the orchestrator's caching and config so callers
        do not have to.
        """
        b = self.briefing(
            minutes_to_next_high_impact_news=minutes_to_next_high_impact_news,
        )
        return inject_into_brain(
            brain, b,
            pre_mortem=pre_mortem,
            max_lessons=self.config.injection_max_lessons_per_brain,
            max_warnings=self.config.injection_max_warnings_per_brain,
            max_total_chars=self.config.injection_max_chars_per_brain,
            language=self.config.injection_language,
        )

    def injection_for_all(self,
                          *,
                          pre_mortem: Optional[PreMortemReport] = None,
                          minutes_to_next_high_impact_news: float = float("inf"),
                          ) -> dict[str, InjectionBlock]:
        """Build augmentation blocks for every known brain at once."""
        b = self.briefing(
            minutes_to_next_high_impact_news=minutes_to_next_high_impact_news,
        )
        return inject_into_all_brains(
            b,
            pre_mortem=pre_mortem,
            max_lessons=self.config.injection_max_lessons_per_brain,
            max_warnings=self.config.injection_max_warnings_per_brain,
            max_total_chars=self.config.injection_max_chars_per_brain,
            language=self.config.injection_language,
        )

    # ==================================================================
    # Pre-mortem (one trade at a time).
    # ==================================================================
    def run_pre_mortem(self, ctx: PreMortemContext) -> PreMortemReport:
        """Run a Klein/Kahneman pre-mortem on a proposed trade.

        Pulls the active patterns (most recent scan) so the failure
        inventory is grounded in journal evidence, not just generic
        FX hazards.
        """
        recent = self.journal.read_recent(50)
        return run_pre_mortem(
            ctx,
            patterns=self._last_patterns,
            recent_trades=recent,
        )

    # ==================================================================
    # Closed-trade ingestion.
    # ==================================================================
    def record_trade(self, record: TradeRecord) -> PostMortemReport:
        """Run the post-mortem, attach the review, append to journal.

        The flow is:
            1. run_post_mortem() — quantitative skeleton
            2. journal.append() — persist the (un-reviewed) record
            3. journal.attach_review() — patch the review fields
            4. invalidate the briefing cache
            5. maybe refresh patterns + lessons
        """
        report = run_post_mortem(record)

        # Mutate the record's review fields *in place* before appending,
        # so the on-disk record carries the post-mortem from the start
        # (rather than going through a rewrite immediately).
        record.decision_quality_grade = report.decision_quality_grade
        record.outcome_quality_grade = report.outcome_quality_grade
        # join skeleton bullets with newlines so the LLM grader can
        # later refine them but the file is already useful as-is.
        record.what_went_right = "\n".join(report.skeleton_what_went_right)
        record.what_went_wrong = "\n".join(report.skeleton_what_went_wrong)
        record.what_id_change = "\n".join(report.skeleton_what_id_change)
        record.one_sentence_lesson = report.one_sentence_lesson_seed
        record.tags = list(report.suggested_tags)

        self.journal.append(record)

        with self._lock:
            self._trades_since_last_refresh += 1
            # Invalidate cached briefing so the next caller rebuilds.
            self._last_briefing = None
            self._last_briefing_at = None
            do_refresh = (
                self._trades_since_last_refresh >= self.config.refresh_every_n_trades
            )

        if do_refresh:
            self.refresh_lessons()

        return report

    def attach_llm_review(self,
                          trade_id: str,
                          *,
                          decision_quality_grade: int,
                          outcome_quality_grade: int,
                          what_went_right: str,
                          what_went_wrong: str,
                          what_id_change: str,
                          one_sentence_lesson: str,
                          tags: Iterable[str] = ()) -> bool:
        """Replace the post-mortem skeleton with the LLM grader's
        polished narrative once it has run. Returns True on success.

        This is exposed separately because the LLM grader runs
        asynchronously after the trade is already on disk — we want
        the journal to be useful in the gap.
        """
        ok = self.journal.attach_review(
            trade_id,
            decision_quality_grade=decision_quality_grade,
            outcome_quality_grade=outcome_quality_grade,
            what_went_right=what_went_right,
            what_went_wrong=what_went_wrong,
            what_id_change=what_id_change,
            one_sentence_lesson=one_sentence_lesson,
            tags=tags,
        )
        if ok:
            with self._lock:
                # Briefing might cite this trade — invalidate.
                self._last_briefing = None
                self._last_briefing_at = None
        return ok

    def annotate(self, trade_id: str, note: str) -> bool:
        """Add a free-text human note to a trade. Convenience wrapper."""
        return self.journal.annotate(trade_id, note)

    # ==================================================================
    # Pattern / lesson refresh.
    # ==================================================================
    def refresh_lessons(self,
                        force: bool = False) -> tuple[list[DiscoveredPattern], list[Lesson]]:
        """Re-mine patterns and re-distill lessons.

        Skips silently when the journal has fewer than
        `refresh_min_total_trades` records (default 30) unless
        `force=True`. Returns (patterns, lessons_upserted) for the
        caller to log if desired.
        """
        all_trades = self.journal.read_all()
        if not force and len(all_trades) < self.config.refresh_min_total_trades:
            with self._lock:
                self._trades_since_last_refresh = 0
            return [], []

        patterns = mine_patterns(
            all_trades,
            alpha=self.config.pattern_alpha,
            min_n=self.config.pattern_min_n,
            holdout_fraction=self.config.pattern_holdout_fraction,
        )
        lessons = distill_lessons(
            patterns,
            self.lesson_book,
            min_confidence=self.config.lesson_min_confidence,
        )

        # Demote any existing lessons that no longer pass — every
        # active lesson should be re-seen in this scan if still valid.
        active_slugs = {
            _slug_for(p, "favourable" if p.direction == "favourable" else "adverse")
            for p in patterns if p.passes_bonferroni
        }
        for existing in self.lesson_book.active():
            if existing.is_permanent:
                continue
            if existing.slug not in active_slugs:
                self.lesson_book.mark_failed_validation(existing.slug)

        with self._lock:
            self._trades_since_last_refresh = 0
            self._last_refresh_at = datetime.now(timezone.utc)
            self._last_patterns = patterns
            # Briefing is now stale — invalidate.
            self._last_briefing = None
            self._last_briefing_at = None

        return patterns, lessons

    # ==================================================================
    # Read-side helpers.
    # ==================================================================
    def metrics(self,
                *,
                lookback_days: Optional[int] = None) -> MetricsSummary:
        """Headline metrics over the last N days (defaults to config)."""
        days = lookback_days or self.config.briefing_lookback_days
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return compute_metrics(self.journal.read_since(since))

    def biases(self) -> list[BiasFlag]:
        """Current bias flags over the recent journal."""
        return scan_for_biases(
            self.journal.read_recent(self.config.bias_lookback_trades * 2),
            lookback=self.config.bias_lookback_trades,
        )

    def recent_trades(self, n: int = 20) -> list[TradeRecord]:
        return self.journal.read_recent(n)


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _slug_for(pat: DiscoveredPattern, direction: str) -> str:
    """Mirror of lessons._slug_from_feature, kept private here so the
    orchestrator can compare slugs without importing private symbols.
    """
    sanitised = pat.feature.replace("=", "_eq_").replace(" ", "_")\
                       .replace("(", "_").replace(")", "_")\
                       .replace("[", "_").replace("]", "_")\
                       .replace(",", "_").replace(".", "p")\
                       .replace("/", "_")
    return f"{direction}__{sanitised}"
