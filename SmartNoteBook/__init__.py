# -*- coding: utf-8 -*-
"""SmartNoteBook — institutional memory for the EUR/USD trading system.

Role
----
Every other brain (NewsMind, ChartMind, MarketMind, GateMind) writes to
SmartNoteBook after a trade closes and reads from it before a new
decision. The notebook is the system's *long-term memory*: it converts
every closed trade into a structured lesson, mines patterns over time,
flags cognitive biases, and injects relevant context into each brain's
prompt before the next cycle.

The architecture is taken straight from the trader-psychology canon:

    * Brett Steenbarger — *The Daily Trading Coach*, *Trading Psychology
      2.0*. The journal is the trader's mirror; daily review is non-
      negotiable.
    * Mark Douglas — *Trading in the Zone*. Separate decision quality
      from outcome quality; record both.
    * Van Tharp — *Trade Your Way to Financial Freedom*. Track R-
      multiples and the System Quality Number (SQN).
    * Annie Duke — *Thinking in Bets*, *How to Decide*. Resist
      "resulting" — a good decision can still produce a bad outcome.
    * Gary Klein — *Sources of Power*. Run a pre-mortem before every
      trade: "imagine this has lost — what was the most likely cause?"
    * Daniel Kahneman — *Thinking, Fast and Slow*. Anchoring, recency,
      confirmation, sunk-cost, and hindsight bias all corrode decisions
      and must be detected explicitly.
    * David Aronson — *Evidence-Based Technical Analysis*. Patterns
      claim significance only with adequate sample size and multiple-
      comparisons correction.
    * Robert Carver — *Systematic Trading*. Diagnose the system, then
      revise rules from evidence.
    * Marcos Lopez de Prado — *Advances in Financial Machine Learning*.
      Meta-label trades and apply Bonferroni when many hypotheses are
      tested in parallel.

Public API
----------
The orchestrator is `SmartNoteBook`. The supporting modules expose
their own public functions for callers that want a single capability
(e.g. just R-multiple metrics, or just the pre-mortem).
"""
from __future__ import annotations

from .journal import (
    Journal,
    TradeRecord,
    TradeOutcome,
    BrainGradeRecord,
)
from .metrics import (
    MetricsSummary,
    compute_metrics,
    system_quality_number,
    r_distribution,
    cohort_table,
)
from .patterns import (
    DiscoveredPattern,
    mine_patterns,
)
from .pre_mortem import (
    PreMortemContext,
    PreMortemReport,
    FailureMode,
    run_pre_mortem,
)
from .post_mortem import (
    PostMortemReport,
    run_post_mortem,
)
from .bias_detector import (
    BiasFlag,
    scan_for_biases,
)
from .lessons import (
    Lesson,
    LessonBook,
    distill_lessons,
)
from .briefing import (
    DailyBriefing,
    build_daily_briefing,
)
from .memory_injector import (
    inject_into_brain,
)
from .SmartNoteBook import (
    SmartNoteBook,
    SmartNoteBookConfig,
)

__all__ = [
    # journal
    "Journal", "TradeRecord", "TradeOutcome", "BrainGradeRecord",
    # metrics
    "MetricsSummary", "compute_metrics", "system_quality_number",
    "r_distribution", "cohort_table",
    # patterns
    "DiscoveredPattern", "mine_patterns",
    # reviews
    "PreMortemContext", "PreMortemReport", "FailureMode", "run_pre_mortem",
    "PostMortemReport", "run_post_mortem",
    # bias
    "BiasFlag", "scan_for_biases",
    # lessons
    "Lesson", "LessonBook", "distill_lessons",
    # briefing
    "DailyBriefing", "build_daily_briefing",
    # injector
    "inject_into_brain",
    # orchestrator
    "SmartNoteBook", "SmartNoteBookConfig",
]
