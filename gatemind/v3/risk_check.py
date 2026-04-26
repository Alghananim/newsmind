# -*- coding: utf-8 -*-
"""Risk check — stop/target/RR validation."""
from __future__ import annotations
from typing import Optional


def check(*, entry: Optional[float], stop: Optional[float],
          target: Optional[float], atr: float = 0.0) -> dict:
    if entry is None or stop is None or target is None:
        return {"status": "missing", "rr": None, "details": "stop_or_target_or_entry_missing"}
    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0 or reward <= 0:
        return {"status": "invalid", "rr": None,
                "details": f"risk={risk} reward={reward}"}
    rr = reward / risk
    if atr > 0:
        risk_atr = risk / atr
        if risk_atr < 0.3:
            return {"status": "stop_too_tight", "rr": round(rr,2),
                    "details": f"risk_atr={risk_atr:.2f}"}
        if risk_atr > 3.0:
            return {"status": "stop_too_wide", "rr": round(rr,2),
                    "details": f"risk_atr={risk_atr:.2f}"}
    if rr < 0.8:
        return {"status": "rr_too_low", "rr": round(rr,2), "details": f"rr={rr:.2f}"}
    if rr < 1.2:
        return {"status": "rr_marginal", "rr": round(rr,2), "details": f"rr={rr:.2f}"}
    return {"status": "ok", "rr": round(rr,2), "details": f"rr={rr:.2f}"}
