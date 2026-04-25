# -*- coding: utf-8 -*-
"""Metrics — Tharp-style analytics over the journal.

The metrics here are deliberately the small, robust set every serious
trader checks. We resist the urge to compute dozens of vanity stats
(which have a way of inviting overfitting). Instead we focus on:

    * **Expectancy in R** (Tharp). The single most important number a
      retail trader can know. If expectancy < 0, no amount of position
      sizing rescues you.

    * **System Quality Number (SQN)** (Tharp).

          SQN = sqrt(N) * mean(R) / stdev(R)

      Tharp's interpretation:
          < 1.6   below average
          1.6-1.9 average
          2.0-2.4 good
          2.5-2.9 excellent
          3.0+    superb (rare; treat with skepticism if N small)

      The sqrt(N) term prevents you from celebrating after 5 trades.

    * **Profit Factor** = sum(wins) / sum(losses). Robust, easy to
      reason about. Schwager notes most Market Wizards run between 1.4
      and 2.5.

    * **Win rate** alone is a bad metric (you can be 90% wins and still
      lose money), but combined with avg-win-R and avg-loss-R it's
      diagnostic.

    * **Drawdown stats** (Hite, Kovner). Maximum peak-to-trough as the
      single risk metric that survives every regime.

    * **Cohort tables**. Not aggregate stats — *grouped* stats. By
      setup_type, by hour, by news_state, by spread_percentile. Carver
      makes the case (Systematic Trading ch.18): aggregate stats hide
      the failure modes you most need to find.

We keep all calculations in pure Python + `statistics` to stay
dependency-light. No numpy, no pandas — the journals we expect (~10k
trades over years) fit easily in lists.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Optional

from .journal import TradeRecord


# ----------------------------------------------------------------------
# Summary container.
# ----------------------------------------------------------------------
@dataclass
class MetricsSummary:
    """Headline metrics over a window of trades."""
    n_trades: int
    n_wins: int
    n_losses: int
    n_scratches: int
    win_rate: float                 # 0.0 - 1.0
    expectancy_r: float             # in R units
    avg_win_r: float
    avg_loss_r: float               # stored as a *positive* number
    profit_factor: float            # sum(wins) / |sum(losses)|; +inf if no loss
    sqn: float                      # Tharp's System Quality Number
    sqn_label: str                  # human reading per Tharp's table
    max_drawdown_r: float           # peak-to-trough in R, positive number
    longest_losing_streak: int
    longest_winning_streak: int
    avg_bars_held: float
    decision_outcome_correlation: float  # process-vs-result Pearson r

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ----------------------------------------------------------------------
# Top-level computation.
# ----------------------------------------------------------------------
def compute_metrics(trades: Iterable[TradeRecord]) -> MetricsSummary:
    """Compute the standard headline summary over `trades`.

    Returns a MetricsSummary even for empty input (zeroed out) so the
    briefing layer never has to special-case empty journals.
    """
    trades = list(trades)
    n = len(trades)
    if n == 0:
        return MetricsSummary(
            n_trades=0, n_wins=0, n_losses=0, n_scratches=0,
            win_rate=0.0, expectancy_r=0.0, avg_win_r=0.0, avg_loss_r=0.0,
            profit_factor=0.0, sqn=0.0, sqn_label="no_data",
            max_drawdown_r=0.0,
            longest_losing_streak=0, longest_winning_streak=0,
            avg_bars_held=0.0, decision_outcome_correlation=0.0,
        )

    rs = [t.outcome.r_multiple for t in trades]
    wins = [r for r in rs if r > 0.05]      # tiny positive treated as scratch
    losses = [r for r in rs if r < -0.05]
    scratches = n - len(wins) - len(losses)

    win_rate = len(wins) / n
    avg_win_r = statistics.fmean(wins) if wins else 0.0
    avg_loss_r = abs(statistics.fmean(losses)) if losses else 0.0

    # Expectancy follows Tharp's exact formulation:
    expectancy_r = win_rate * avg_win_r - (1.0 - win_rate) * avg_loss_r

    # Profit factor; +inf when there are no losses.
    sum_wins = sum(wins)
    sum_losses_abs = abs(sum(losses))
    if sum_losses_abs == 0:
        profit_factor = float("inf") if sum_wins > 0 else 0.0
    else:
        profit_factor = sum_wins / sum_losses_abs

    sqn_value = system_quality_number(rs)
    sqn_label = _sqn_label(sqn_value, n)

    max_dd_r = _max_drawdown_r(rs)
    win_streak = _longest_streak(rs, lambda r: r > 0)
    lose_streak = _longest_streak(rs, lambda r: r < 0)

    avg_bars = statistics.fmean([t.outcome.bars_held for t in trades])

    # Decision-vs-outcome quality correlation. Annie Duke: in a sound
    # system this should be *positive but not 1.0* — perfect
    # correlation means you're rationalising outcomes back into your
    # decision grade (resulting); near-zero means your decision rubric
    # ignores the variables that drive results.
    dq = [t.decision_quality_grade for t in trades if t.decision_quality_grade > 0]
    oq = [t.outcome_quality_grade for t in trades if t.outcome_quality_grade > 0]
    corr = _pearson_aligned(
        [t.decision_quality_grade for t in trades],
        [t.outcome_quality_grade for t in trades],
    ) if dq and oq else 0.0

    return MetricsSummary(
        n_trades=n, n_wins=len(wins), n_losses=len(losses),
        n_scratches=scratches,
        win_rate=win_rate,
        expectancy_r=expectancy_r,
        avg_win_r=avg_win_r,
        avg_loss_r=avg_loss_r,
        profit_factor=profit_factor,
        sqn=sqn_value,
        sqn_label=sqn_label,
        max_drawdown_r=max_dd_r,
        longest_losing_streak=lose_streak,
        longest_winning_streak=win_streak,
        avg_bars_held=avg_bars,
        decision_outcome_correlation=corr,
    )


# ----------------------------------------------------------------------
# SQN.
# ----------------------------------------------------------------------
def system_quality_number(r_multiples: list[float]) -> float:
    """Tharp's SQN: sqrt(N) * mean / stdev of R-multiples.

    For N < 2 returns 0 (stdev undefined). For zero variance returns 0
    (a system that always produces the same R is a coin toss in
    disguise — usually a sign of mislabelled trades).
    """
    n = len(r_multiples)
    if n < 2:
        return 0.0
    try:
        sd = statistics.stdev(r_multiples)
    except statistics.StatisticsError:
        return 0.0
    if sd == 0:
        return 0.0
    return math.sqrt(n) * statistics.fmean(r_multiples) / sd


def _sqn_label(value: float, n: int) -> str:
    """Tharp's interpretation table, with a 'low_n' caveat under 30
    trades — the sqrt(N) factor inflates small samples.
    """
    if n < 30:
        return "insufficient_sample"
    if value < 1.6:
        return "below_average"
    if value < 2.0:
        return "average"
    if value < 2.5:
        return "good"
    if value < 3.0:
        return "excellent"
    return "superb"


# ----------------------------------------------------------------------
# R-distribution.
# ----------------------------------------------------------------------
def r_distribution(trades: Iterable[TradeRecord],
                   bin_edges: Optional[list[float]] = None) -> dict[str, int]:
    """Histogram of R-multiples in human-readable buckets.

    Default bins: <-2R, [-2,-1), [-1, -0.5), [-0.5, 0), [0, 0.5),
    [0.5, 1), [1, 2), [2, 3), >=3R.
    """
    if bin_edges is None:
        bin_edges = [-2.0, -1.0, -0.5, 0.0, 0.5, 1.0, 2.0, 3.0]
    labels: list[str] = []
    for i, edge in enumerate(bin_edges):
        if i == 0:
            labels.append(f"<{edge:+g}R")
        labels.append(f"[{bin_edges[i-1]:+g}R..{edge:+g}R)" if i > 0 else
                      f"<{edge:+g}R")
    # Build sequential labels covering the full real line:
    seq_labels = []
    seq_labels.append(f"<{bin_edges[0]:+g}R")
    for i in range(1, len(bin_edges)):
        seq_labels.append(f"[{bin_edges[i-1]:+g}R..{bin_edges[i]:+g}R)")
    seq_labels.append(f">={bin_edges[-1]:+g}R")

    counts = {label: 0 for label in seq_labels}
    for t in trades:
        r = t.outcome.r_multiple
        idx = 0
        for edge in bin_edges:
            if r < edge:
                break
            idx += 1
        counts[seq_labels[idx]] += 1
    return counts


# ----------------------------------------------------------------------
# Cohort tables.
# ----------------------------------------------------------------------
def cohort_table(trades: Iterable[TradeRecord],
                 key: str | Callable[[TradeRecord], Any]) -> dict[str, MetricsSummary]:
    """Group trades by `key` and compute metrics per group.

    `key` may be a string field-name on TradeRecord (e.g. "setup_type")
    or a callable that returns the cohort label. Useful keys:

        * "setup_type"          — Carver's "system fitness per setup"
        * "market_regime"       — does our edge survive in chop?
        * "news_state"          — pre-event vs post-event vs blackout
        * a lambda for hour-of-day buckets
    """
    grouped: dict[str, list[TradeRecord]] = defaultdict(list)
    if isinstance(key, str):
        getter = lambda r, k=key: getattr(r, k, "unknown")
    else:
        getter = key
    for t in trades:
        try:
            label = str(getter(t))
        except Exception:
            label = "unknown"
        grouped[label].append(t)
    return {label: compute_metrics(group) for label, group in grouped.items()}


def hour_of_day_cohort(trades: Iterable[TradeRecord]) -> dict[str, MetricsSummary]:
    """Convenience: cohort by UTC hour-of-day in 4-hour buckets.

    Bucketing avoids 24 sparse cells; aligned to FX session edges:
        00-04 (Asia open)
        04-08 (Asia close / Europe pre-open)
        08-12 (London morning)
        12-16 (London/NY overlap)
        16-20 (NY afternoon)
        20-24 (NY close / Asia pre-open)
    """
    def bucket(t: TradeRecord) -> str:
        h = t.opened_at.hour
        for lo, hi in [(0,4),(4,8),(8,12),(12,16),(16,20),(20,24)]:
            if lo <= h < hi:
                return f"{lo:02d}-{hi:02d}_UTC"
        return "unknown"
    return cohort_table(trades, bucket)


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _max_drawdown_r(rs: list[float]) -> float:
    """Maximum cumulative R drawdown; positive number."""
    if not rs:
        return 0.0
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rs:
        cum += r
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _longest_streak(rs: list[float], pred: Callable[[float], bool]) -> int:
    cur = best = 0
    for r in rs:
        if pred(r):
            cur += 1
            if cur > best:
                best = cur
        else:
            cur = 0
    return best


def _pearson_aligned(xs: list[float], ys: list[float]) -> float:
    """Pearson correlation over aligned (x,y) pairs where both are
    non-zero (we treat 0 as 'unrated'). Returns 0.0 if fewer than 3
    rated pairs or zero variance.
    """
    pairs = [(x, y) for x, y in zip(xs, ys) if x != 0 and y != 0]
    if len(pairs) < 3:
        return 0.0
    xs_, ys_ = zip(*pairs)
    try:
        mx = statistics.fmean(xs_)
        my = statistics.fmean(ys_)
        num = sum((x - mx) * (y - my) for x, y in pairs)
        denom = math.sqrt(
            sum((x - mx) ** 2 for x in xs_) *
            sum((y - my) ** 2 for y in ys_)
        )
        if denom == 0:
            return 0.0
        return num / denom
    except statistics.StatisticsError:
        return 0.0
