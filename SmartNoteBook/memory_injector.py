# -*- coding: utf-8 -*-
"""Memory injector — push SmartNoteBook's knowledge into brain prompts.

Why a dedicated module
----------------------
Each brain (NewsMind, ChartMind, MarketMind, GateMind) is implemented
as an LLM call with a base system prompt. The journal accumulates
evidence over time; that evidence has to *reach the brain at decision
time* or it does not change behavior. This is exactly the gap Annie
Duke describes in *How to Decide* (ch.7): the right knowledge in the
wrong place at the wrong time has zero leverage.

The injector takes a `DailyBriefing` (and optional `PreMortemReport`)
and produces:

    1. a per-brain *augmentation block* — short, in the brain's own
       vocabulary, that the orchestrator splices into the brain's
       system prompt before the LLM call.
    2. a sanity-checked rendering — capped length, no contradictions
       between active lessons, no leakage of fields irrelevant to the
       receiving brain.

Per-brain filtering
-------------------
Different brains care about different slices:

    * **ChartMind** — patterns about setup_type, hour_utc, regime;
      these are the cohorts ChartMind can actually act on.
    * **NewsMind** — lessons about news_state, news_proximity, blackout
      handling; warnings about news in the next 30 minutes.
    * **MarketMind** — lessons about regime + spread + correlation;
      warnings about cross-asset stress.
    * **GateMind** — *all* of the above plus bias flags (gate decides
      to skip / size down based on revenge_trading risk, etc.).

Length budget
-------------
LLM context windows are finite and adding noise hurts more than
adding silence. We apply per-brain caps:

    * `max_lessons` (default 5 per brain)
    * `max_warnings` (default 3 per brain)
    * `max_total_chars` (default 1200 per brain block — roughly 250
      tokens, small enough that it never crowds out the brain's own
      prompt template).

The injector does not call the brains directly. It returns the
augmentation block as a string; the brain's caller is responsible for
splicing it in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .bias_detector import BiasFlag
from .briefing import DailyBriefing
from .lessons import Lesson
from .pre_mortem import PreMortemReport


# ----------------------------------------------------------------------
# Config and constants.
# ----------------------------------------------------------------------
KNOWN_BRAINS = ("newsmind", "chartmind", "marketmind", "gatemind")

# Which lesson features each brain cares about, by `feature` substring.
# A None entry means the brain receives all lessons (only gatemind).
_BRAIN_LESSON_FILTERS: dict[str, Optional[tuple[str, ...]]] = {
    "chartmind": ("setup_type", "hour_utc", "market_regime", "spread"),
    "newsmind":  ("news_state", "news", "blackout"),
    "marketmind": ("market_regime", "spread", "execution"),
    "gatemind":  None,    # all
}

# Which bias flags each brain receives. Same convention.
_BRAIN_BIAS_FILTERS: dict[str, Optional[tuple[str, ...]]] = {
    "chartmind": ("anchoring", "fomo_chasing", "forcing_low_dq"),
    "newsmind":  (),                                 # none — news has no bias mapping
    "marketmind": ("recency_sizing_drift",),
    "gatemind":  None,    # all (it gates on them)
}


# ----------------------------------------------------------------------
# Output container.
# ----------------------------------------------------------------------
@dataclass
class InjectionBlock:
    """The text payload the orchestrator splices into a brain prompt.

    Fields:
        brain: which brain this block is for.
        text: the full augmentation block, ready to splice.
        char_count: convenience for length budgets / logging.
        lesson_slugs: which lessons made it in (for debugging).
        bias_names: which bias flags made it in.
    """
    brain: str
    text: str
    char_count: int
    lesson_slugs: list[str]
    bias_names: list[str]


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def inject_into_brain(brain: str,
                      briefing: DailyBriefing,
                      *,
                      pre_mortem: Optional[PreMortemReport] = None,
                      max_lessons: int = 5,
                      max_warnings: int = 3,
                      max_total_chars: int = 1200,
                      language: str = "en") -> InjectionBlock:
    """Build the per-brain augmentation block.

    `brain` must be one of the known brains (see KNOWN_BRAINS); an
    unknown brain raises ValueError so caller bugs surface loudly
    rather than silently producing an empty block.

    `language` is `"en"` (default) or `"ar"`. Lessons are bilingual in
    the journal; this picks which headline form goes into the prompt.

    The output is always a fully-formed block — even when no lessons
    or warnings apply (in which case the block contains only the
    headline, signalling "system clean, no special context today").
    """
    brain = brain.lower()
    if brain not in KNOWN_BRAINS:
        raise ValueError(
            f"unknown brain {brain!r}; expected one of {KNOWN_BRAINS}"
        )

    lessons = _filter_lessons_for_brain(briefing.active_lessons, brain)[:max_lessons]
    biases = _filter_biases_for_brain(briefing.bias_flags, brain)
    warnings = list(briefing.psychological_warnings)[:max_warnings]

    text = _render_block(
        brain=brain,
        briefing=briefing,
        lessons=lessons,
        biases=biases,
        warnings=warnings,
        pre_mortem=pre_mortem,
        language=language,
    )

    # Hard length cap: trim from the *end* (lessons-then-warnings) so
    # the headline is always preserved.
    if len(text) > max_total_chars:
        text = text[:max_total_chars - 1].rstrip() + "…"

    return InjectionBlock(
        brain=brain,
        text=text,
        char_count=len(text),
        lesson_slugs=[l.slug for l in lessons],
        bias_names=[b.name for b in biases],
    )


# ----------------------------------------------------------------------
# Filtering.
# ----------------------------------------------------------------------
def _filter_lessons_for_brain(lessons: Iterable[Lesson],
                              brain: str) -> list[Lesson]:
    """Keep only lessons whose `feature` mentions a substring this
    brain cares about. GateMind receives all lessons.
    """
    flt = _BRAIN_LESSON_FILTERS.get(brain)
    if flt is None:                  # gatemind: all
        return list(lessons)
    if not flt:                      # explicitly empty: none
        return []
    out = []
    for l in lessons:
        if any(sub in l.feature for sub in flt):
            out.append(l)
    return out


def _filter_biases_for_brain(biases: Iterable[BiasFlag],
                             brain: str) -> list[BiasFlag]:
    flt = _BRAIN_BIAS_FILTERS.get(brain)
    if flt is None:
        return list(biases)
    if not flt:
        return []
    return [b for b in biases if b.name in flt]


# ----------------------------------------------------------------------
# Rendering.
# ----------------------------------------------------------------------
def _render_block(*,
                  brain: str,
                  briefing: DailyBriefing,
                  lessons: list[Lesson],
                  biases: list[BiasFlag],
                  warnings: list[str],
                  pre_mortem: Optional[PreMortemReport],
                  language: str) -> str:
    """Render the augmentation block in the brain's own style.

    The format is intentionally simple, plain text — no Markdown that
    might fight a brain's own template. Sections are separated by
    blank lines and prefixed with short tags (LESSON:, WATCH:, ...) so
    the brain can be told to attend to or ignore them in its system
    prompt template.
    """
    lines: list[str] = []
    lines.append(f"[SmartNoteBook context for {brain.upper()} — "
                 f"{briefing.generated_at.strftime('%Y-%m-%d %H:%M UTC')}]")
    lines.append(briefing.one_line_headline)

    if lessons:
        lines.append("")
        lines.append("Active lessons (committed knowledge):")
        for l in lessons:
            tag = "FAVOR" if l.action == "favor" else (
                  "AVOID" if l.action == "avoid" else "WATCH")
            head = l.headline_ar if language == "ar" else l.headline_en
            lines.append(f"  - {tag}: {head}")

    if biases:
        lines.append("")
        lines.append("Behavioral flags (recent journal scan):")
        for b in biases:
            lines.append(f"  ! {b.severity.upper()}: {b.name} — {b.remedy}")

    if warnings:
        lines.append("")
        lines.append("Session watch:")
        for w in warnings:
            lines.append(f"  * {w}")

    if pre_mortem is not None and brain in ("gatemind", "chartmind"):
        # Pre-mortem is most relevant to the gate (decides skip/take)
        # and to ChartMind (decides plan adjustments). NewsMind already
        # produced part of the input.
        lines.append("")
        lines.append("Pre-mortem (this trade):")
        lines.append(f"  predicted outcome: {pre_mortem.predicted_outcome}")
        lines.append(f"  top failure mode: {pre_mortem.top_failure_mode}")
        for w in pre_mortem.warnings_for_brain[:3]:
            lines.append(f"  - {w}")

    if not lessons and not biases and not warnings:
        # Explicit "all clear" signal — better than silence so the
        # brain knows the injector ran and had nothing to add.
        lines.append("")
        lines.append("(no committed lessons or behavioral flags apply this session)")

    return "\n".join(lines)


# ----------------------------------------------------------------------
# Convenience: render for all brains at once.
# ----------------------------------------------------------------------
def inject_into_all_brains(briefing: DailyBriefing,
                           *,
                           pre_mortem: Optional[PreMortemReport] = None,
                           **kwargs) -> dict[str, InjectionBlock]:
    """Build augmentation blocks for every known brain in one call.

    Useful from the orchestrator's poll loop, which fans out the
    briefing to each brain right before the cycle.
    """
    return {
        brain: inject_into_brain(brain, briefing,
                                 pre_mortem=pre_mortem, **kwargs)
        for brain in KNOWN_BRAINS
    }
