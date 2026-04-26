# -*- coding: utf-8 -*-
"""System state check — open positions, daily limits, cooldowns, drawdown."""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from .models import SystemState


def check(state: Optional[SystemState], now_utc: datetime) -> dict:
    if state is None:
        return {"position_state": "missing", "daily_limits": "missing",
                "details": "no_system_state"}

    # Daily loss limit
    if state.daily_loss_pct >= state.daily_loss_limit_pct:
        return {"position_state": "blocked_by_loss_limit",
                "daily_limits": "at_loss_limit",
                "details": f"daily_loss={state.daily_loss_pct:.2f}%>={state.daily_loss_limit_pct:.2f}%"}

    # Daily trade count
    if state.trades_today >= state.daily_trade_limit:
        return {"position_state": "blocked_by_trade_limit",
                "daily_limits": "at_trade_limit",
                "details": f"trades_today={state.trades_today}>={state.daily_trade_limit}"}

    # Cooldown after consecutive losses
    if state.consecutive_losses >= 3:
        if state.cooldown_until_utc and now_utc < state.cooldown_until_utc:
            return {"position_state": "in_cooldown",
                    "daily_limits": "ok",
                    "details": f"cooldown_after_{state.consecutive_losses}_losses_until_{state.cooldown_until_utc.isoformat()}"}
        return {"position_state": "after_3_losses_cooldown",
                "daily_limits": "ok",
                "details": f"3+_consecutive_losses_no_cooldown_set"}

    # Open positions / pending orders for this pair
    for p, side, size in state.open_positions:
        if p == state.pair:
            return {"position_state": "position_already_open",
                    "daily_limits": "ok",
                    "details": f"open_position_{p}_{side}_{size}"}
    for p, side, size in state.pending_orders:
        if p == state.pair:
            return {"position_state": "pending_order_exists",
                    "daily_limits": "ok",
                    "details": f"pending_{p}_{side}_{size}"}

    # Latency check
    if state.data_latency_ms > state.max_data_latency_ms:
        return {"position_state": "data_stale",
                "daily_limits": "ok",
                "details": f"latency={state.data_latency_ms}ms>{state.max_data_latency_ms}ms"}

    return {"position_state": "flat", "daily_limits": "ok",
            "details": "no_blocking_state"}
