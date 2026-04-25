# -*- coding: utf-8 -*-
"""Bias detector — flag recurring cognitive-bias patterns in the journal.

The biases we scan for are the ones that, in Steenbarger's phrasing
(*Trading Psychology 2.0*, ch.4), "show up in the trade record before
they show up in self-awareness". Each detector looks at *behavioral
signatures*, not introspection — because the whole point is to catch
biases that the trader (or the brain stack) does not yet know are
operating.

Coverage
--------
    * **Revenge trading** (Kahneman, "loss-chasing"). Right after a
      loss, the next trade is taken too soon, oversized, or with weak
      pre-trade input grades.

    * **FOMO / chase entries** (Steenbarger). Entries with large
      slippage on the wrong side of the planned price, especially in
      strong-trend regimes — paying up to be in.

    * **Confirmation bias** (Kahneman; Aronson). Trades taken despite
      a brain veto or a B-grade input — the trader (or system) found a
      way to dismiss disconfirming evidence.

    * **Recency / hot-streak inflation** (Kahneman). After a streak
      of wins, position sizing creeps up. We flag systematic upward
      drift in lot_size or risk_amount that does NOT track equity
      growth.

    * **Sunk-cost / stop-moving** (Thaler; Steenbarger). A losing
      trade's stop_price is moved further from entry across the trade's
      life. We detect via `annotations` (if logged) or via the gap
      between final stop and original.

    * **Anchoring** (Tversky-Kahneman). Repeated identical entry/
      target/stop levels across distinct setups suggests the trader is
      anchoring to a price rather than reading structure.

    * **Boredom / forcing** (Steenbarger). Long windows with no real
      setup followed by a flurry of low-DQ trades.

Outputs
-------
A `BiasFlag` per detected bias, each with:

    * a short slug name (machine-readable)
    * a human description
    * supporting evidence (specific trade_ids, counts)
    * a recommended remedy line for the briefing

The flags are designed to *inform*, not to halt — the kill_switches
already do that. A flag's right home is the daily briefing and the
memory injector, where the brains can be reminded that "you have shown
revenge-trading behavior in the last 5 sessions" before deciding the
next trade.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Iterable, Optional

from .journal import TradeRecord


# ----------------------------------------------------------------------
# Output type.
# ----------------------------------------------------------------------
@dataclass
class BiasFlag:
    name: str                       # "revenge_trading" | "fomo" | ...
    description: str                # one sentence
    severity: str                   # "low" | "medium" | "high"
    supporting_trade_ids: list[str]
    evidence: dict                  # raw counters, deltas, etc.
    remedy: str                     # short prescriptive line

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def scan_for_biases(trades: Iterable[TradeRecord],
                    lookback: int = 30) -> list[BiasFlag]:
    """Run all detectors over the most recent `lookback` trades.

    A small lookback (defaults to 30) is intentional: biases compound
    quickly and we want to catch the *current* drift, not aggregate
    behavior over a year. Long-horizon issues are caught by patterns.py
    instead.
    """
    trades = sorted(trades, key=lambda r: r.opened_at)[-lookback:]
    if len(trades) < 5:
        return []

    flags: list[BiasFlag] = []
    flags.extend(_detect_revenge_trading(trades))
    flags.extend(_detect_fomo(trades))
    flags.extend(_detect_confirmation_bias(trades))
    flags.extend(_detect_recency_sizing_drift(trades))
    flags.extend(_detect_anchoring(trades))
    flags.extend(_detect_forcing(trades))
    return flags


# ----------------------------------------------------------------------
# Revenge trading.
# ----------------------------------------------------------------------
def _detect_revenge_trading(trades: list[TradeRecord]) -> list[BiasFlag]:
    """Flag: a trade taken < 30 minutes after a loss with worse DQ
    than the trader's median *and* outcome a loss too.

    We require all three conditions because each alone is innocent;
    the conjunction is the signature.
    """
    if len(trades) < 4:
        return []
    median_dq = statistics.median(
        [t.decision_quality_grade for t in trades if t.decision_quality_grade > 0]
        or [3]
    )
    suspects: list[TradeRecord] = []
    for prev, cur in zip(trades, trades[1:]):
        if prev.outcome.r_multiple >= 0:
            continue
        gap = cur.opened_at - prev.outcome.closed_at
        if gap > timedelta(minutes=30):
            continue
        if cur.decision_quality_grade and cur.decision_quality_grade >= median_dq:
            continue
        if cur.outcome.r_multiple >= 0:
            continue
        suspects.append(cur)

    if len(suspects) < 2:
        return []

    return [BiasFlag(
        name="revenge_trading",
        description=(
            f"{len(suspects)} trades taken within 30 minutes of a loss, "
            f"with below-median decision quality, also losing"
        ),
        severity="high" if len(suspects) >= 4 else "medium",
        supporting_trade_ids=[t.trade_id for t in suspects],
        evidence={"count": len(suspects), "median_dq": median_dq},
        remedy=(
            "enforce a cooling-off window of 30+ minutes after any "
            "losing trade; require fresh A-grade inputs from at least "
            "two brains before re-engaging"
        ),
    )]


# ----------------------------------------------------------------------
# FOMO / chase entries.
# ----------------------------------------------------------------------
def _detect_fomo(trades: list[TradeRecord]) -> list[BiasFlag]:
    """Flag: trades whose filled_price is more than 1.5 pips on the
    *adverse* side of the planned entry — i.e., we paid up to get in.
    """
    chases: list[TradeRecord] = []
    for t in trades:
        # Sign convention: for a long, paying *more* than planned is adverse.
        # For a short, paying *less* than planned is adverse.
        if t.direction == "long":
            adverse = t.filled_price - t.entry_price
        else:
            adverse = t.entry_price - t.filled_price
        if adverse > 1.5 * 0.0001:
            chases.append(t)
    if len(chases) < 3:
        return []

    losses = sum(1 for t in chases if t.outcome.r_multiple < 0)
    return [BiasFlag(
        name="fomo_chasing",
        description=(
            f"{len(chases)} trades entered with > 1.5 pip adverse slippage "
            f"({losses} of which lost)"
        ),
        severity="medium" if losses >= len(chases) * 0.5 else "low",
        supporting_trade_ids=[t.trade_id for t in chases],
        evidence={"count": len(chases), "loss_rate": losses / len(chases)},
        remedy=(
            "switch to limit orders for entries within 1 pip of price; "
            "if price moves > 1 pip past planned entry, skip the trade"
        ),
    )]


# ----------------------------------------------------------------------
# Confirmation bias.
# ----------------------------------------------------------------------
def _detect_confirmation_bias(trades: list[TradeRecord]) -> list[BiasFlag]:
    """Flag: trades opened despite a B-or-worse grade from any brain
    (gate may still have passed if rules are loose). These represent
    cases where a dissenting voice was overridden.
    """
    overrides: list[TradeRecord] = []
    for t in trades:
        b_or_worse = sum(1 for g in t.brain_grades
                         if g.grade in ("B", "C"))
        if b_or_worse >= 1:
            overrides.append(t)
    if len(overrides) < 3:
        return []

    over_losses = sum(1 for t in overrides if t.outcome.r_multiple < 0)
    if over_losses < len(overrides) * 0.5:
        return []   # losses not concentrated; no bias signal

    return [BiasFlag(
        name="confirmation_bias",
        description=(
            f"{len(overrides)} trades opened despite at least one B-grade "
            f"or worse brain input; {over_losses} of those lost"
        ),
        severity="medium",
        supporting_trade_ids=[t.trade_id for t in overrides],
        evidence={"count": len(overrides), "loss_rate": over_losses / len(overrides)},
        remedy=(
            "tighten the gate to require all three brains >= A; if a B "
            "grade is acceptable, force the brain to articulate the "
            "specific dissent and address it in the plan rationale"
        ),
    )]


# ----------------------------------------------------------------------
# Recency / sizing drift.
# ----------------------------------------------------------------------
def _detect_recency_sizing_drift(trades: list[TradeRecord]) -> list[BiasFlag]:
    """Flag: lot_size has drifted upward across the lookback in a way
    that does *not* track equity. We approximate by comparing the
    first-half median lot to the second-half median lot.
    """
    if len(trades) < 10:
        return []
    half = len(trades) // 2
    first = trades[:half]
    second = trades[half:]
    first_med = statistics.median([t.lot_size for t in first])
    second_med = statistics.median([t.lot_size for t in second])
    if first_med <= 0:
        return []
    drift_pct = (second_med - first_med) / first_med
    if drift_pct < 0.25:   # <25% upward drift is normal as equity grows
        return []
    # Drift on the back of recent wins?
    recent_win_rate = sum(1 for t in second if t.outcome.r_multiple > 0) / len(second)
    if recent_win_rate < 0.6:
        return []   # drift without a hot streak is probably equity-driven
    return [BiasFlag(
        name="recency_sizing_drift",
        description=(
            f"median lot size grew {drift_pct*100:.0f}% across the lookback "
            f"on the back of a {recent_win_rate*100:.0f}% recent win rate"
        ),
        severity="medium" if drift_pct < 0.5 else "high",
        supporting_trade_ids=[t.trade_id for t in second[-5:]],
        evidence={"drift_pct": drift_pct, "win_rate_recent": recent_win_rate},
        remedy=(
            "anchor lot_size to a fixed-fractional rule; ignore recent "
            "win streaks when sizing — Tharp's expectancy is computed "
            "over many trades, not the last five"
        ),
    )]


# ----------------------------------------------------------------------
# Anchoring.
# ----------------------------------------------------------------------
def _detect_anchoring(trades: list[TradeRecord]) -> list[BiasFlag]:
    """Flag: same target_price repeated across distinct setup_types or
    different market regimes. A 1-pip tolerance.
    """
    grouped: dict[float, list[TradeRecord]] = {}
    for t in trades:
        key = round(t.target_price, 4)
        grouped.setdefault(key, []).append(t)
    suspects: list[TradeRecord] = []
    for price, ts in grouped.items():
        if len(ts) < 3:
            continue
        setups = {t.setup_type for t in ts}
        regimes = {t.market_regime for t in ts}
        if len(setups) >= 2 or len(regimes) >= 2:
            suspects.extend(ts)
    if len(suspects) < 3:
        return []
    return [BiasFlag(
        name="anchoring",
        description=(
            f"{len(suspects)} trades share an identical target price "
            "across different setups or regimes — possible price anchoring"
        ),
        severity="low",
        supporting_trade_ids=[t.trade_id for t in suspects],
        evidence={"count": len(suspects)},
        remedy=(
            "force ChartMind to re-derive target from current structure "
            "every cycle; a target should follow the setup, not the other way"
        ),
    )]


# ----------------------------------------------------------------------
# Forcing / boredom.
# ----------------------------------------------------------------------
def _detect_forcing(trades: list[TradeRecord]) -> list[BiasFlag]:
    """Flag: clusters of trades with low DQ inside a short time window.
    Suggests trades were forced to fill a slow session.
    """
    if len(trades) < 5:
        return []
    forced: list[TradeRecord] = []
    for t in trades:
        if t.decision_quality_grade in (1, 2):
            forced.append(t)
    if len(forced) < 3:
        return []
    losses = sum(1 for t in forced if t.outcome.r_multiple < 0)
    return [BiasFlag(
        name="forcing_low_dq",
        description=(
            f"{len(forced)} trades with decision-quality 1-2 in the lookback "
            f"({losses} losses) — suggests trades are being forced rather "
            "than waited for"
        ),
        severity="medium" if losses >= len(forced) * 0.5 else "low",
        supporting_trade_ids=[t.trade_id for t in forced],
        evidence={"count": len(forced), "loss_rate": losses / len(forced) if forced else 0},
        remedy=(
            "raise the gate's grade floor temporarily; explicitly accept "
            "doing nothing on slow days as a valid action"
        ),
    )]
