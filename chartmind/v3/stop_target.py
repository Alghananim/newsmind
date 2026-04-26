# -*- coding: utf-8 -*-
"""Stop loss + take profit + Risk/Reward (Murphy + Brooks).

LONG:
  stop = swing_low - 0.3 * ATR  (or entry - 1.0 * ATR if no swing)
  target = nearest_resistance (or entry + 2.0 * ATR if none)

SHORT: mirror.

Safety:
  stop_distance < 0.3 ATR  → too tight → block
  stop_distance > 3.0 ATR  → too wide  → block
  R/R < 0.8                → block
"""
from __future__ import annotations
from typing import List, Optional
from .models import Bar


def compute(*, bars: List[Bar], atr: float, direction: str,
            supports: list, resistances: list) -> dict:
    """Return dict with stop/target/RR + status."""
    if not bars or atr <= 0 or direction not in ("bullish","bearish"):
        return {"stop": None, "target": None, "rr": None,
                "stop_logic": "n_a", "target_logic": "n_a",
                "status": "no_direction"}

    entry = bars[-1].close
    last_swing_low = min((b.low for b in bars[-15:]), default=entry)
    last_swing_high = max((b.high for b in bars[-15:]), default=entry)

    if direction == "bullish":
        # Stop behind nearest support (or below recent swing low)
        relevant_supports = [s.price for s in supports if s.price < entry]
        if relevant_supports:
            stop = max(relevant_supports) - 0.3 * atr
            stop_logic = "behind_swing_support"
        else:
            stop = last_swing_low - 0.3 * atr
            stop_logic = "behind_recent_low"

        # Target = next resistance up
        relevant_res = sorted([r.price for r in resistances if r.price > entry])
        if relevant_res:
            target = relevant_res[0]
            target_logic = "to_next_resistance"
        else:
            target = entry + 2.0 * atr
            target_logic = "atr_2x_no_resistance"

    else:    # bearish
        relevant_res = [r.price for r in resistances if r.price > entry]
        if relevant_res:
            stop = min(relevant_res) + 0.3 * atr
            stop_logic = "behind_swing_resistance"
        else:
            stop = last_swing_high + 0.3 * atr
            stop_logic = "behind_recent_high"

        relevant_sup = sorted([s.price for s in supports if s.price < entry], reverse=True)
        if relevant_sup:
            target = relevant_sup[0]
            target_logic = "to_next_support"
        else:
            target = entry - 2.0 * atr
            target_logic = "atr_2x_no_support"

    if direction == "bullish":
        risk = entry - stop
        reward = target - entry
    else:
        risk = stop - entry
        reward = entry - target

    if risk <= 0:
        return {"stop": stop, "target": target, "rr": None,
                "stop_logic": stop_logic, "target_logic": target_logic,
                "status": "invalid_risk"}

    rr = reward / risk

    # Status
    risk_atr = risk / atr
    if risk_atr < 0.3:
        status = "stop_too_tight"
    elif risk_atr > 3.0:
        status = "stop_too_wide"
    elif rr < 0.8:
        status = "rr_too_low"
    elif rr < 1.2:
        status = "rr_marginal"
    else:
        status = "ok"

    return {"stop": round(stop, 5), "target": round(target, 5),
            "rr": round(rr, 2),
            "stop_logic": stop_logic, "target_logic": target_logic,
            "status": status, "risk_atr": round(risk_atr, 2)}
