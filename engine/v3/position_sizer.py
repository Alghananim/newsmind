# -*- coding: utf-8 -*-
"""Position sizer — calculate units from risk % + stop distance.

Formula:
    risk_amount = balance * (risk_pct / 100)
    pip_value = (pip_size * units) * (1 / quote_per_account_currency)
    units = risk_amount / (stop_distance_pips * pip_value_per_unit)

For EUR/USD account in USD:
    pip_value_per_unit = 0.0001  (for 1 unit = 0.0001 USD per pip)
    For 1000 units = $0.10 per pip
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class PositionSizeResult:
    units: int
    risk_amount: float
    stop_distance_pips: float
    pip_value_per_unit: float
    valid: bool
    reason: str = ""


def pip_size(pair: str) -> float:
    if "JPY" in pair: return 0.01
    return 0.0001


def pip_value_per_unit_in_account_currency(pair: str,
                                           account_currency: str = "USD",
                                           current_price: float = 1.0) -> float:
    """Approximate pip value per 1 unit in account currency."""
    p = pip_size(pair)
    base, quote = pair.split("/")
    if quote == account_currency:        # EUR/USD with USD account
        return p
    if base == account_currency:         # USD/JPY with USD account
        # pip value = pip_size / current_price (e.g. 0.01 / 150 ≈ 0.000067)
        return p / current_price if current_price > 0 else 0
    # Cross pair — needs conversion rate (rare in our setup)
    return p


def calculate_position_size(*, balance: float, risk_pct: float,
                            entry_price: float, stop_loss: float,
                            pair: str, account_currency: str = "USD") -> PositionSizeResult:
    if balance <= 0:
        return PositionSizeResult(units=0, risk_amount=0, stop_distance_pips=0,
                                  pip_value_per_unit=0, valid=False,
                                  reason="invalid_balance")
    if risk_pct <= 0 or risk_pct > 0.5:
        return PositionSizeResult(units=0, risk_amount=0, stop_distance_pips=0,
                                  pip_value_per_unit=0, valid=False,
                                  reason=f"risk_pct={risk_pct}_outside_safe_range_0..0.5")
    if entry_price is None or stop_loss is None:
        return PositionSizeResult(units=0, risk_amount=0, stop_distance_pips=0,
                                  pip_value_per_unit=0, valid=False,
                                  reason="missing_entry_or_stop")

    p = pip_size(pair)
    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        return PositionSizeResult(units=0, risk_amount=0, stop_distance_pips=0,
                                  pip_value_per_unit=0, valid=False,
                                  reason="zero_stop_distance")

    stop_pips = stop_distance / p
    risk_amount = balance * (risk_pct / 100.0)
    pv = pip_value_per_unit_in_account_currency(pair, account_currency, entry_price)
    if pv <= 0:
        return PositionSizeResult(units=0, risk_amount=risk_amount, stop_distance_pips=stop_pips,
                                  pip_value_per_unit=0, valid=False,
                                  reason="zero_pip_value")

    units = int(risk_amount / (stop_pips * pv))
    if units < 1:
        return PositionSizeResult(units=0, risk_amount=risk_amount, stop_distance_pips=stop_pips,
                                  pip_value_per_unit=pv, valid=False,
                                  reason=f"units_below_1:risk={risk_amount:.4f}_pip_value={pv}")

    return PositionSizeResult(units=units, risk_amount=round(risk_amount, 4),
                              stop_distance_pips=round(stop_pips, 2),
                              pip_value_per_unit=round(pv, 6),
                              valid=True,
                              reason=f"risk_{risk_pct}%_balance_{balance}_units_{units}")
