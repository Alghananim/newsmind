# -*- coding: utf-8 -*-
"""Briefing — the morning page the system reads before it trades.

Steenbarger's central claim in *The Daily Trading Coach* (lessons 1-10)
is that the journal is two halves: the *closing review* (post_mortem)
and the *opening review* (this module). The opening review is where
yesterday's evidence becomes today's discipline. We follow his
template, but compress it into a structured object so the brains can
read it programmatically — not just the human.

What goes into a daily briefing
-------------------------------
    1. **Headline state** — equity, recent drawdown in R, recent
       streak. The first thing a trader needs on the chair is "where
       am I?" (Schwager, *Hedge Fund Market Wizards*).

    2. **Headline metrics** — last-30-day SQN, expectancy, profit
       factor, win rate. Tharp's instrument panel.

    3. **Cohort one-liners** — best/worst setup, best/worst regime,
       best/worst hour. Carver's "system fitness per cohort"
       (*Systematic Trading*, ch.18). Surfaces drift before it shows
       up in aggregate.

    4. **Active lessons** — the rules currently committed to (favor /
       avoid). These are the only items that propagate to brain
       prompts via the memory_injector.

    5. **Active bias flags** — recent behavioral signatures
       (revenge_trading, fomo_chasing, etc.). Steenbarger's "the
       behavior shows up in the record before it shows up in
       awareness" (*Trading Psychology 2.0*, ch.4).

    6. **Open psychological warnings** — things context-dependent
       enough that they must be re-evaluated each session: drawdown
       pressure, post-streak inflation risk, weekend gap risk,
       upcoming high-impact news clusters.

    7. **One-line headline** — the briefing's TL;DR. Forced brevity,
       Steenbarger style. If the briefing has to be ignored, this is
       the line that should still be read.

The briefing is *not* a decision-maker. It does not vote, gate, or
veto. It is read by the brains via the injector and by the human via
the morning console output. Decisions stay in the brains and the
gate; the briefing only sets the table.
"""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from .bias_detector import BiasFlag, scan_for_biases
from .journal import Journal, TradeRecord
from .lessons import Lesson, LessonBook
from .metrics import (
    MetricsSummary,
    cohort_table,
    compute_metrics,
    hour_of_day_cohort,
)
from .patterns import DiscoveredPattern


# ----------------------------------------------------------------------
# Output type.
# ----------------------------------------------------------------------
@dataclass
class CohortHighlight:
    """Best/worst single cohort by expectancy, with sample size guard."""
    dimension: str                  # "setup_type" | "market_regime" | ...
    label: str                      # e.g. "breakout_pullback"
    n: int
    expectancy_r: float
    win_rate: float


@dataclass
class DailyBriefing:
    """Structured morning briefing for the trading session.

    Fields are deliberately concrete (no free-form text dump) so the
    memory_injector can render slices selectively per brain.
    """
    generated_at: datetime
    pair: str

    # ---- headline state --------------------------------------------
    n_trades_lookback: int
    lookback_days: int
    current_drawdown_r: float
    consecutive_losses: int
    consecutive_wins: int

    # ---- headline metrics ------------------------------------------
    metrics: MetricsSummary

    # ---- cohort highlights -----------------------------------------
    best_setup: Optional[CohortHighlight]
    worst_setup: Optional[CohortHighlight]
    best_regime: Optional[CohortHighlight]
    worst_regime: Optional[CohortHighlight]
    best_hour: Optional[CohortHighlight]
    worst_hour: Optional[CohortHighlight]

    # ---- knowledge -------------------------------------------------
    active_lessons: list[Lesson]
    bias_flags: list[BiasFlag]

    # ---- session warnings ------------------------------------------
    psychological_warnings: list[str]

    # ---- the headline itself ---------------------------------------
    one_line_headline: str

    # ----- serialisation -------------------------------------------
    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "pair": self.pair,
            "n_trades_lookback": self.n_trades_lookback,
            "lookback_days": self.lookback_days,
            "current_drawdown_r": self.current_drawdown_r,
            "consecutive_losses": self.consecutive_losses,
            "consecutive_wins": self.consecutive_wins,
            "metrics": self.metrics.to_dict(),
            "best_setup": asdict(self.best_setup) if self.best_setup else None,
            "worst_setup": asdict(self.worst_setup) if self.worst_setup else None,
            "best_regime": asdict(self.best_regime) if self.best_regime else None,
            "worst_regime": asdict(self.worst_regime) if self.worst_regime else None,
            "best_hour": asdict(self.best_hour) if self.best_hour else None,
            "worst_hour": asdict(self.worst_hour) if self.worst_hour else None,
            "active_lessons": [l.to_dict() for l in self.active_lessons],
            "bias_flags": [b.to_dict() for b in self.bias_flags],
            "psychological_warnings": list(self.psychological_warnings),
            "one_line_headline": self.one_line_headline,
        }

    # ----- human render --------------------------------------------
    def to_console_string(self) -> str:
        """Compact multi-line render for the morning console / log.

        Designed to fit in ~30 lines; the brains read the structured
        object, the human reads this.
        """
        lines: list[str] = []
        lines.append(f"=== SmartNoteBook briefing — {self.pair} — "
                     f"{self.generated_at.strftime('%Y-%m-%d %H:%M UTC')} ===")
        lines.append(self.one_line_headline)
        lines.append("")
        lines.append(
            f"State: dd={self.current_drawdown_r:.1f}R, "
            f"streak={'W'+str(self.consecutive_wins) if self.consecutive_wins else 'L'+str(self.consecutive_losses)}, "
            f"trades(last {self.lookback_days}d)={self.n_trades_lookback}"
        )
        m = self.metrics
        lines.append(
            f"Metrics: SQN={m.sqn:.2f} ({m.sqn_label}), "
            f"E={m.expectancy_r:+.2f}R, PF={m.profit_factor:.2f}, "
            f"WR={m.win_rate*100:.0f}%, MaxDD={m.max_drawdown_r:.1f}R"
        )
        if self.best_setup:
            lines.append(f"Best setup: {self.best_setup.label} "
                         f"(E={self.best_setup.expectancy_r:+.2f}R, n={self.best_setup.n})")
        if self.worst_setup:
            lines.append(f"Worst setup: {self.worst_setup.label} "
                         f"(E={self.worst_setup.expectancy_r:+.2f}R, n={self.worst_setup.n})")
        if self.best_hour:
            lines.append(f"Best hour: {self.best_hour.label} "
                         f"(E={self.best_hour.expectancy_r:+.2f}R, n={self.best_hour.n})")
        if self.worst_hour:
            lines.append(f"Worst hour: {self.worst_hour.label} "
                         f"(E={self.worst_hour.expectancy_r:+.2f}R, n={self.worst_hour.n})")
        if self.active_lessons:
            lines.append("")
            lines.append(f"Active lessons ({len(self.active_lessons)}):")
            for l in self.active_lessons[:8]:
                lines.append(f"  - [{l.action}] {l.headline_en}")
        if self.bias_flags:
            lines.append("")
            lines.append(f"Bias flags ({len(self.bias_flags)}):")
            for b in self.bias_flags:
                lines.append(f"  ! {b.severity:>6}: {b.name} — {b.description}")
        if self.psychological_warnings:
            lines.append("")
            lines.append("Psychological watch:")
            for w in self.psychological_warnings:
                lines.append(f"  * {w}")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def build_daily_briefing(journal: Journal,
                         lesson_book: LessonBook,
                         *,
                         pair: str = "EUR/USD",
                         lookback_days: int = 30,
                         bias_lookback_trades: int = 30,
                         minutes_to_next_high_impact_news: float = float("inf"),
                         now: Optional[datetime] = None,
                         ) -> DailyBriefing:
    """Build the morning briefing from the journal + active lessons.

    The function is deliberately pure-ish: it reads the journal and
    lesson book, computes derived state, and returns a `DailyBriefing`.
    It does not write anything back. The orchestrator decides whether
    to log the rendered briefing, persist it, or inject it.

    `minutes_to_next_high_impact_news` is the only piece of "live"
    context required from outside; it's used only to add a warning
    when news is imminent. Pass `inf` (default) when no calendar is
    wired up.
    """
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=lookback_days)
    trades = journal.read_since(since)
    metrics = compute_metrics(trades)

    # ---- streak + drawdown -----------------------------------------
    cw, cl = _current_streak(trades)
    dd = _current_drawdown_r(trades)

    # ---- cohort highlights -----------------------------------------
    best_setup, worst_setup = _highlight(
        cohort_table(trades, "setup_type"), dim="setup_type"
    )
    best_regime, worst_regime = _highlight(
        cohort_table(trades, "market_regime"), dim="market_regime"
    )
    best_hour, worst_hour = _highlight(
        hour_of_day_cohort(trades), dim="hour_utc"
    )

    # ---- active lessons --------------------------------------------
    active = sorted(lesson_book.active(),
                    key=lambda l: -l.confidence)

    # ---- bias scan -------------------------------------------------
    biases = scan_for_biases(trades, lookback=bias_lookback_trades)

    # ---- psychological warnings ------------------------------------
    warns = _psychological_warnings(
        dd_r=dd, consec_losses=cl, consec_wins=cw,
        minutes_to_news=minutes_to_next_high_impact_news,
        metrics=metrics, n_trades=len(trades),
    )

    # ---- one-line headline -----------------------------------------
    headline = _one_line_headline(
        metrics=metrics, dd=dd, cl=cl, cw=cw,
        n_lessons=len(active), n_biases=len(biases),
    )

    return DailyBriefing(
        generated_at=now,
        pair=pair,
        n_trades_lookback=len(trades),
        lookback_days=lookback_days,
        current_drawdown_r=dd,
        consecutive_losses=cl,
        consecutive_wins=cw,
        metrics=metrics,
        best_setup=best_setup,
        worst_setup=worst_setup,
        best_regime=best_regime,
        worst_regime=worst_regime,
        best_hour=best_hour,
        worst_hour=worst_hour,
        active_lessons=active,
        bias_flags=biases,
        psychological_warnings=warns,
        one_line_headline=headline,
    )


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _current_streak(trades: list[TradeRecord]) -> tuple[int, int]:
    """Return (consecutive_wins, consecutive_losses) at the *tail* of
    the trades list, ordered chronologically.

    Exactly one of the two is non-zero (or both zero if the last trade
    was a scratch).
    """
    if not trades:
        return 0, 0
    chrono = sorted(trades, key=lambda r: r.outcome.closed_at)
    cw = cl = 0
    for r in reversed(chrono):
        rm = r.outcome.r_multiple
        if rm > 0.05:
            if cl > 0:
                break
            cw += 1
        elif rm < -0.05:
            if cw > 0:
                break
            cl += 1
        else:
            break  # scratch breaks the streak
    return cw, cl


def _current_drawdown_r(trades: list[TradeRecord]) -> float:
    """Distance in R from the running peak of cumulative R; positive."""
    if not trades:
        return 0.0
    chrono = sorted(trades, key=lambda r: r.outcome.closed_at)
    cum = 0.0
    peak = 0.0
    for r in chrono:
        cum += r.outcome.r_multiple
        if cum > peak:
            peak = cum
    return max(0.0, peak - cum)


def _highlight(cohorts: dict[str, MetricsSummary],
               dim: str,
               min_n: int = 5) -> tuple[Optional[CohortHighlight], Optional[CohortHighlight]]:
    """Return (best, worst) cohort highlights by expectancy, ignoring
    cohorts with fewer than `min_n` trades. Returns (None, None) if no
    qualifying cohort exists.

    A small `min_n` is used here (not the patterns.py threshold of 10)
    because the briefing is *informational*: we want to surface what
    looks weak even on a thin sample, with the caveat that the
    headline itself flags the sample size.
    """
    candidates = [
        (label, m) for label, m in cohorts.items()
        if m.n_trades >= min_n and label not in ("unknown", "")
    ]
    if not candidates:
        return None, None
    best_label, best_m = max(candidates, key=lambda x: x[1].expectancy_r)
    worst_label, worst_m = min(candidates, key=lambda x: x[1].expectancy_r)
    best = CohortHighlight(
        dimension=dim, label=best_label,
        n=best_m.n_trades,
        expectancy_r=best_m.expectancy_r,
        win_rate=best_m.win_rate,
    )
    worst = CohortHighlight(
        dimension=dim, label=worst_label,
        n=worst_m.n_trades,
        expectancy_r=worst_m.expectancy_r,
        win_rate=worst_m.win_rate,
    )
    # If best == worst (only one cohort), return only best.
    if best_label == worst_label:
        return best, None
    return best, worst


def _psychological_warnings(*,
                            dd_r: float,
                            consec_losses: int,
                            consec_wins: int,
                            minutes_to_news: float,
                            metrics: MetricsSummary,
                            n_trades: int) -> list[str]:
    """Context-dependent warnings re-evaluated each session.

    Steenbarger: "the same trader is a different person at different
    points in their P&L curve". These warnings tell that other person
    what to watch for *today*.
    """
    out: list[str] = []
    if dd_r >= 5.0:
        out.append(
            f"deep drawdown ({dd_r:.1f}R): risk of revenge trading and "
            "stop-pulling — keep size at or below baseline"
        )
    elif dd_r >= 3.0:
        out.append(
            f"moderate drawdown ({dd_r:.1f}R): default to skipping "
            "marginal setups, do not chase"
        )
    if consec_losses >= 3:
        out.append(
            f"{consec_losses} consecutive losses: enforce a 30-min "
            "cooling-off window before the next entry"
        )
    if consec_wins >= 4:
        out.append(
            f"{consec_wins} consecutive wins: recency bias risk — "
            "do not size up; expectancy is computed over many trades"
        )
    if minutes_to_news < 30:
        out.append(
            f"high-impact news in {minutes_to_news:.0f} min: spread "
            "expansion expected; avoid new entries in the window"
        )
    if metrics.n_trades >= 30 and metrics.expectancy_r < 0:
        out.append(
            "30-day expectancy is negative — system is in a degraded "
            "state, downgrade size and review patterns before adding new ones"
        )
    if n_trades == 0:
        out.append(
            "no trades in the lookback window — system is cold; first "
            "trades of the period carry extra calibration risk"
        )
    return out


def _one_line_headline(*,
                       metrics: MetricsSummary,
                       dd: float,
                       cl: int,
                       cw: int,
                       n_lessons: int,
                       n_biases: int) -> str:
    """One readable sentence summarising the day. Forced brevity per
    Steenbarger ("if you can't say it in one line, you don't know it").
    """
    streak_part = ""
    if cw:
        streak_part = f", on a {cw}-win streak"
    elif cl:
        streak_part = f", on a {cl}-loss streak"

    state = "healthy"
    if metrics.expectancy_r < 0 and metrics.n_trades >= 20:
        state = "degraded"
    elif dd >= 5.0:
        state = "drawdown"
    elif metrics.sqn_label in ("good", "excellent", "superb"):
        state = "strong"

    return (
        f"System {state}: SQN {metrics.sqn:.2f}, "
        f"E {metrics.expectancy_r:+.2f}R over {metrics.n_trades} trades, "
        f"dd {dd:.1f}R{streak_part}; "
        f"{n_lessons} active lessons, {n_biases} bias flags."
    )
