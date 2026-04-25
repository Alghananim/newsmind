# -*- coding: utf-8 -*-
"""Position sizing — converting a TradePlan to lot size.

The single most underrated part of a trading system. As Tom Basso says
in Schwager's Market Wizards: "The most important thing is to never
risk more than 1% of your equity on any single trade. After that, the
strategy is almost a footnote."

We implement three sizing methods and pick between them via config:

    1. Fixed-fractional (default)
       ----------------------------
       Risk a fixed fraction of current equity per trade. The
       canonical reference is Vince's "Mathematics of Money
       Management" (1990). Formula:

           lot = (equity * risk_pct) / (stop_distance_in_pip_value)

       Pros: simple, robust, never blows up.
       Cons: shrinks position after a loss (hurts recovery).

    2. Fixed-R (Van Tharp's "1R = 1 unit of risk")
       --------------------------------------------
       Same formula as fixed-fractional, but expressed in "R" units.
       Useful for backtest reports: every trade's outcome becomes a
       multiple of R, independent of currency. Reference: Van Tharp,
       "Trade Your Way to Financial Freedom" (1998).

    3. Quarter-Kelly
       --------------
       Kelly criterion (Kelly 1956, Thorp 1962) gives the equity
       fraction that maximises long-run growth:

           f* = (p * b - q) / b

       where p is win probability, q = 1 - p, b is win/loss ratio.
       Full Kelly is too aggressive — modest probability errors blow
       up the account. Edward Thorp recommends 0.25*Kelly for real-
       world use; we follow that. The required p comes from the
       brain's combined_confidence (after calibration); b comes from
       the plan's R:R ratio.

Risk-of-ruin guard
------------------
After computing lot size, we enforce two hard ceilings (Tharp p.79):

    * Lot must not require more than `max_margin_pct` of equity.
    * Lot must not produce a worst-case loss > `max_loss_pct` of equity
      even if stop slips by 2x its planned distance.

Both ceilings cap the lot, never zero it out (a smaller-than-ideal
trade is still a trade).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------
# Config + outputs.
# --------------------------------------------------------------------
@dataclass
class RiskConfig:
    """Account- and pair-level risk parameters.

    All percentages are 0..1 fractions, not 0..100 numbers. Mansur
    edits these once; the system never edits them mid-run.
    """
    method: str = "fixed_fractional"   # | "fixed_r" | "quarter_kelly"
    risk_pct: float = 0.01             # 1% of equity per trade
    max_loss_pct: float = 0.02         # cap worst-case loss at 2% / trade
    max_margin_pct: float = 0.20       # never use > 20% of equity as margin
    min_lot: float = 0.01              # broker minimum (OANDA: 1 micro lot)
    lot_step: float = 0.01             # round to nearest 0.01 lot
    pair_pip: float = 0.0001           # 0.0001 EUR/USD; 0.01 JPY pairs
    pip_value_per_lot: float = 10.0    # USD per pip per standard lot (EUR/USD)
    max_lot: float = 50.0              # absolute upper bound for sanity


@dataclass
class SizedTrade:
    """Result of sizing a single trade.

    `lot`               final lot size (broker units)
    `risk_amount`       expected loss if stop fills exactly = currency units
    `risk_pct`          risk_amount / equity (0..1)
    `worst_case_loss`   loss if stop slips 2x = currency units
    `worst_case_pct`    worst_case_loss / equity
    `margin_required`   approx margin (uses broker leverage)
    `method_used`       which sizing method actually produced this lot
    `caps_applied`      list of caps that bound the lot, e.g. ["max_loss"]
    `notes`             plain-text trace
    """
    lot: float
    risk_amount: float
    risk_pct: float
    worst_case_loss: float
    worst_case_pct: float
    margin_required: float
    method_used: str
    caps_applied: list[str]
    notes: str


# --------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------
def _round_to_step(lot: float, step: float, lo: float, hi: float) -> float:
    """Round to nearest step, then clip to [lo, hi]."""
    if step <= 0:
        return max(lo, min(hi, lot))
    rounded = round(lot / step) * step
    return float(max(lo, min(hi, rounded)))


def _pip_value(lot: float, pip_value_per_lot: float) -> float:
    """Currency value of one pip move at this lot size."""
    return lot * pip_value_per_lot


def _stop_distance_pips(entry: float, stop: float, pair_pip: float) -> float:
    if pair_pip <= 0:
        return 0.0
    return abs(entry - stop) / pair_pip


# --------------------------------------------------------------------
# Sizing methods.
# --------------------------------------------------------------------
def _size_fixed_fractional(
    equity: float, stop_pips: float, cfg: RiskConfig
) -> tuple[float, str]:
    """Vince fixed-fractional. Returns (lot, note)."""
    if stop_pips <= 0 or equity <= 0:
        return 0.0, "fixed_fractional: invalid inputs (zero stop or equity)"
    risk_amount = equity * cfg.risk_pct
    pip_value_at_one_lot = cfg.pip_value_per_lot
    if pip_value_at_one_lot <= 0:
        return 0.0, "fixed_fractional: invalid pip_value_per_lot"
    lot = risk_amount / (stop_pips * pip_value_at_one_lot)
    note = (
        f"fixed_fractional: risk={cfg.risk_pct:.1%} of {equity:.2f} = "
        f"{risk_amount:.2f}; stop={stop_pips:.1f} pips; lot={lot:.4f}"
    )
    return lot, note


def _size_fixed_r(
    equity: float, stop_pips: float, cfg: RiskConfig
) -> tuple[float, str]:
    """Van Tharp R-units. Identical math to fixed-fractional but the
    note documents the R-unit interpretation explicitly. Kept separate
    because backtest reports often pivot on this label.
    """
    lot, _ = _size_fixed_fractional(equity, stop_pips, cfg)
    note = (
        f"fixed_r: 1R = {cfg.risk_pct:.1%} of equity; "
        f"position sized so stop = -1R; lot={lot:.4f}"
    )
    return lot, note


def _size_quarter_kelly(
    equity: float, stop_pips: float, target_pips: float,
    win_prob: float, cfg: RiskConfig,
) -> tuple[float, str]:
    """Quarter-Kelly: 0.25 * Kelly fraction.

    f* = (p*b - q) / b, then we use 0.25*f*. Win prob must come from a
    *calibrated* probability — the calibrated_confidence layer in the
    feeder brain. b = target/stop in pip terms (the R:R ratio).
    """
    if stop_pips <= 0 or target_pips <= 0 or equity <= 0:
        return 0.0, "quarter_kelly: invalid inputs"
    p = max(0.0, min(1.0, float(win_prob)))
    q = 1.0 - p
    b = target_pips / stop_pips  # win/loss payoff ratio
    if b <= 0:
        return 0.0, "quarter_kelly: non-positive payoff ratio"
    full_kelly = (p * b - q) / b
    if full_kelly <= 0:
        return 0.0, (
            f"quarter_kelly: negative edge (p={p:.2f}, b={b:.2f}); "
            "do not trade"
        )
    target_fraction = 0.25 * full_kelly
    risk_amount = equity * target_fraction
    pip_value_at_one_lot = cfg.pip_value_per_lot
    lot = risk_amount / (stop_pips * pip_value_at_one_lot)
    note = (
        f"quarter_kelly: full_kelly={full_kelly:.3f}, "
        f"used={target_fraction:.3f} of equity; lot={lot:.4f}"
    )
    return lot, note


# --------------------------------------------------------------------
# Public entry point.
# --------------------------------------------------------------------
def size_trade(
    *,
    equity: float,
    entry_price: float,
    stop_price: float,
    target_price: Optional[float] = None,
    win_probability: Optional[float] = None,
    cfg: Optional[RiskConfig] = None,
) -> SizedTrade:
    """Compute a SizedTrade for a single TradePlan.

    Required:
        equity, entry_price, stop_price.
    Optional:
        target_price, win_probability — needed only for quarter_kelly.
    """
    if cfg is None:
        cfg = RiskConfig()

    stop_pips = _stop_distance_pips(entry_price, stop_price, cfg.pair_pip)
    target_pips = (
        _stop_distance_pips(entry_price, target_price, cfg.pair_pip)
        if target_price is not None else 0.0
    )

    method = cfg.method.lower()
    if method == "quarter_kelly":
        if win_probability is None or target_pips <= 0:
            # Fall back gracefully rather than refuse.
            method = "fixed_fractional"

    if method == "quarter_kelly":
        raw_lot, note = _size_quarter_kelly(
            equity, stop_pips, target_pips, win_probability or 0.0, cfg,
        )
    elif method == "fixed_r":
        raw_lot, note = _size_fixed_r(equity, stop_pips, cfg)
    else:
        raw_lot, note = _size_fixed_fractional(equity, stop_pips, cfg)
        method = "fixed_fractional"

    caps_applied: list[str] = []

    # --- Hard ceiling: max_loss_pct (Tharp risk-of-ruin guard) -----
    # We test "what if the stop slips by 2x?" — this is conservative
    # but matches Tharp's recommendation for retail FX.
    max_lot_by_loss = (
        equity * cfg.max_loss_pct / (stop_pips * cfg.pip_value_per_lot * 2.0)
        if stop_pips > 0 and cfg.pip_value_per_lot > 0
        else cfg.max_lot
    )
    if raw_lot > max_lot_by_loss:
        caps_applied.append(
            f"max_loss_pct ({cfg.max_loss_pct:.1%}, 2x slippage budget)"
        )
        raw_lot = max_lot_by_loss

    # --- Hard ceiling: max_margin_pct ------------------------------
    # Heuristic: 1 standard lot of EUR/USD ~= $1000 margin at 100:1.
    # We don't pretend to know the exact broker rule; we use a
    # conservative proxy. The execution layer rechecks this against
    # the broker's actual margin response and aborts if needed.
    margin_per_lot = 1000.0  # USD margin per standard lot @ 100:1 leverage
    max_lot_by_margin = (
        equity * cfg.max_margin_pct / margin_per_lot
        if margin_per_lot > 0 else cfg.max_lot
    )
    if raw_lot > max_lot_by_margin:
        caps_applied.append(
            f"max_margin_pct ({cfg.max_margin_pct:.1%})"
        )
        raw_lot = max_lot_by_margin

    # --- Absolute clamp + step rounding ----------------------------
    if raw_lot > cfg.max_lot:
        caps_applied.append(f"absolute max_lot ({cfg.max_lot})")
        raw_lot = cfg.max_lot
    final_lot = _round_to_step(raw_lot, cfg.lot_step, cfg.min_lot, cfg.max_lot)
    if final_lot != raw_lot and abs(raw_lot - final_lot) > 1e-9:
        caps_applied.append(f"step={cfg.lot_step}")

    # If we rounded down to zero (e.g. equity too small), we still
    # return min_lot so the system doesn't silently emit a zero-trade.
    # The caller is expected to inspect risk_pct and decide.
    if final_lot < cfg.min_lot:
        final_lot = cfg.min_lot

    # --- Reporting -------------------------------------------------
    expected_loss = final_lot * stop_pips * cfg.pip_value_per_lot
    risk_pct = expected_loss / equity if equity > 0 else 0.0
    worst_case_loss = expected_loss * 2.0          # 2x slippage buffer
    worst_case_pct = worst_case_loss / equity if equity > 0 else 0.0
    margin_required = final_lot * margin_per_lot

    notes = note
    if caps_applied:
        notes += "  |  caps: " + ", ".join(caps_applied)

    return SizedTrade(
        lot=float(final_lot),
        risk_amount=float(expected_loss),
        risk_pct=float(risk_pct),
        worst_case_loss=float(worst_case_loss),
        worst_case_pct=float(worst_case_pct),
        margin_required=float(margin_required),
        method_used=method,
        caps_applied=caps_applied,
        notes=notes,
    )


# --------------------------------------------------------------------
# Helpers for callers — sometimes useful to know R-multiples directly.
# --------------------------------------------------------------------
def to_r_multiple(pnl_currency: float, risk_amount: float) -> float:
    """Convert a realised P&L to R-units. Useful for backtest stats."""
    if risk_amount <= 0:
        return 0.0
    return pnl_currency / risk_amount


def expectancy_r(win_rate: float, avg_win_r: float, avg_loss_r: float) -> float:
    """Tharp expectancy in R units.

    expectancy = win_rate*avg_win_r - (1-win_rate)*avg_loss_r

    Rule of thumb: a system with expectancy < 0.25R is fragile;
    < 0.5R needs many trades to converge; > 1R is rare and worth
    protecting.
    """
    if not 0.0 <= win_rate <= 1.0:
        return 0.0
    return win_rate * avg_win_r - (1.0 - win_rate) * abs(avg_loss_r)
