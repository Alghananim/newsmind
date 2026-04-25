# -*- coding: utf-8 -*-
"""Pre-mortem — Klein/Kahneman pre-trade risk inventory.

Gary Klein introduced the pre-mortem in *Sources of Power* (1998) and
revisited it in *Streetlights and Shadows* (2009). Kahneman elevated
it in *Thinking, Fast and Slow* (ch.24) as the single most useful
debiasing technique in his thirty-year career studying judgment.

The mechanic is simple:

    "Imagine that this trade has already failed and we are now sitting
     in a post-mortem. What is the most likely cause?"

That tiny linguistic shift — from "what could go wrong" (passive,
hypothetical) to "what *did* go wrong" (committed, narrative) — pulls
out failure modes that prospective hindsight ignores. In Klein's field
work it surfaced ~30% more concrete risks than standard risk reviews.

How we use it in the system
---------------------------
GateMind, after the gate passes and before submitting the order,
calls `run_pre_mortem(plan, context)`. The function returns a
`PreMortemReport` with:

    * a ranked list of imagined failure modes, drawn from the
      patterns library (recurring losses with statistical support)
      *plus* generic FX failure modes that always apply
    * the inventory the trader/system has committed to in writing
    * a single-sentence prediction ("most likely outcome: loss because
      of news shock during +/-15min window")

GateMind writes the report into the journal record's
`pre_mortem_top_risk` and `pre_mortem_predicted_outcome` fields. After
the trade closes, post_mortem.py compares prediction against reality —
calibrating the system's self-awareness over time.

This is *not* a veto layer. The kill_switches and gate already vetoed
unacceptable trades. The pre-mortem is a *commitment device* for
trades we are about to take: by naming the most likely failure mode,
we resist the surprise-and-rationalise pattern Annie Duke calls
"resulting" (*Thinking in Bets*).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

from .journal import TradeRecord
from .patterns import DiscoveredPattern


# ----------------------------------------------------------------------
# Inputs and output types.
# ----------------------------------------------------------------------
@dataclass
class PreMortemContext:
    """The minimum context required to run a pre-mortem.

    All fields are duck-typed via attribute access so the orchestrator
    does not have to know about ChartMind/MarketMind specific types.
    """
    pair: str
    direction: str                   # "long" | "short"
    setup_type: str
    market_regime: str
    news_state: str
    minutes_to_next_high_impact_news: float   # +inf if none scheduled
    spread_percentile_rank: float    # 0.0 (calm) - 1.0 (very wide)
    rr_planned: float
    plan_confidence: float
    gate_combined_confidence: float
    recent_drawdown_r: float         # current peak-to-trough in R
    recent_consecutive_losses: int
    last_trade_was_loss: bool


@dataclass
class FailureMode:
    """One imagined way the trade could fail."""
    name: str                        # short slug
    description: str                 # one sentence, conversational
    severity: str                    # "low" | "medium" | "high"
    historical_support_n: int = 0    # how many past trades support this
    historical_support_p: float = 1.0
    source: str = "generic"          # "pattern" | "generic" | "regime"


@dataclass
class PreMortemReport:
    """Output: a ranked failure inventory and a single prediction."""
    pair: str
    generated_at: datetime
    failure_modes: list[FailureMode]
    top_failure_mode: str
    predicted_outcome: str           # "win" | "loss" | "scratch"
    rationale: str
    warnings_for_brain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["generated_at"] = self.generated_at.isoformat()
        d["failure_modes"] = [fm.__dict__.copy() for fm in self.failure_modes]
        return d


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def run_pre_mortem(ctx: PreMortemContext,
                   patterns: Optional[Iterable[DiscoveredPattern]] = None,
                   recent_trades: Optional[Iterable[TradeRecord]] = None
                   ) -> PreMortemReport:
    """Run a pre-mortem and return a ranked failure inventory.

    `patterns` should typically be the output of
    `patterns.mine_patterns()` filtered to `passes_bonferroni=True`.
    Patterns whose feature matches the proposed trade become the
    primary, evidence-backed failure modes. Generic FX failure modes
    are always considered as a baseline.

    `recent_trades` is used for streak-aware warnings (revenge trading
    risk after consecutive losses).
    """
    now = datetime.now(timezone.utc)
    failure_modes: list[FailureMode] = []

    # ---- 1. Pattern-derived failure modes --------------------------
    if patterns:
        for pat in patterns:
            if pat.direction != "adverse" or not pat.passes_bonferroni:
                continue
            if not _pattern_matches_trade(pat, ctx):
                continue
            failure_modes.append(FailureMode(
                name=pat.feature,
                description=(
                    f"history shows {pat.feature} carries expectancy "
                    f"{pat.cohort_expectancy_r:+.2f}R "
                    f"(rest of journal: {pat.rest_expectancy_r:+.2f}R, "
                    f"n={pat.cohort_n}, p={pat.p_value:.3f})"
                ),
                severity=_severity_from_effect(pat.expectancy_delta_r),
                historical_support_n=pat.cohort_n,
                historical_support_p=pat.p_value,
                source="pattern",
            ))

    # ---- 2. Always-applicable generic FX failure modes -------------
    failure_modes.extend(_generic_failure_modes(ctx))

    # ---- 3. Streak / context-driven failure modes ------------------
    if ctx.recent_consecutive_losses >= 2:
        failure_modes.append(FailureMode(
            name="psych_revenge_trading",
            description=(
                f"this is the {ctx.recent_consecutive_losses + 1}th trade "
                "after consecutive losses; revenge-trading risk elevated"
            ),
            severity="high" if ctx.recent_consecutive_losses >= 3 else "medium",
            source="regime",
        ))
    if ctx.recent_drawdown_r >= 3.0:
        failure_modes.append(FailureMode(
            name="psych_drawdown_pressure",
            description=(
                f"system in {ctx.recent_drawdown_r:.1f}R drawdown; "
                "tendency to oversize or skip stops increases here"
            ),
            severity="medium",
            source="regime",
        ))
    if ctx.minutes_to_next_high_impact_news < 60:
        failure_modes.append(FailureMode(
            name="news_proximity",
            description=(
                f"high-impact news in "
                f"{ctx.minutes_to_next_high_impact_news:.0f} min; expect "
                "spread expansion and possible whipsaw"
            ),
            severity="medium",
            source="generic",
        ))
    if ctx.spread_percentile_rank > 0.66:
        failure_modes.append(FailureMode(
            name="execution_wide_spread",
            description=(
                f"current spread is in the {ctx.spread_percentile_rank*100:.0f}th "
                "percentile; entry slippage will eat R"
            ),
            severity="low" if ctx.spread_percentile_rank < 0.85 else "medium",
            source="generic",
        ))
    if ctx.rr_planned < 1.2:
        failure_modes.append(FailureMode(
            name="plan_tight_rr",
            description=(
                f"planned R:R is {ctx.rr_planned:.2f}; thin margin for "
                "any slippage or partial exit"
            ),
            severity="low",
            source="generic",
        ))

    # ---- 4. Rank by severity, then historical-support strength -----
    sev_rank = {"high": 3, "medium": 2, "low": 1}
    failure_modes.sort(
        key=lambda fm: (-sev_rank.get(fm.severity, 0),
                        fm.historical_support_p,
                        -fm.historical_support_n),
    )

    top = failure_modes[0] if failure_modes else FailureMode(
        name="none_identified",
        description="no obvious failure mode flagged by pre-mortem",
        severity="low",
    )

    predicted = _predict_outcome(failure_modes, ctx)
    rationale = _build_rationale(top, predicted, ctx)
    warnings = _warnings_for_brain(failure_modes, ctx)

    return PreMortemReport(
        pair=ctx.pair,
        generated_at=now,
        failure_modes=failure_modes,
        top_failure_mode=top.name,
        predicted_outcome=predicted,
        rationale=rationale,
        warnings_for_brain=warnings,
    )


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _pattern_matches_trade(pat: DiscoveredPattern, ctx: PreMortemContext) -> bool:
    """Decide whether a discovered adverse pattern applies to this trade.

    Pattern features are encoded as `field=value` strings (see
    patterns.py). We parse them here and check the proposed trade.
    """
    f = pat.feature
    if "=" in f:
        field, value = f.split("=", 1)
        if field == "setup_type":
            return ctx.setup_type == value
        if field == "market_regime":
            return ctx.market_regime == value
        if field == "news_state":
            return ctx.news_state == value
        if field == "spread":
            if "wide" in value:
                return ctx.spread_percentile_rank >= 0.66
            if "tight" in value:
                return ctx.spread_percentile_rank <= 0.33
        if field == "execution":
            return False  # only knowable post-fill
        if field == "decision_quality_low":
            return False  # only knowable post-trade
        if field.startswith("hour_utc"):
            # We don't have the wall clock here; the orchestrator knows it.
            return False
    return False


def _severity_from_effect(expectancy_delta_r: float) -> str:
    abs_d = abs(expectancy_delta_r)
    if abs_d >= 0.6:
        return "high"
    if abs_d >= 0.3:
        return "medium"
    return "low"


def _generic_failure_modes(ctx: PreMortemContext) -> list[FailureMode]:
    """Failure modes that always belong on the inventory, regardless
    of journal history. They are listed at low severity unless context
    elevates them (handled by the caller).
    """
    return [
        FailureMode(
            name="setup_invalidation",
            description=(
                "price reverses through the structural level the setup "
                "depended on; stop is correctly placed but psychological "
                "pressure to move it is real"
            ),
            severity="low",
        ),
        FailureMode(
            name="time_decay",
            description=(
                "trade goes nowhere within the time budget; opportunity "
                "cost as capital sits while better setups appear"
            ),
            severity="low",
        ),
        FailureMode(
            name="liquidity_pocket",
            description=(
                "during session transitions liquidity thins and stops "
                "trigger on noise rather than real flow"
            ),
            severity="low",
        ),
    ]


def _predict_outcome(failure_modes: list[FailureMode],
                     ctx: PreMortemContext) -> str:
    """Pick the single most likely outcome label.

    Heuristic: if any high-severity failure mode is in the inventory
    we predict 'loss'; if mostly low-severity and the gate confidence
    is high we predict 'win'; else 'scratch'. Calibration is checked
    over time by post_mortem.py.
    """
    if any(fm.severity == "high" for fm in failure_modes):
        return "loss"
    medium_count = sum(1 for fm in failure_modes if fm.severity == "medium")
    if medium_count >= 2:
        return "loss"
    if ctx.gate_combined_confidence >= 0.65 and medium_count == 0:
        return "win"
    return "scratch"


def _build_rationale(top: FailureMode, predicted: str,
                     ctx: PreMortemContext) -> str:
    return (
        f"top imagined failure: {top.name} "
        f"(severity={top.severity}). predicted outcome: {predicted}. "
        f"gate confidence={ctx.gate_combined_confidence:.2f}, "
        f"plan confidence={ctx.plan_confidence:.2f}, "
        f"R:R={ctx.rr_planned:.2f}."
    )


def _warnings_for_brain(failure_modes: list[FailureMode],
                        ctx: PreMortemContext) -> list[str]:
    """Short imperative warnings to surface back to the deciding brain
    via the memory injector. Two purposes:

        1. force the brain to address the failure mode in its
           rationale (process discipline)
        2. give the post-mortem something concrete to compare against

    Kept short so they fit in a system-prompt augmentation block.
    """
    out: list[str] = []
    for fm in failure_modes:
        if fm.severity == "high":
            out.append(f"HIGH-RISK: {fm.description}")
        elif fm.severity == "medium":
            out.append(f"watch: {fm.description}")
    return out[:5]   # cap at five — too many warnings dilute attention
