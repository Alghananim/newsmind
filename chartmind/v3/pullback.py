# -*- coding: utf-8 -*-
"""Pullback / retest quality (Brooks).

After a breakout, did price return to test the broken level cleanly?
"""
from __future__ import annotations
from typing import List, Optional
from .models import Bar


def assess(bars: List[Bar], level_price: float, atr: float,
           breakout_idx: Optional[int],
           direction: str = "up") -> dict:
    """Returns:
        status: successful/failed/pending/none
        quality: clean/deep/shallow/n_a
    """
    if breakout_idx is None or atr <= 0 or level_price is None:
        return {"status": "none", "quality": "n_a", "details": "no_breakout"}

    n = len(bars)
    if n - breakout_idx < 2:
        return {"status": "pending", "quality": "n_a",
                "details": "not_enough_post_breakout_bars"}

    after = bars[breakout_idx + 1:]
    # Look for return to level (within 0.5 ATR)
    test_idx = None
    for i, b in enumerate(after):
        if direction == "up":
            if b.low <= level_price + 0.5 * atr:
                test_idx = i
                break
        else:
            if b.high >= level_price - 0.5 * atr:
                test_idx = i
                break

    if test_idx is None:
        return {"status": "none", "quality": "n_a",
                "details": "no_retest_yet_no_pullback"}

    test_bar = after[test_idx]
    last = bars[-1]

    # Did price hold the level (successful retest)?
    if direction == "up":
        held = last.close > level_price
        # Check rejection wick at retest
        rejection = (test_bar.low - level_price) <= 0.5 * atr and test_bar.close > level_price
    else:
        held = last.close < level_price
        rejection = (level_price - test_bar.high) <= 0.5 * atr and test_bar.close < level_price

    # Depth: how far from level did pullback go (in ATR)
    depth_atr = abs(test_bar.low - level_price if direction == "up"
                    else test_bar.high - level_price) / atr

    if held and rejection:
        quality = "clean" if depth_atr < 0.7 else "deep"
        return {"status": "successful", "quality": quality,
                "details": f"depth_atr={depth_atr:.2f}"}

    if not held:
        return {"status": "failed", "quality": "n_a",
                "details": f"close_returned_within_level"}

    return {"status": "pending", "quality": "shallow" if depth_atr < 0.3 else "n_a",
            "details": f"depth_atr={depth_atr:.2f}"}
