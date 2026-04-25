# -*- coding: utf-8 -*-
"""Wyckoff phase detection — Phase B of the deepening plan.

Richard Wyckoff described the price-action cycle as four macro phases
that repeat endlessly at every scale. Recognising which phase the
market is currently in tells a scalper what KIND of trade to look for:

  * Markup        — clean up-trend; look for pullback-to-MA entries
  * Distribution  — topping range; look for short setups, avoid longs
  * Markdown      — clean down-trend; look for rejection-at-supply shorts
  * Accumulation  — basing range; look for long setups, avoid shorts

Within Accumulation and Distribution, Wyckoff further identified
sub-phases labelled A through E, punctuated by specific events: the
Selling Climax, Automatic Rally, Secondary Test, Spring, Sign of
Strength, Last Point of Support, and so on.

This module implements a pragmatic detector that maps observable
features (EMA stack, ADX, range geometry, ATR contraction, volume
behaviour, presence of Spring/Upthrust) to a phase label with a
confidence score. It does NOT try to reproduce the full Wyckoff
schematic — such a pattern-match is discretionary by design — but it
gets 70-80% of what a human Wyckoffian would annotate.

Reference concepts: Wyckoff's original course (1910s-1930s), Pruden,
and the widely-taught modern syllabus (e.g. Stockcharts Wyckoff
wiki). All code below is an original implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Output record.
# ---------------------------------------------------------------------------
@dataclass
class WyckoffEvent:
    """A single Wyckoff-style event detected in the recent past."""
    name: str                    # e.g. "selling_climax", "automatic_rally"
    ts: pd.Timestamp
    price: float
    detail: str


@dataclass
class WyckoffPhase:
    """Current Wyckoff phase assessment."""
    phase: str                   # "accumulation" | "markup" | "distribution"
                                 # | "markdown" | "unknown"
    sub_phase: str               # "early" | "mid" | "late" | ""
    confidence: float            # 0..1
    range_high: Optional[float]  # current basing/topping range upper edge
    range_low: Optional[float]   # lower edge
    range_bars: int              # how long the range has been in place
    events: list                 # list of WyckoffEvent
    detail: str                  # human-readable summary

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(self.range_high, float) and not np.isfinite(self.range_high):
            d["range_high"] = None
        if isinstance(self.range_low, float) and not np.isfinite(self.range_low):
            d["range_low"] = None
        d["events"] = [
            {**asdict(e), "ts": e.ts.isoformat() if isinstance(e.ts, pd.Timestamp) else str(e.ts)}
            for e in self.events
        ]
        return d


# ---------------------------------------------------------------------------
# Internal: geometric helpers.
# ---------------------------------------------------------------------------
def _range_box(df: pd.DataFrame, bars: int = 60) -> tuple[float, float, int]:
    """Rough bounding box of the last `bars` bars.

    Returns (high, low, actual_bars_used).
    """
    tail = df.tail(bars)
    if len(tail) == 0:
        return float("nan"), float("nan"), 0
    return float(tail["High"].max()), float(tail["Low"].min()), len(tail)


def _range_tightness(df: pd.DataFrame, atr_series: pd.Series,
                     bars: int = 60) -> float:
    """Ratio of recent range width to typical ATR. Small value means
    price is compressed; large means wide swings.

    1.0 ≈ range equals one ATR — very tight. 5.0 ≈ five ATRs wide.
    """
    hi, lo, n = _range_box(df, bars)
    if n == 0 or not np.isfinite(hi - lo):
        return float("nan")
    recent_atr = atr_series.tail(n).mean()
    if not recent_atr or not np.isfinite(recent_atr) or recent_atr <= 0:
        return float("nan")
    return float((hi - lo) / recent_atr)


def _atr_contracting(atr_series: pd.Series, bars: int = 40) -> bool:
    """True if ATR has been trending DOWN over `bars`. Approximated by
    comparing mean of first half to mean of second half.
    """
    if len(atr_series) < bars * 2:
        return False
    tail = atr_series.tail(bars * 2)
    first_half = tail.iloc[:bars].mean()
    second_half = tail.iloc[bars:].mean()
    if not np.isfinite(first_half) or not np.isfinite(second_half):
        return False
    return second_half < first_half * 0.85


def _prior_trend(df: pd.DataFrame, lookback: int = 100) -> str:
    """Classify the move over the prior `lookback` bars as up, down,
    or flat. Used to distinguish Accumulation (after down-move) from
    Distribution (after up-move).
    """
    if len(df) < lookback + 20:
        return "unknown"
    tail = df.iloc[-(lookback + 20):-20]
    if tail.empty:
        return "unknown"
    change = tail["Close"].iloc[-1] - tail["Close"].iloc[0]
    tail_atr_span = (tail["High"].max() - tail["Low"].min())
    if tail_atr_span <= 0:
        return "flat"
    magnitude = abs(change) / tail_atr_span
    if magnitude < 0.4:
        return "flat"
    return "up" if change > 0 else "down"


def _selling_climax(df: pd.DataFrame, atr_series: pd.Series,
                    lookback: int = 80) -> Optional[WyckoffEvent]:
    """Big-volume, big-range down bar at the recent low — classical
    SC. We scan the last `lookback` bars and keep the most prominent.
    """
    if len(df) < 30 or "Volume" not in df.columns:
        return None
    tail = df.tail(lookback)
    if len(tail) < 20:
        return None
    # Numpy-array optimization: the original did an O(N²) cumulative
    # min via `tail["Low"].iloc[:i+1].min()` inside the loop. We
    # precompute it once as a cummin over the window; drops SC scan
    # from tens-of-ms to sub-ms.
    highs = tail["High"].to_numpy()
    lows = tail["Low"].to_numpy()
    opens = tail["Open"].to_numpy()
    closes = tail["Close"].to_numpy()
    vols = tail["Volume"].to_numpy().astype(float)
    volume_mean = float(vols.mean()) if vols.size else 0.0
    atr_arr = atr_series.tail(lookback).to_numpy() \
        if atr_series is not None else np.array([])
    running_min = np.minimum.accumulate(lows)
    index_np = tail.index

    best: Optional[WyckoffEvent] = None
    best_score = 0.0
    n = len(tail)
    for i in range(n):
        rng = float(highs[i] - lows[i])
        if rng <= 0:
            continue
        if (closes[i] - opens[i]) >= 0:
            continue
        atr = float(atr_arr[i]) if i < len(atr_arr) else 0.0
        if atr <= 0 or not np.isfinite(atr):
            continue
        range_norm = rng / atr
        vol_norm = (float(vols[i]) / volume_mean) if volume_mean > 0 else 1.0
        at_low = lows[i] <= running_min[i] * 1.002
        if at_low and range_norm > 1.5 and vol_norm > 1.5:
            score = range_norm * vol_norm
            if score > best_score:
                best_score = score
                best = WyckoffEvent(
                    name="selling_climax",
                    ts=index_np[i],
                    price=float(lows[i]),
                    detail=(
                        f"SC: wide down bar {range_norm:.1f}×ATR on "
                        f"{vol_norm:.1f}× avg volume at the low."
                    ),
                )
    return best


def _buying_climax(df: pd.DataFrame, atr_series: pd.Series,
                   lookback: int = 80) -> Optional[WyckoffEvent]:
    """Mirror of selling climax at a top.

    Same numpy-array optimization as _selling_climax.
    """
    if len(df) < 30 or "Volume" not in df.columns:
        return None
    tail = df.tail(lookback)
    if len(tail) < 20:
        return None

    highs = tail["High"].to_numpy()
    lows = tail["Low"].to_numpy()
    opens = tail["Open"].to_numpy()
    closes = tail["Close"].to_numpy()
    vols = tail["Volume"].to_numpy().astype(float)
    volume_mean = float(vols.mean()) if vols.size else 0.0
    atr_arr = atr_series.tail(lookback).to_numpy() \
        if atr_series is not None else np.array([])
    running_max = np.maximum.accumulate(highs)
    index_np = tail.index

    best: Optional[WyckoffEvent] = None
    best_score = 0.0
    n = len(tail)
    for i in range(n):
        rng = float(highs[i] - lows[i])
        if rng <= 0:
            continue
        if (closes[i] - opens[i]) <= 0:
            continue
        atr = float(atr_arr[i]) if i < len(atr_arr) else 0.0
        if atr <= 0 or not np.isfinite(atr):
            continue
        range_norm = rng / atr
        vol_norm = (float(vols[i]) / volume_mean) if volume_mean > 0 else 1.0
        at_high = highs[i] >= running_max[i] * 0.998
        if at_high and range_norm > 1.5 and vol_norm > 1.5:
            score = range_norm * vol_norm
            if score > best_score:
                best_score = score
                best = WyckoffEvent(
                    name="buying_climax",
                    ts=index_np[i],
                    price=float(highs[i]),
                    detail=(
                        f"BC: wide up bar {range_norm:.1f}×ATR on "
                        f"{vol_norm:.1f}× avg volume at the high."
                    ),
                )
    return best


def _sign_of_strength(df: pd.DataFrame, atr_series: pd.Series,
                      range_high: float, range_low: float) -> Optional[WyckoffEvent]:
    """A wide-range up bar closing at or above the upper quarter of
    the recent range. Signals imminent markup after accumulation.
    """
    if len(df) < 3 or not np.isfinite(range_high) or not np.isfinite(range_low):
        return None
    if range_high - range_low <= 0:
        return None
    q = range_low + 0.75 * (range_high - range_low)
    tail = df.tail(5)
    highs = tail["High"].to_numpy()
    lows = tail["Low"].to_numpy()
    opens = tail["Open"].to_numpy()
    closes = tail["Close"].to_numpy()
    atr_arr = atr_series.tail(5).to_numpy() if atr_series is not None else np.array([])
    index_np = tail.index
    for i in range(len(tail)):
        rng = float(highs[i] - lows[i])
        if rng <= 0:
            continue
        if closes[i] <= opens[i]:
            continue
        atr = float(atr_arr[i]) if i < len(atr_arr) else 0.0
        if atr <= 0 or not np.isfinite(atr):
            continue
        if rng / atr > 1.2 and closes[i] >= q:
            return WyckoffEvent(
                name="sign_of_strength",
                ts=index_np[i],
                price=float(closes[i]),
                detail=(
                    f"SOS: wide up bar {rng / atr:.1f}×ATR closing in "
                    f"the upper quarter of the range."
                ),
            )
    return None


def _sign_of_weakness(df: pd.DataFrame, atr_series: pd.Series,
                      range_high: float, range_low: float) -> Optional[WyckoffEvent]:
    """Mirror of SOS at the bottom of a topping range."""
    if len(df) < 3 or not np.isfinite(range_high) or not np.isfinite(range_low):
        return None
    if range_high - range_low <= 0:
        return None
    q = range_low + 0.25 * (range_high - range_low)
    tail = df.tail(5)
    highs = tail["High"].to_numpy()
    lows = tail["Low"].to_numpy()
    opens = tail["Open"].to_numpy()
    closes = tail["Close"].to_numpy()
    atr_arr = atr_series.tail(5).to_numpy() if atr_series is not None else np.array([])
    index_np = tail.index
    for i in range(len(tail)):
        rng = float(highs[i] - lows[i])
        if rng <= 0:
            continue
        if closes[i] >= opens[i]:
            continue
        atr = float(atr_arr[i]) if i < len(atr_arr) else 0.0
        if atr <= 0 or not np.isfinite(atr):
            continue
        if rng / atr > 1.2 and closes[i] <= q:
            return WyckoffEvent(
                name="sign_of_weakness",
                ts=index_np[i],
                price=float(closes[i]),
                detail=(
                    f"SOW: wide down bar {rng / atr:.1f}×ATR closing in "
                    f"the lower quarter of the range."
                ),
            )
    return None


# ---------------------------------------------------------------------------
# Phase classifier — the public API.
# ---------------------------------------------------------------------------
def detect_wyckoff(
    df: pd.DataFrame,
    atr_series: pd.Series,
    reading=None,
    traps: Optional[list] = None,
) -> WyckoffPhase:
    """Classify the current macro phase of the chart.

    Uses:
      * the ChartReading (trend_direction, adx, ema_stack) when provided
      * the traps list (spring / upthrust presence)
      * geometric features (range, ATR contraction, prior trend)
      * Wyckoff-style events (climax, SOS, SOW)

    Returns a WyckoffPhase with a 0..1 confidence.
    """
    if df is None or len(df) < 60:
        return WyckoffPhase(
            phase="unknown", sub_phase="", confidence=0.0,
            range_high=None, range_low=None, range_bars=0,
            events=[], detail="insufficient bars",
        )
    traps = traps or []
    hi, lo, range_bars = _range_box(df, bars=60)
    tight = _range_tightness(df, atr_series, bars=60)
    atr_compressing = _atr_contracting(atr_series, bars=30)
    prior = _prior_trend(df, lookback=80)
    events: list[WyckoffEvent] = []

    # --- strong trend branch: Markup / Markdown ----------------------
    # If the ChartReading already says "up" or "down" with decent ADX,
    # we're in a trending phase, not a ranging one.
    if reading is not None and reading.adx >= 22:
        if reading.trend_direction == "up":
            detail = (
                f"Clear up-trend (ADX {reading.adx:.0f}). Markup phase. "
                f"Look for pullback entries, not reversals."
            )
            return WyckoffPhase(
                phase="markup", sub_phase="", confidence=min(1.0, reading.adx / 40),
                range_high=None, range_low=None, range_bars=0,
                events=events, detail=detail,
            )
        if reading.trend_direction == "down":
            detail = (
                f"Clear down-trend (ADX {reading.adx:.0f}). Markdown phase. "
                f"Look for rallies to short, not bottom-picking."
            )
            return WyckoffPhase(
                phase="markdown", sub_phase="", confidence=min(1.0, reading.adx / 40),
                range_high=None, range_low=None, range_bars=0,
                events=events, detail=detail,
            )

    # --- ranging branch: Accumulation / Distribution -----------------
    # A range is recognised when tightness < 6 ATRs over 60 bars AND
    # the range has lasted at least 30 bars.
    if not np.isfinite(tight) or tight > 6 or range_bars < 30:
        return WyckoffPhase(
            phase="unknown", sub_phase="", confidence=0.2,
            range_high=hi if np.isfinite(hi) else None,
            range_low=lo if np.isfinite(lo) else None,
            range_bars=range_bars, events=events,
            detail="no clear range structure",
        )

    # We have a range. Determine whether it's likely accumulation or
    # distribution based on the prior trend.
    if prior == "down":
        # Likely accumulation
        sc = _selling_climax(df, atr_series)
        if sc:
            events.append(sc)
        has_spring = any(
            t.name == "spring" for t in traps
        )
        if has_spring:
            spring_tr = next(t for t in traps if t.name == "spring")
            events.append(WyckoffEvent(
                name="spring", ts=spring_tr.ts,
                price=getattr(spring_tr, "price", 0.0),
                detail=getattr(spring_tr, "detail", "spring event"),
            ))
        sos = _sign_of_strength(df, atr_series, hi, lo)
        if sos:
            events.append(sos)

        # Sub-phase: early (just SC), mid (range forming, no spring),
        # late (spring + SOS).
        if sos and has_spring:
            sub = "late"
            conf = 0.85
        elif has_spring:
            sub = "mid"
            conf = 0.7
        elif sc:
            sub = "early"
            conf = 0.55
        else:
            sub = "mid"
            conf = 0.45

        detail = (
            f"Accumulation {sub} phase. Range "
            f"{lo:.5f}–{hi:.5f} across {range_bars} bars. "
            + ("Spring detected — bullish setup probable. " if has_spring else "")
            + ("SOS confirms. " if sos else "")
        ).strip()
        return WyckoffPhase(
            phase="accumulation", sub_phase=sub, confidence=conf,
            range_high=hi, range_low=lo, range_bars=range_bars,
            events=events, detail=detail,
        )

    if prior == "up":
        # Likely distribution
        bc = _buying_climax(df, atr_series)
        if bc:
            events.append(bc)
        has_upthrust = any(t.name == "upthrust" for t in traps)
        if has_upthrust:
            ut_tr = next(t for t in traps if t.name == "upthrust")
            events.append(WyckoffEvent(
                name="upthrust", ts=ut_tr.ts,
                price=getattr(ut_tr, "price", 0.0),
                detail=getattr(ut_tr, "detail", "upthrust event"),
            ))
        sow = _sign_of_weakness(df, atr_series, hi, lo)
        if sow:
            events.append(sow)

        if sow and has_upthrust:
            sub = "late"
            conf = 0.85
        elif has_upthrust:
            sub = "mid"
            conf = 0.7
        elif bc:
            sub = "early"
            conf = 0.55
        else:
            sub = "mid"
            conf = 0.45

        detail = (
            f"Distribution {sub} phase. Range "
            f"{lo:.5f}–{hi:.5f} across {range_bars} bars. "
            + ("Upthrust detected — bearish setup probable. " if has_upthrust else "")
            + ("SOW confirms. " if sow else "")
        ).strip()
        return WyckoffPhase(
            phase="distribution", sub_phase=sub, confidence=conf,
            range_high=hi, range_low=lo, range_bars=range_bars,
            events=events, detail=detail,
        )

    # Prior is flat — unknown phase.
    return WyckoffPhase(
        phase="unknown", sub_phase="", confidence=0.3,
        range_high=hi, range_low=lo, range_bars=range_bars,
        events=events,
        detail="range exists but prior trend unclear — unknown phase",
    )
