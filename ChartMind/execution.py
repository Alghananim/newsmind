# -*- coding: utf-8 -*-
"""Entry Precision — Phase F of the deepening plan.

Given a TradePlan from `planner`, decide HOW to enter:

    - Market order (take the spread, enter now)
    - Limit order (wait for price to come back to a better level)
    - Stop order (break of a reference level — rare for scalping)
    - Wait (no clean entry right now)

The planner answers "where would I enter if everything were perfect?".
This module answers "given current spread, current distance from the
ideal price, and the microstructure, what order do I actually send?".

Doctrine:

    Harris (Trading & Exchanges) — limit orders provide liquidity and
    save the spread but risk non-execution; market orders consume
    liquidity and guarantee fill but pay the spread. The decision hinges
    on how much the trader values fill certainty vs price improvement.

    Brooks (Reading Price Action) — a skilled scalper enters on the
    pullback, not on the breakout. Limit orders at a prior swing or at
    the signal bar's opposite edge are the default; market orders are
    reserved for rare "always-in" strong trend conditions where missing
    the move costs more than paying the spread.

    Huddleston / ICT — preferred entries are at Order Block retests, at
    the midpoint of a Fair Value Gap (CE = Consequent Encroachment), or
    at the discount/premium edge of a dealing range. The order block or
    FVG acts as the anchor; enter on retest, not on the initial
    displacement.

    López de Prado (Advances in Financial ML) — slippage is a cost that
    compounds. Every basis point saved on entry is pure alpha. Limit
    orders that fill are strictly dominant over equivalent market
    orders.

Sizing, spread cost, and microstructure all combine to select an order
type. The rules are conservative: if in doubt, WAIT rather than chase.

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
class EntryPlan:
    """Concrete order to send (or not).

    `order_type`:
        - "market"   send now, take the spread
        - "limit"    rest at `entry_price`, wait for price to come back
        - "stop"     trigger at `entry_price` on breakout
        - "wait"     no acceptable entry — do nothing this bar
    """
    order_type: str                 # "market" | "limit" | "stop" | "wait"
    entry_price: float              # the price to enter at (or trigger)
    limit_valid_for_bars: int       # how long the limit/stop is good
    slippage_budget_pips: float     # max slippage we'll tolerate
    expected_slippage_pips: float   # what we think we'll actually pay
    anchor: str                     # what the entry anchors to
    rationale: str                  # plain-text why
    confidence: float               # 0..1
    alternatives: list = field(default_factory=list)
    is_actionable: bool = True
    reason_if_not: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ExecutionContext:
    """Live market state the execution decision depends on.

    Must be passed in from the live layer — `execution` itself does
    not read the feed. Keeps this module a pure function of inputs.
    """
    current_price: float            # current mid price (or bid/ask avg)
    spread_pips: float              # current spread in pips
    atr_pips: float                 # ATR on the trade TF, in pips
    bar_range_pips: float           # last closed bar's range in pips
    pair_pip: float = 0.0001        # 0.0001 for EUR/USD


# ---------------------------------------------------------------------------
# Constants.
# ---------------------------------------------------------------------------
# Max acceptable spread for scalping EUR/USD. Above this, always WAIT.
# Source: industry convention + Harris — wide spread = dealer dominance.
_MAX_SPREAD_PIPS = 2.0

# If ideal entry is within this many pips of current price, just go Market.
# Source: Brooks — chasing by less than half the bar range is acceptable.
_MARKET_CHASE_PIPS = 1.5

# If ideal entry is further than this from current, use Limit.
# Wider than this usually means the trade is either already gone or
# premature.
_MAX_LIMIT_DISTANCE_ATR = 1.0

# Slippage budget = min(spread * 2, ATR * 0.15). Keeps execution cost
# bounded relative to both market microstructure and volatility.
_SLIPPAGE_SPREAD_MULT = 2.0
_SLIPPAGE_ATR_FRAC = 0.15

# Limit orders expire after N bars of the trade TF. Beyond this the
# setup has likely decayed.
_LIMIT_VALID_BARS_DEFAULT = 3


# ---------------------------------------------------------------------------
# Anchor selection — what point on the chart do we anchor the entry to?
# ---------------------------------------------------------------------------
def _current_price(reading) -> Optional[float]:
    """Pull the current price off a ChartReading-shaped object.

    Prefer `price` (ChartReading's field name), fall back to `close`
    for hand-rolled test doubles.
    """
    for attr in ("price", "close"):
        v = getattr(reading, attr, None)
        if v is not None:
            return float(v)
    return None


def _order_blocks(reading):
    """Return the order-block list regardless of attribute name."""
    for attr in ("order_blocks",):
        v = getattr(reading, attr, None)
        if v:
            return v
    return []


def _fvgs(reading):
    """Return the FVG list. ChartReading names it `fair_value_gaps`;
    tests may name it `fvgs`."""
    for attr in ("fair_value_gaps", "fvgs"):
        v = getattr(reading, attr, None)
        if v:
            return v
    return []


def _pa_context(reading):
    """Return the price-action context.

    ChartReading stores it as `pa_context`; tests may pass `price_action`.
    """
    for attr in ("pa_context", "price_action"):
        v = getattr(reading, attr, None)
        if v is not None:
            return v
    return None


def _ob_side(ob) -> str:
    """Read the OB direction. ChartMind.OrderBlock uses `side`; we
    also accept `kind` for test doubles."""
    for attr in ("side", "kind"):
        v = getattr(ob, attr, None)
        if v:
            return str(v)
    return ""


def _ob_mitigated(ob) -> bool:
    """True if the OB has already been tagged. ChartMind uses
    `mitigated`; tests may use `tested`."""
    for attr in ("mitigated", "tested"):
        v = getattr(ob, attr, None)
        if v is not None:
            return bool(v)
    return False


def _fvg_side(fvg) -> str:
    for attr in ("side", "kind"):
        v = getattr(fvg, attr, None)
        if v:
            return str(v)
    return ""


def _fvg_range(fvg):
    """Return (low, high). ChartMind's FairValueGap uses top/bottom;
    we also accept low/high for doubles."""
    low = getattr(fvg, "bottom", None)
    if low is None:
        low = getattr(fvg, "low", None)
    high = getattr(fvg, "top", None)
    if high is None:
        high = getattr(fvg, "high", None)
    return low, high


def _find_ob_anchor(reading, direction: str) -> Optional[tuple]:
    """Find the nearest aligned Order Block to anchor a limit entry.

    For longs, we want a bullish OB below current price that hasn't been
    mitigated. For shorts, bearish OB above.

    Source: Huddleston / ICT — OB retest is the preferred entry. Enter
    at the OB's proximal edge (the edge closest to the direction of
    travel), not at the far edge.

    Returns (price, description) or None.
    """
    obs = _order_blocks(reading)
    if not obs:
        return None

    current = _current_price(reading)
    if current is None:
        return None

    best = None
    best_dist = float("inf")
    for ob in obs:
        side = _ob_side(ob)
        low = getattr(ob, "low", None)
        high = getattr(ob, "high", None)
        if low is None or high is None:
            continue
        if _ob_mitigated(ob):
            continue
        if direction == "long" and side == "bullish" and high < current:
            # Proximal edge for longs = top of OB (closest to current).
            dist = current - high
            if dist < best_dist:
                best_dist = dist
                best = (high, f"bullish OB top @ {high:.5f}")
        elif direction == "short" and side == "bearish" and low > current:
            # Proximal edge for shorts = bottom of OB.
            dist = low - current
            if dist < best_dist:
                best_dist = dist
                best = (low, f"bearish OB bottom @ {low:.5f}")
    return best


def _find_fvg_anchor(reading, direction: str) -> Optional[tuple]:
    """Find the nearest aligned Fair Value Gap midpoint (CE).

    Source: Huddleston / ICT — Consequent Encroachment = the 50% of the
    FVG. Entering at CE is a higher-probability fill than waiting for
    full fill.

    Returns (price, description) or None.
    """
    fvgs = _fvgs(reading)
    if not fvgs:
        return None

    current = _current_price(reading)
    if current is None:
        return None

    best = None
    best_dist = float("inf")
    for fvg in fvgs:
        side = _fvg_side(fvg)
        low, high = _fvg_range(fvg)
        filled = bool(getattr(fvg, "filled", False))
        if low is None or high is None:
            continue
        if filled:
            continue
        mid = (low + high) / 2.0
        if direction == "long" and side == "bullish" and mid < current:
            dist = current - mid
            if dist < best_dist:
                best_dist = dist
                best = (mid, f"bullish FVG CE @ {mid:.5f}")
        elif direction == "short" and side == "bearish" and mid > current:
            dist = mid - current
            if dist < best_dist:
                best_dist = dist
                best = (mid, f"bearish FVG CE @ {mid:.5f}")
    return best


def _find_signal_bar_anchor(reading, plan) -> Optional[tuple]:
    """For signal_entry setups, anchor on the entry-bar's trigger price.

    ChartMind's PriceActionContext stores signal bars and their
    corresponding entry bars. The entry bar knows the price at which
    the signal was confirmed (`entry_price`). That is the anchor —
    a limit retest at that level on a pullback is the textbook
    Brooks continuation entry.

    Source: Brooks — reversal bar entry triggers on the bar after the
    signal; limit at the signal-bar extreme on pullback is the
    conservative variant.
    """
    pa = _pa_context(reading)
    if pa is None:
        return None
    entry_bars = getattr(pa, "entry_bars", None) or []
    if not entry_bars:
        return None
    eb = entry_bars[-1]
    price = getattr(eb, "entry_price", None)
    direction = getattr(eb, "direction", None)
    if price is None or direction is None:
        return None
    tag = "long" if direction == "bullish" else "short"
    return (float(price), f"{tag} signal→entry @ {float(price):.5f}")


def _find_pattern_anchor(reading, plan) -> Optional[tuple]:
    """For chart-pattern setups, use the planner's entry price directly.

    Chart patterns (double top, H&S, triangles) have a single breakout
    line — the planner already picked it. The anchor is just the plan's
    entry_price, but ONLY when setup_type indicates a chart pattern.
    For other setup types (signal_entry, two_legged_pullback, trap),
    the pattern anchor is not applicable and returns None.
    """
    setup = getattr(plan, "setup_type", "") or ""
    if "pattern" not in setup.lower():
        return None
    if plan.entry_price and plan.entry_price > 0:
        return (plan.entry_price, f"pattern breakout @ {plan.entry_price:.5f}")
    return None


# ---------------------------------------------------------------------------
# Slippage budget.
# ---------------------------------------------------------------------------
def _slippage_budget(exec_ctx: ExecutionContext) -> float:
    """Max slippage we'll accept, in pips.

    Source: industry convention. Two bounds: never more than 2x spread
    (respect microstructure) and never more than 15% of ATR (respect
    volatility). Take the min — the tighter rule wins.
    """
    spread_bound = exec_ctx.spread_pips * _SLIPPAGE_SPREAD_MULT
    atr_bound = exec_ctx.atr_pips * _SLIPPAGE_ATR_FRAC
    return max(0.3, min(spread_bound, atr_bound))


def _expected_slippage(order_type: str, exec_ctx: ExecutionContext) -> float:
    """Point estimate of what we'll actually pay.

    Market orders pay ~half spread to cross on normal liquidity, plus a
    small impact term. Limit orders pay nothing if filled (but may not
    fill). Stop orders pay similar to market, possibly worse on gaps.
    """
    if order_type == "market":
        return exec_ctx.spread_pips * 0.6 + 0.2
    if order_type == "limit":
        return 0.0
    if order_type == "stop":
        return exec_ctx.spread_pips * 0.75 + 0.3
    return 0.0


# ---------------------------------------------------------------------------
# Main decision.
# ---------------------------------------------------------------------------
def decide_entry(reading, plan, exec_ctx: ExecutionContext) -> EntryPlan:
    """Select the optimal order type and price for a given TradePlan.

    Decision tree (priority order):

        1. Guard: if spread > _MAX_SPREAD_PIPS → WAIT.
        2. Guard: if plan.is_actionable is False → WAIT.
        3. Build anchor candidates (OB, FVG, signal bar, pattern level).
        4. Pick the best aligned anchor closest to current.
        5. If no aligned anchor and the plan's entry is within
           _MARKET_CHASE_PIPS of current → MARKET.
        6. If aligned anchor exists at a better price than current
           (pullback direction) → LIMIT at anchor.
        7. If aligned anchor is further than _MAX_LIMIT_DISTANCE_ATR
           from current → WAIT (setup premature).
        8. Otherwise fall back to LIMIT at the plan.entry_price.

    All decisions are logged in `rationale` and `alternatives`.
    """
    # Guard 1: unacceptable spread.
    if exec_ctx.spread_pips > _MAX_SPREAD_PIPS:
        return EntryPlan(
            order_type="wait",
            entry_price=0.0,
            limit_valid_for_bars=0,
            slippage_budget_pips=0.0,
            expected_slippage_pips=0.0,
            anchor="none",
            rationale=(
                f"Spread {exec_ctx.spread_pips:.1f} pips exceeds max "
                f"{_MAX_SPREAD_PIPS} — refuse to trade into a wide market."
            ),
            confidence=0.0,
            is_actionable=False,
            reason_if_not="wide_spread",
        )

    # Guard 2: plan itself refused.
    if not plan.is_actionable:
        return EntryPlan(
            order_type="wait",
            entry_price=0.0,
            limit_valid_for_bars=0,
            slippage_budget_pips=0.0,
            expected_slippage_pips=0.0,
            anchor="none",
            rationale=f"Upstream plan not actionable: {plan.reason_if_not}",
            confidence=0.0,
            is_actionable=False,
            reason_if_not=plan.reason_if_not or "plan_not_actionable",
        )

    direction = plan.direction
    current = exec_ctx.current_price
    pip = exec_ctx.pair_pip

    # Collect candidate anchors. Each is (price, description).
    candidates = []
    ob = _find_ob_anchor(reading, direction)
    if ob:
        candidates.append(("order_block", ob[0], ob[1]))
    fvg = _find_fvg_anchor(reading, direction)
    if fvg:
        candidates.append(("fvg_ce", fvg[0], fvg[1]))
    sig = _find_signal_bar_anchor(reading, plan)
    if sig:
        candidates.append(("signal_bar", sig[0], sig[1]))
    pat = _find_pattern_anchor(reading, plan)
    if pat:
        candidates.append(("pattern_level", pat[0], pat[1]))

    # Rank candidates: closest to current that is still on the *correct*
    # side for a limit (below for long, above for short).
    def _is_pullback(price):
        return (direction == "long" and price <= current) or (
            direction == "short" and price >= current
        )

    pullback_candidates = [c for c in candidates if _is_pullback(c[1])]

    alternatives = [
        f"{kind}: {desc}" for (kind, _, desc) in candidates
    ]

    budget = _slippage_budget(exec_ctx)

    # Case: we have a clean pullback anchor.
    if pullback_candidates:
        # Pick the anchor closest to current (most likely to fill first).
        pullback_candidates.sort(
            key=lambda c: abs(c[1] - current)
        )
        best_kind, best_price, best_desc = pullback_candidates[0]
        dist_pips = abs(best_price - current) / pip

        # Too far? Wait rather than chase an old level.
        if exec_ctx.atr_pips > 0:
            max_dist_pips = _MAX_LIMIT_DISTANCE_ATR * exec_ctx.atr_pips
            if dist_pips > max_dist_pips:
                return EntryPlan(
                    order_type="wait",
                    entry_price=best_price,
                    limit_valid_for_bars=0,
                    slippage_budget_pips=budget,
                    expected_slippage_pips=0.0,
                    anchor=best_kind,
                    rationale=(
                        f"Best anchor ({best_desc}) is {dist_pips:.1f} pips "
                        f"away — beyond {max_dist_pips:.1f} pip limit. "
                        "Setup premature; wait for price to approach."
                    ),
                    confidence=0.4,
                    alternatives=alternatives,
                    is_actionable=False,
                    reason_if_not="anchor_too_far",
                )

        # Close enough to just chase? (dist < _MARKET_CHASE_PIPS)
        if dist_pips < _MARKET_CHASE_PIPS:
            return EntryPlan(
                order_type="market",
                entry_price=current,
                limit_valid_for_bars=0,
                slippage_budget_pips=budget,
                expected_slippage_pips=_expected_slippage("market", exec_ctx),
                anchor=best_kind,
                rationale=(
                    f"Anchor ({best_desc}) within {_MARKET_CHASE_PIPS} pips "
                    f"of current ({dist_pips:.1f} pips) — market entry "
                    "cheaper than waiting for a limit that may not fill."
                ),
                confidence=min(plan.confidence + 0.05, 1.0),
                alternatives=alternatives,
                is_actionable=True,
            )

        # Normal case: limit at the anchor.
        return EntryPlan(
            order_type="limit",
            entry_price=best_price,
            limit_valid_for_bars=_LIMIT_VALID_BARS_DEFAULT,
            slippage_budget_pips=budget,
            expected_slippage_pips=_expected_slippage("limit", exec_ctx),
            anchor=best_kind,
            rationale=(
                f"Limit at {best_desc} — {dist_pips:.1f} pips of improvement "
                f"vs market. Valid for {_LIMIT_VALID_BARS_DEFAULT} bars."
            ),
            confidence=plan.confidence,
            alternatives=alternatives,
            is_actionable=True,
        )

    # Case: no pullback anchor. If plan.entry_price is close to current,
    # take market. Otherwise wait.
    dist_to_plan = abs(plan.entry_price - current) / pip
    if dist_to_plan < _MARKET_CHASE_PIPS:
        return EntryPlan(
            order_type="market",
            entry_price=current,
            limit_valid_for_bars=0,
            slippage_budget_pips=budget,
            expected_slippage_pips=_expected_slippage("market", exec_ctx),
            anchor="current",
            rationale=(
                f"No pullback anchor available; plan entry "
                f"({plan.entry_price:.5f}) within "
                f"{_MARKET_CHASE_PIPS} pips — take market."
            ),
            confidence=max(plan.confidence - 0.05, 0.0),
            alternatives=alternatives,
            is_actionable=True,
        )

    # All else fails: wait. This is the conservative default and will
    # trigger often on chop — that is intentional.
    return EntryPlan(
        order_type="wait",
        entry_price=plan.entry_price,
        limit_valid_for_bars=0,
        slippage_budget_pips=budget,
        expected_slippage_pips=0.0,
        anchor="none",
        rationale=(
            "No aligned anchor and plan entry is too far from current "
            f"({dist_to_plan:.1f} pips). No clean entry — wait."
        ),
        confidence=0.0,
        alternatives=alternatives,
        is_actionable=False,
        reason_if_not="no_clean_entry",
    )


# ---------------------------------------------------------------------------
# Helper: convert price distance to pips for a pair.
# ---------------------------------------------------------------------------
def price_to_pips(delta_price: float, pair_pip: float = 0.0001) -> float:
    """Convert a price delta into pips. JPY pairs use 0.01 for pair_pip."""
    if pair_pip <= 0:
        return 0.0
    return abs(delta_price) / pair_pip
