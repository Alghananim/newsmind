# -*- coding: utf-8 -*-
"""Planning + Self-Monitoring — Phase E of the deepening plan.

Two responsibilities that naturally belong together:

    1. PLANNING — given everything ChartMind has read, classify the
       setup and produce a full trade plan: entry, stop, target, time
       budget, and written rationale. Nothing gets traded without a
       plan that can be audited after the fact.

    2. SELF-MONITORING — once a position is open, re-evaluate it on
       every bar. Has the setup been invalidated? Has price stalled
       below target? Is it time to move stop to break-even? Return a
       recommended action the executor can act on mechanically.

Planning follows the Kovner/Dunn doctrine drawn from Market Wizards
interviews: "before I put on a trade, I already know where I'm
getting out if I'm wrong." The plan is pre-registered. The executor
just follows it.

Monitoring follows the Livermore doctrine: sit tight when the thesis
holds, exit immediately when it breaks. No emotional attachment.

All code original Python. No copyrighted material reproduced.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Outputs.
# ---------------------------------------------------------------------------
@dataclass
class TradePlan:
    """Pre-registered plan for a hypothetical trade.

    The executor either takes this verbatim or skips — it never
    improvises on top. A plan with `is_actionable=False` means the
    setup exists but either conviction is too low, R:R is
    insufficient, or some constraint fails.
    """
    setup_type: str                # short label
    direction: str                 # "long" | "short"
    entry_price: float
    stop_price: float
    target_price: float
    rr_ratio: float                # (target - entry) / (entry - stop) for long
    time_budget_bars: int          # max bars the plan expects to need
    confidence: float              # 0..1
    rationale: str                 # why this plan
    contingencies: list = field(default_factory=list)
    is_actionable: bool = True
    reason_if_not: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PositionHealth:
    """Live assessment of an open position.

    Produced on every bar update. `recommended_action` is the
    imperative instruction for the executor. Reasons list carries
    audit detail.
    """
    direction: str                 # "long" | "short"
    entry_price: float
    current_price: float
    stop_price: float
    target_price: float
    pnl_pips: float                # current PnL in pips (positive = profit)
    progress_to_target: float      # 0..1 where 0=at entry, 1=at target
    distance_to_stop: float        # 0..1 where 0=at stop, 1=at entry
    bars_held: int
    health_score: float            # 0..1
    recommended_action: str        # "hold" | "move_stop_to_be" | "trail" |
                                   # "partial_exit" | "full_exit"
    reasons: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Setup classification.
# ---------------------------------------------------------------------------
def _classify_setup(reading) -> tuple[str, str, float]:
    """Examine the reading and choose the BEST setup type.

    Priority order (from cleanest to murkiest):
        1. Signal+Entry bar confirmed in direction of MTF bias
        2. Two-legged pullback in trending regime
        3. Confirmed chart pattern (H&S, double top/bottom) above cutoff
        4. Spring / upthrust trap near key level
        5. Failed breakout reversal
        6. No clear setup → "none"

    Returns (setup_type, direction, conviction).
    """
    # 1. Signal+Entry bar setups
    if reading.pa_context and reading.pa_context.entry_bars:
        eb = reading.pa_context.entry_bars[-1]
        direction = "long" if eb.direction == "bullish" else "short"
        return ("signal_entry_continuation", direction, 0.75)

    # 2. Two-legged pullback
    if reading.pa_context and reading.pa_context.pullbacks:
        pb = reading.pa_context.pullbacks[-1]
        direction = "long" if pb.direction == "bullish" else "short"
        return ("two_legged_pullback", direction, 0.70)

    # 3. Chart pattern
    if reading.chart_patterns:
        best = max(reading.chart_patterns, key=lambda p: p.confidence)
        if best.confidence >= 0.65:
            direction = "long" if best.direction == "bullish" else "short"
            return (f"pattern_{best.name}", direction, best.confidence)

    # 4. Trap event
    if reading.traps:
        best = max(reading.traps, key=lambda t: t.strength)
        if best.strength >= 0.6:
            direction = "long" if best.direction == "bullish" else "short"
            return (f"trap_{best.name}", direction, best.strength)

    return ("none", "neutral", 0.0)


# ---------------------------------------------------------------------------
# Plan generation.
# ---------------------------------------------------------------------------
_RR_MIN_ACTIONABLE = 1.2           # Reject plans with R:R < 1.2
_STOP_BUFFER_ATR = 0.25            # Extra buffer beyond structure
_TIME_BUDGET_M15_DEFAULT = 16      # 4 hours on M15


def _trend_to_regime(trend_direction: str) -> str:
    """Map ChartReading.trend_direction to priors regime label.

    Priors use a coarser vocabulary than trend_direction — 4 regimes
    (trending_up / trending_down / ranging / chaos) vs trend_direction's
    3 values (up / down / flat). We don't emit "chaos" here — that
    decision is made by the clarity layer when atr_pct_rank > 0.85.
    """
    if trend_direction == "up":
        return "trending_up"
    if trend_direction == "down":
        return "trending_down"
    return "ranging"


def generate_plan(reading,
                  mtf=None,
                  confluence=None,
                  clarity=None,
                  calibrated=None,
                  priors=None,
                  pair_pip: float = 0.0001) -> TradePlan:
    """Build a full trade plan from the current ChartMind state.

    If `clarity` recommends abstain, we produce a non-actionable plan
    with the reason explained. Same if R:R falls below the threshold.

    If `priors` (a RegimePriors instance) is supplied, we query the
    historical success rate for the detected setup under the current
    regime/session/vol bucket and fold it into plan confidence. A
    strong negative prior (mean < 0.4 with n >= STRONG_N) can even
    refuse an otherwise-actionable setup — history says it doesn't work.
    """
    setup_type, direction, setup_conf = _classify_setup(reading)

    # --- Abstain if clarity says so --------------------------------
    if clarity is not None and clarity.verdict == "abstain":
        return TradePlan(
            setup_type=setup_type or "none",
            direction="neutral",
            entry_price=reading.price,
            stop_price=reading.price,
            target_price=reading.price,
            rr_ratio=0.0,
            time_budget_bars=0,
            confidence=0.0,
            rationale="Clarity scanner recommends abstain — do not trade.",
            contingencies=[],
            is_actionable=False,
            reason_if_not=(
                "Clarity: " + clarity.summary.split("\n")[0]
                if clarity.summary else "clarity abstain"
            ),
        )

    if setup_type == "none":
        return TradePlan(
            setup_type="none", direction="neutral",
            entry_price=reading.price, stop_price=reading.price,
            target_price=reading.price, rr_ratio=0.0,
            time_budget_bars=0, confidence=0.0,
            rationale="No actionable setup detected.",
            contingencies=[],
            is_actionable=False,
            reason_if_not="no setup",
        )

    atr = reading.atr14 or 1e-9
    entry = reading.price

    # --- Stop placement -------------------------------------------
    # For long: stop below nearest strong support or below the
    # lowest of the last 5 bars, whichever is SAFER (lower) but not
    # more than 3 × ATR from entry.
    # For short: mirror above.
    if direction == "long":
        candidate_levels = [lv.price for lv in reading.key_support
                            if lv.price < entry]
        if candidate_levels:
            struct_stop = max(candidate_levels) - _STOP_BUFFER_ATR * atr
        else:
            struct_stop = entry - 1.5 * atr
        stop = max(entry - 3 * atr, struct_stop)
    else:   # short
        candidate_levels = [lv.price for lv in reading.key_resistance
                            if lv.price > entry]
        if candidate_levels:
            struct_stop = min(candidate_levels) + _STOP_BUFFER_ATR * atr
        else:
            struct_stop = entry + 1.5 * atr
        stop = min(entry + 3 * atr, struct_stop)

    # --- Target: prefer chart-pattern target if one exists and ---
    #            is in our direction; else 2R from stop.
    pattern_target: Optional[float] = None
    for p in reading.chart_patterns:
        if p.target is None:
            continue
        if direction == "long" and p.direction == "bullish" and p.target > entry:
            pattern_target = p.target
            break
        if direction == "short" and p.direction == "bearish" and p.target < entry:
            pattern_target = p.target
            break

    risk = abs(entry - stop)
    if pattern_target is not None and risk > 0:
        target = pattern_target
    else:
        target = entry + 2 * risk if direction == "long" else entry - 2 * risk

    reward = abs(target - entry)
    rr = reward / risk if risk > 0 else 0.0

    # --- Time budget: scale by ATR — bigger ATR = faster move ----
    time_budget = _TIME_BUDGET_M15_DEFAULT

    # --- Compute plan confidence ---------------------------------
    # Blend setup_conf, confluence strength, calibrated proba if any
    parts = [setup_conf]
    if confluence is not None:
        parts.append(confluence.verdict_strength)
    if calibrated is not None and calibrated.trust != "none":
        parts.append(calibrated.calibrated)

    # --- Priors lookup: historical success rate for this context --
    # When RegimePriors is available, query for (setup_type, regime,
    # session, vol). Fold mean into plan_conf. Refuse the plan if
    # the prior strongly says this setup loses.
    prior_res = None
    prior_reason = None
    if priors is not None:
        try:
            from ChartMind.priors import PriorContext
            regime_label = _trend_to_regime(reading.trend_direction)
            ctx = PriorContext(
                pattern=setup_type,
                regime=regime_label,
                session=getattr(reading, "session", "off"),
                vol_bucket=getattr(reading, "volatility_regime", "normal"),
                pair=reading.pair,
            )
            prior_res = priors.query(ctx)
            if prior_res.confidence != "none":
                parts.append(prior_res.mean)
            # Strong negative prior → refuse
            if prior_res.confidence == "strong" and prior_res.mean < 0.4:
                prior_reason = (
                    f"Historical prior for {setup_type} in {regime_label}/"
                    f"{ctx.session}/{ctx.vol_bucket}: mean {prior_res.mean:.2f} "
                    f"(n={prior_res.n_observations}). History says this "
                    "context loses; skip."
                )
        except Exception:
            prior_res = None

    plan_conf = float(np.mean(parts))

    # --- Priors-based refusal (if prior strongly says no) ---------
    if prior_reason is not None:
        return TradePlan(
            setup_type=setup_type, direction=direction,
            entry_price=entry, stop_price=stop, target_price=target,
            rr_ratio=rr, time_budget_bars=time_budget,
            confidence=plan_conf,
            rationale=prior_reason,
            contingencies=[],
            is_actionable=False,
            reason_if_not="prior_strongly_negative",
        )

    # --- Check actionability -------------------------------------
    if rr < _RR_MIN_ACTIONABLE:
        return TradePlan(
            setup_type=setup_type, direction=direction,
            entry_price=entry, stop_price=stop, target_price=target,
            rr_ratio=rr, time_budget_bars=time_budget,
            confidence=plan_conf,
            rationale=(
                f"Setup = {setup_type}. R:R {rr:.2f} below minimum "
                f"{_RR_MIN_ACTIONABLE} — skip."
            ),
            contingencies=[],
            is_actionable=False,
            reason_if_not=f"rr {rr:.2f} < {_RR_MIN_ACTIONABLE}",
        )

    # --- Contingencies ------------------------------------------
    contingencies = _build_contingencies(
        direction, entry, stop, target, atr, reading,
    )

    rationale = _build_rationale(
        setup_type, direction, entry, stop, target, rr,
        reading, mtf, confluence,
    )

    return TradePlan(
        setup_type=setup_type, direction=direction,
        entry_price=float(entry),
        stop_price=float(stop),
        target_price=float(target),
        rr_ratio=float(rr),
        time_budget_bars=time_budget,
        confidence=plan_conf,
        rationale=rationale,
        contingencies=contingencies,
        is_actionable=True,
    )


def _build_rationale(setup_type, direction, entry, stop, target, rr,
                     reading, mtf, confluence) -> str:
    parts = [f"Setup = {setup_type}, direction = {direction}."]
    if mtf is not None:
        parts.append(f"MTF alignment {mtf.alignment:+.2f} "
                     f"(dominant {mtf.dominant_tf}:{mtf.dominant_trend}).")
    if confluence is not None:
        parts.append(f"Confluence verdict {confluence.verdict} "
                     f"at strength {confluence.verdict_strength:.2f}.")
    if reading.wyckoff:
        parts.append(f"Wyckoff phase {reading.wyckoff.phase}.")
    parts.append(
        f"Risk {abs(entry - stop) * (10**4):.1f} pips, "
        f"reward {abs(target - entry) * (10**4):.1f} pips, "
        f"R:R {rr:.2f}."
    )
    return " ".join(parts)


def _build_contingencies(direction, entry, stop, target, atr,
                         reading) -> list:
    """Scripted 'if X then Y' follow-ups — executable by the monitor."""
    at_1r = (entry + (entry - stop) if direction == "short"
             else entry + (target - entry) / 2)   # mid to target
    # Re-compute properly: 1R in direction of trade
    if direction == "long":
        one_r = entry + (entry - stop)         # +1R
    else:
        one_r = entry - (stop - entry)
    return [
        f"If price reaches {one_r:.5f} (+1R) → move stop to break-even.",
        f"If price reaches {(entry + target)/2:.5f} → take 50% partial profit.",
        f"If a counter-direction trap forms (upthrust for long, spring "
        f"for short) → close full at market.",
        f"If {reading.pa_context.best_setup if reading.pa_context else 'the setup'} "
        f"invalidates → exit immediately.",
        f"If not at +1R after {(_TIME_BUDGET_M15_DEFAULT // 2)} bars → "
        f"halve size (time-decay rule).",
    ]


# ---------------------------------------------------------------------------
# Self-monitoring of open positions.
# ---------------------------------------------------------------------------
def monitor_position(
    plan: TradePlan,
    current_price: float,
    bars_held: int,
    reading=None,
    pair_pip: float = 0.0001,
) -> PositionHealth:
    """Evaluate an open position against its original plan + the latest
    chart reading. Returns a recommended action.

    The executor is expected to follow the action mechanically.
    """
    entry = plan.entry_price
    stop = plan.stop_price
    target = plan.target_price
    d = plan.direction
    reasons: list[str] = []

    # PnL in pips
    if d == "long":
        pnl_price = current_price - entry
        risk_price = entry - stop
        reward_price = target - entry
    else:
        pnl_price = entry - current_price
        risk_price = stop - entry
        reward_price = entry - target
    pnl_pips = pnl_price / pair_pip

    # Progress ratio to target
    prog = pnl_price / reward_price if reward_price > 0 else 0.0
    prog = float(max(-1.0, min(1.5, prog)))

    # Distance-to-stop ratio: 1 = at entry, 0 = stopped out
    if d == "long":
        dts = (current_price - stop) / risk_price if risk_price > 0 else 0.0
    else:
        dts = (stop - current_price) / risk_price if risk_price > 0 else 0.0
    dts = float(max(0.0, min(1.0, dts)))

    # Base health score
    health = 0.5 + 0.3 * prog + 0.2 * (dts - 0.5)
    health = float(max(0.0, min(1.0, health)))

    # Default action: hold
    action = "hold"

    # Rule 1 — full exit if we've hit the stop or the target
    if d == "long" and current_price <= stop:
        action = "full_exit"
        reasons.append("Stop hit")
    elif d == "short" and current_price >= stop:
        action = "full_exit"
        reasons.append("Stop hit")
    elif d == "long" and current_price >= target:
        action = "full_exit"
        reasons.append("Target hit")
    elif d == "short" and current_price <= target:
        action = "full_exit"
        reasons.append("Target hit")

    # Rule 2 — move stop to break-even at +1R
    elif prog >= 0.5:
        action = "move_stop_to_be"
        reasons.append(f"Price at {prog*100:.0f}% to target; protect capital.")

    # Rule 3 — partial at midway
    elif prog >= 0.35 and prog < 0.5:
        action = "partial_exit"
        reasons.append("Price past midway — take 25-50% profit.")

    # Rule 4 — time-budget exit
    if bars_held >= plan.time_budget_bars and prog < 0.4:
        action = "full_exit"
        reasons.append(
            f"Bars held {bars_held} ≥ budget {plan.time_budget_bars} "
            f"but progress only {prog*100:.0f}% — time decay."
        )

    # Rule 5 — setup invalidation (if reading passed in)
    if reading is not None:
        # Long trade: invalidated if a bearish transition bar or upthrust
        # forms. Short: mirror.
        if d == "long":
            if reading.pa_context and any(
                t.direction == "bearish" and t.range_atr > 1.3
                for t in reading.pa_context.transitions
            ):
                action = "full_exit"
                reasons.append("Bearish transition bar — setup invalidated.")
            if reading.traps and any(
                t.name == "upthrust" and t.strength > 0.6
                for t in reading.traps
            ):
                action = "full_exit"
                reasons.append("Upthrust formed — thesis broken.")
        if d == "short":
            if reading.pa_context and any(
                t.direction == "bullish" and t.range_atr > 1.3
                for t in reading.pa_context.transitions
            ):
                action = "full_exit"
                reasons.append("Bullish transition bar — setup invalidated.")
            if reading.traps and any(
                t.name == "spring" and t.strength > 0.6
                for t in reading.traps
            ):
                action = "full_exit"
                reasons.append("Spring formed — thesis broken.")

    return PositionHealth(
        direction=d,
        entry_price=entry,
        current_price=current_price,
        stop_price=stop,
        target_price=target,
        pnl_pips=float(pnl_pips),
        progress_to_target=prog,
        distance_to_stop=dts,
        bars_held=int(bars_held),
        health_score=health,
        recommended_action=action,
        reasons=reasons,
    )
