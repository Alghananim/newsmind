# -*- coding: utf-8 -*-
"""ChaseDetector — has the market already moved before we received the news?

If yes, we are CHASING — late entry, blown stops, missed move. The system
must NOT trade in that scenario.

Detection signals
-----------------
   1. Bar-range spike: any of the last K bars has range >= range_mult × ATR
   2. Spread widening: current spread >= spread_mult × recent_avg_spread
   3. Volume spike: last bar volume >= volume_mult × recent_avg_volume
   4. Direction already obvious: |close[-1] - close[-K]| / ATR >= move_threshold

Decision logic
--------------
   * 0 signals fired -> not chasing (allow)
   * 1-2 signals fired -> caution (wait)
   * 3+ signals fired -> CHASING (block)

The detector is fed a rolling window of recent bars + the current bar
that arrived around news_time. It does NOT need network access.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class ChaseAssessment:
    """Output of one chase analysis."""
    chasing: bool
    score: int                       # number of signals fired (0..4)
    signals_fired: tuple
    decision: str                    # "allow" | "wait" | "block"
    reason: str


class ChaseDetector:
    """Stateless. Pass recent bars + current bar; get back a decision."""

    def __init__(self,
                 lookback: int = 20,
                 spike_window: int = 3,
                 range_mult: float = 2.0,
                 spread_mult: float = 2.0,
                 volume_mult: float = 2.0,
                 move_threshold: float = 1.5,
                 ):
        self.lookback = lookback
        self.spike_window = spike_window
        self.range_mult = range_mult
        self.spread_mult = spread_mult
        self.volume_mult = volume_mult
        self.move_threshold = move_threshold

    def assess(self, recent_bars: list, current_bar) -> ChaseAssessment:
        """Decide whether a news event is being chased.

        recent_bars: list of bar-like objects (last `lookback` bars).
        current_bar: the bar at/after news arrival.
        Each bar must have: high, low, close, spread_pips, volume.
        """
        signals = []

        if len(recent_bars) < self.lookback:
            # Insufficient context — be conservative
            return ChaseAssessment(
                chasing=False, score=0, signals_fired=(),
                decision="wait", reason="insufficient_history",
            )

        # ATR(lookback) computed from recent_bars
        ranges = [b.high - b.low for b in recent_bars]
        atr = sum(ranges) / len(ranges)

        # Signal 1: range spike on the last `spike_window` bars
        recent_window = recent_bars[-self.spike_window:] + [current_bar]
        max_range = max(b.high - b.low for b in recent_window)
        if atr > 0 and max_range >= self.range_mult * atr:
            signals.append(f"range_spike_{max_range/atr:.1f}xATR")

        # Signal 2: spread widening
        if hasattr(current_bar, "spread_pips") and recent_bars[0].spread_pips:
            avg_spread = sum(getattr(b, "spread_pips", 0.5)
                             for b in recent_bars) / len(recent_bars)
            cur_spread = getattr(current_bar, "spread_pips", 0.5)
            if avg_spread > 0 and cur_spread >= self.spread_mult * avg_spread:
                signals.append(f"spread_widen_{cur_spread/avg_spread:.1f}x")

        # Signal 3: volume spike
        if hasattr(current_bar, "volume"):
            avg_vol = sum(getattr(b, "volume", 0) or 0 for b in recent_bars)
            avg_vol = avg_vol / len(recent_bars) if avg_vol > 0 else 0
            cur_vol = getattr(current_bar, "volume", 0) or 0
            if avg_vol > 0 and cur_vol >= self.volume_mult * avg_vol:
                signals.append(f"volume_spike_{cur_vol/avg_vol:.1f}x")

        # Signal 4: directional move already happened
        if atr > 0 and len(recent_bars) >= self.spike_window + 1:
            move = abs(current_bar.close - recent_bars[-self.spike_window-1].close)
            if move / atr >= self.move_threshold:
                signals.append(f"move_done_{move/atr:.1f}xATR")

        score = len(signals)
        if score >= 3:
            return ChaseAssessment(
                chasing=True, score=score, signals_fired=tuple(signals),
                decision="block",
                reason=f"chasing_{score}_signals",
            )
        if score >= 1:
            return ChaseAssessment(
                chasing=False, score=score, signals_fired=tuple(signals),
                decision="wait",
                reason=f"caution_{score}_signals",
            )
        return ChaseAssessment(
            chasing=False, score=0, signals_fired=(),
            decision="allow",
            reason="no_chase_signals",
        )
