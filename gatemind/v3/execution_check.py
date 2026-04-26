# -*- coding: utf-8 -*-
"""Execution / broker safety check.

PAIR_STATUS enforced strictly:
   production → live trading allowed
   monitoring → paper-only (NEVER live)
   disabled   → never any trading
"""
from __future__ import annotations
from typing import Optional


# Single source of truth — synced with main.py PAIR_STATUS
PAIR_STATUS = {
    "EUR/USD": "production",
    "USD/JPY": "monitoring",
    "GBP/USD": "disabled",
}


def check(*, pair: str, broker_mode: str, live_enabled: bool,
          spread_pips: float, max_spread_pips: float,
          slippage_pips: float, max_slippage_pips: float) -> dict:
    pair_status = PAIR_STATUS.get(pair, "unknown")
    if pair_status == "disabled":
        return {"status": "disabled_pair", "pair_status": "disabled",
                "details": f"pair_{pair}_disabled"}
    if pair_status == "unknown":
        return {"status": "unknown_pair", "pair_status": "unknown",
                "details": f"pair_{pair}_not_in_PAIR_STATUS"}

    # Broker mode validation
    if broker_mode not in ("paper", "live", "sandbox"):
        return {"status": "broker_unsafe", "pair_status": pair_status,
                "details": f"broker_mode_unknown:{broker_mode}"}

    # monitoring: live mode is hard-blocked
    if pair_status == "monitoring" and broker_mode == "live" and live_enabled:
        return {"status": "monitoring_pair_live_blocked",
                "pair_status": "monitoring",
                "details": f"pair_monitoring_cannot_go_live"}

    # spread
    if spread_pips is None or spread_pips < 0:
        return {"status": "spread_unknown", "pair_status": pair_status,
                "details": "spread_invalid"}
    if spread_pips > max_spread_pips:
        return {"status": "spread_too_wide", "pair_status": pair_status,
                "details": f"spread={spread_pips:.2f}>max={max_spread_pips:.2f}"}

    # slippage
    if slippage_pips is None or slippage_pips < 0:
        return {"status": "slippage_unknown", "pair_status": pair_status,
                "details": "slippage_invalid"}
    if slippage_pips > max_slippage_pips:
        return {"status": "slippage_too_high", "pair_status": pair_status,
                "details": f"slippage={slippage_pips:.2f}>max={max_slippage_pips:.2f}"}

    return {"status": "ok", "pair_status": pair_status,
            "details": f"pair={pair_status} broker={broker_mode} spread={spread_pips:.2f}"}
