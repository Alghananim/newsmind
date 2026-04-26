# -*- coding: utf-8 -*-
"""Entry quality assessment.

EXCELLENT: candle close in lower half (long) / upper half (short) of bar,
           AT a structural level, with room ≥ 1.5 ATR to nearest opposing level.
GOOD:      similar but room only 1.0-1.5 ATR.
MARGINAL:  body location middle, room < 1.0 ATR.
LATE:      candle body fills > 50% of recent move OR bar > 1.5×ATR.
CHASE:     last 3 bars one-way + body extends + close near extreme.
NO_SETUP:  doesn't fit any pattern.
"""
from __future__ import annotations
from typing import List, Optional
from .models import Bar


def assess(bars: List[Bar], atr: float, *,
           direction: str = "bullish",
           nearest_opposing_distance: Optional[float] = None) -> dict:
    """Returns {"quality", "late_entry_risk", "details"}."""
    if not bars or len(bars) < 6 or atr <= 0:
        return {"quality": "no_setup", "late_entry_risk": False,
                "details": "insufficient"}

    cur = bars[-1]
    rng = cur.high - cur.low
    if rng == 0:
        return {"quality": "no_setup", "late_entry_risk": False,
                "details": "zero_range"}

    body = abs(cur.close - cur.open)
    body_loc = (cur.close - cur.low) / rng    # 0 = at low, 1 = at high
    bar_size_atr = rng / atr

    # Late entry checks
    last_5_move = abs(cur.close - bars[-6].close) if len(bars) >= 6 else 0
    late_due_to_move = atr > 0 and last_5_move > 1.5 * atr
    late_due_to_bar = bar_size_atr > 1.5

    # Chase detection: 3 bars one-way + close at extreme
    chase = False
    if len(bars) >= 4:
        last_3 = bars[-3:]
        all_bull = all(b.close > b.open for b in last_3)
        all_bear = all(b.close < b.open for b in last_3)
        close_at_extreme = (body_loc > 0.85) if direction == "bullish" else (body_loc < 0.15)
        if (all_bull and direction == "bullish" and close_at_extreme) or \
           (all_bear and direction == "bearish" and close_at_extreme):
            chase = True

    if chase:
        return {"quality": "chase", "late_entry_risk": True,
                "details": f"3_bars_one_way close_loc={body_loc:.2f}"}

    if late_due_to_move or late_due_to_bar:
        return {"quality": "late", "late_entry_risk": True,
                "details": f"move5={last_5_move/atr:.1f}xATR bar={bar_size_atr:.1f}xATR"}

    # Body location preference
    if direction == "bullish":
        good_loc = body_loc < 0.5     # close in lower half = entry near low
    else:
        good_loc = body_loc > 0.5

    # Room to opposing level
    room_atr = (nearest_opposing_distance / atr
                if nearest_opposing_distance and atr > 0 else 999)

    if good_loc and room_atr >= 1.5:
        quality = "excellent"
    elif room_atr >= 1.0:
        quality = "good"
    elif room_atr >= 0.5:
        quality = "marginal"
    else:
        quality = "no_setup"

    return {"quality": quality, "late_entry_risk": False,
            "details": f"body_loc={body_loc:.2f} room_atr={room_atr:.2f}"}
