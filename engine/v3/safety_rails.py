# -*- coding: utf-8 -*-
"""Safety rails — final hard-stop checks BEFORE order submission.

These run AFTER GateMind says "enter" — they're the absolute last line.
Each check returns (allowed: bool, reason: str).
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional
from .validation_config import ValidationConfig
from .position_sizer import PositionSizeResult


def check_all(*, gate_decision_result, position_size: PositionSizeResult,
              cfg: ValidationConfig, account_balance: float,
              daily_loss_pct: float, consecutive_losses: int,
              trades_today: int, smartnotebook_writable: bool,
              spread_pips: float, slippage_pips: float, pair: str,
              broker_mode: str) -> tuple:
    """Return (ok: bool, blocking_reasons: list)."""
    blocks = []

    # 1. Gate must say enter
    if gate_decision_result is None or gate_decision_result.final_decision != "enter":
        blocks.append(f"gate_decision_not_enter:{gate_decision_result.final_decision if gate_decision_result else 'none'}")

    # 2. Position size must be valid
    if not position_size.valid:
        blocks.append(f"position_size_invalid:{position_size.reason}")
    if position_size.units < 1:
        blocks.append(f"units_below_1:{position_size.units}")

    # 3. Risk must not exceed cap
    actual_risk_pct = (position_size.risk_amount / account_balance * 100
                       if account_balance > 0 else 999)
    if actual_risk_pct > cfg.max_risk_pct_per_trade + 0.01:    # tiny float tolerance
        blocks.append(f"risk_exceeds_cap:{actual_risk_pct:.3f}>{cfg.max_risk_pct_per_trade}")

    # 4. Daily loss limit
    if daily_loss_pct >= cfg.daily_loss_limit_pct:
        blocks.append(f"daily_loss_limit_hit:{daily_loss_pct:.2f}>={cfg.daily_loss_limit_pct}")

    # 5. Consecutive losses limit
    if consecutive_losses >= cfg.consecutive_losses_limit:
        blocks.append(f"consecutive_losses_limit:{consecutive_losses}>={cfg.consecutive_losses_limit}")

    # 6. Daily trade limit
    if trades_today >= cfg.daily_trade_limit:
        blocks.append(f"daily_trade_limit:{trades_today}>={cfg.daily_trade_limit}")

    # 7. SmartNoteBook must be writable (no trade if logging broken)
    if not smartnotebook_writable:
        blocks.append("smartnotebook_not_writable_no_trade_without_logging")

    # 8. Spread within limit
    max_sp = cfg.max_spread_pips.get(pair, 2.0)
    if spread_pips > max_sp:
        blocks.append(f"spread_too_high:{spread_pips}>{max_sp}")

    # 9. Slippage estimate within limit
    if slippage_pips > cfg.max_slippage_pips:
        blocks.append(f"slippage_too_high:{slippage_pips}>{cfg.max_slippage_pips}")

    # 10. Pair must be in production status
    pair_status = cfg.pair_status.get(pair, "unknown")
    if pair_status == "disabled":
        blocks.append(f"pair_disabled:{pair}")
    if pair_status == "monitoring" and broker_mode == "live":
        blocks.append(f"monitoring_pair_in_live:{pair}")
    if pair_status == "unknown":
        blocks.append(f"pair_unknown_status:{pair}")

    # 11. Broker env validation
    if broker_mode not in ("practice", "live"):
        blocks.append(f"broker_env_invalid:{broker_mode}")

    # 12. Account balance sanity
    if account_balance is None or account_balance <= 0:
        blocks.append(f"account_balance_invalid:{account_balance}")

    return (len(blocks) == 0, blocks)
