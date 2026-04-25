# -*- coding: utf-8 -*-
"""CandleAnalyzer — single + multi-bar patterns with structure context.

We score patterns much higher when they form AT a known S/R level
(see Linda Raschke, Holy Grail Setups). A bullish engulfing at PDL
is far more meaningful than the same pattern in mid-air.

Patterns recognised
-------------------
   * Bullish engulfing  / Bearish engulfing
   * Pin bar (bull/bear) — long wick on one side, small body
   * Inside bar (consolidation; awaits breakout)
   * 2-bar reversal — bear bar followed by full bull (or vice versa)
   * Break-of-structure (BOS) — close beyond a recent swing
"""
from __future__ import annotations
from typing import Optional
from .models import CandleReading, StructureReading


def _body(b) -> float:
    return abs(b.close - b.open)

def _range(b) -> float:
    return b.high - b.low

def _upper_wick(b) -> float:
    return b.high - max(b.close, b.open)

def _lower_wick(b) -> float:
    return min(b.close, b.open) - b.low

def _is_bull(b) -> bool:
    return b.close > b.open

def _is_bear(b) -> bool:
    return b.close < b.open


def detect_engulfing(prev, cur) -> Optional[tuple[str, float]]:
    """Bullish: prev bear + cur bull whose body engulfs prev body.
    Bearish: prev bull + cur bear whose body engulfs prev body.
    Returns (direction, strength 0..1) or None.
    """
    if _body(cur) < _body(prev) * 1.0:
        return None
    pb_open, pb_close = prev.open, prev.close
    cb_open, cb_close = cur.open, cur.close
    if _is_bear(prev) and _is_bull(cur):
        if cb_open <= pb_close and cb_close >= pb_open:
            strength = min(1.0, _body(cur) / max(_body(prev), 1e-9) / 2.0)
            return "long", strength
    if _is_bull(prev) and _is_bear(cur):
        if cb_open >= pb_close and cb_close <= pb_open:
            strength = min(1.0, _body(cur) / max(_body(prev), 1e-9) / 2.0)
            return "short", strength
    return None


def detect_pin_bar(b) -> Optional[tuple[str, float]]:
    """Pin bar: small body + long wick on one side.
    Bullish pin: lower wick >= 2x body, upper wick small.
    Bearish pin: upper wick >= 2x body, lower wick small.
    """
    body = _body(b)
    rng = _range(b)
    if rng <= 0 or body / rng > 0.4:
        return None
    lw, uw = _lower_wick(b), _upper_wick(b)
    if lw >= 2 * body and uw <= 0.6 * body:
        return "long", min(1.0, lw / max(body, 1e-9) / 4.0)
    if uw >= 2 * body and lw <= 0.6 * body:
        return "short", min(1.0, uw / max(body, 1e-9) / 4.0)
    return None


def detect_inside_bar(prev, cur) -> Optional[tuple[str, float]]:
    """Inside bar: cur high < prev high AND cur low > prev low.
    Direction unknown until breakout — return 'neutral' so confluence
    treats it as compression.
    """
    if cur.high < prev.high and cur.low > prev.low:
        return "neutral", 0.5
    return None


def detect_two_bar_reversal(prev, cur) -> Optional[tuple[str, float]]:
    """Strong bear bar followed by a strong bull bar that closes
    above the bear bar's open (or vice versa).
    """
    if _is_bear(prev) and _is_bull(cur):
        if cur.close > prev.open and _body(cur) >= _body(prev) * 0.8:
            return "long", 0.7
    if _is_bull(prev) and _is_bear(cur):
        if cur.close < prev.open and _body(cur) >= _body(prev) * 0.8:
            return "short", 0.7
    return None


def detect_bos(bars: list, lookback: int = 20) -> Optional[tuple[str, float]]:
    """Break of structure: latest close above recent swing high (long)
    or below recent swing low (short).
    """
    if len(bars) < lookback + 2:
        return None
    recent = bars[-lookback:-1]   # exclude current bar
    swing_high = max(b.high for b in recent)
    swing_low = min(b.low for b in recent)
    cur = bars[-1]
    if cur.close > swing_high:
        return "long", 0.65
    if cur.close < swing_low:
        return "short", 0.65
    return None


class CandleAnalyzer:
    """Analyse the last 2 bars; if at a structure level, boost strength.

    Usage:
        ca = CandleAnalyzer(pair_pip=0.0001)
        reading = ca.analyze(m15_bars, structure_reading)
    """
    def __init__(self, pair_pip: float = 0.0001,
                 structure_tolerance_pips: float = 4.0):
        self.pair_pip = pair_pip
        self.structure_tolerance_pips = structure_tolerance_pips

    def analyze(self, m15_bars: list,
                structure: Optional[StructureReading] = None) -> CandleReading:
        if len(m15_bars) < 3:
            return self._none(0)
        prev, cur = m15_bars[-2], m15_bars[-1]

        # Try detectors in priority order
        det = None
        pattern_name = "none"
        for name, fn in [
            ("engulfing", lambda: detect_engulfing(prev, cur)),
            ("pin_bar", lambda: detect_pin_bar(cur)),
            ("two_bar_reversal", lambda: detect_two_bar_reversal(prev, cur)),
            ("bos", lambda: detect_bos(m15_bars)),
            ("inside_bar", lambda: detect_inside_bar(prev, cur)),
        ]:
            res = fn()
            if res is not None:
                det = res
                pattern_name = name
                break

        if det is None:
            return self._none(0)

        direction, strength = det

        # Boost strength if at structure level
        at_structure = False
        structure_label = ""
        if structure is not None:
            cur_price = cur.close
            for level in (structure.nearest_support, structure.nearest_resistance,
                          *(structure.levels[:5] if structure.levels else [])):
                if level is None:
                    continue
                dist = abs(cur_price - level.price) / self.pair_pip
                if dist <= self.structure_tolerance_pips:
                    at_structure = True
                    structure_label = level.label
                    strength = min(1.0, strength * 1.5)
                    break

        return CandleReading(
            pattern=pattern_name, direction=direction,
            at_structure=at_structure, structure_label=structure_label,
            strength=strength, bar_index=0,
        )

    def _none(self, idx: int) -> CandleReading:
        return CandleReading(pattern="none", direction="neutral",
                             at_structure=False, structure_label="",
                             strength=0.0, bar_index=idx)
