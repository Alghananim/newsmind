# -*- coding: utf-8 -*-
"""Multi-candle price-action context — Phase C of the deepening plan.

Any bar in isolation is noise. The meaning of a candle comes from the
bars around it: the one that just preceded it, the level it closed
near, and whether the next bar confirmed or rejected the hypothesis
the prior bar raised. This module encodes that reading.

Six structures we detect:

    1. Signal bar        — a bar that advertises an entry idea
    2. Entry bar         — the bar that confirms the signal by
                           breaking past it
    3. Two-legged pullback — two down-legs separated by a small push,
                           the cleanest continuation entry
    4. Higher-high failure — an attempted new high that closes back
                           below the previous high
    5. Lower-low failure  — mirror for downtrend
    6. Trend transition bar — a large-range bar that reverses momentum
                           at a key level

Concepts drawn from Al Brooks' price-action work. Original code below.
All time-series inputs must end at the bar we're currently reading;
look-ahead is impossible by construction because every scan walks
forward only until `len(df) - 1`.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Output records.
# ---------------------------------------------------------------------------
@dataclass
class SignalBar:
    ts: pd.Timestamp
    direction: str                # "bullish" | "bearish"
    kind: str                     # "reversal_bar" | "trend_bar" | "doji_at_level"
    strength: float               # 0..1
    detail: str


@dataclass
class EntryBar:
    ts: pd.Timestamp
    direction: str                # "bullish" | "bearish"
    triggered_signal_at: Optional[pd.Timestamp]
    entry_price: float
    detail: str


@dataclass
class Pullback:
    ts_end: pd.Timestamp
    direction: str                # "bullish" | "bearish" (trend direction)
    legs: int                     # usually 2
    depth_atr: float              # total pullback in ATR units
    detail: str


@dataclass
class StructuralFailure:
    """A higher-high-fail (bearish warning) or lower-low-fail (bullish
    warning) — prior extreme could not be surpassed."""
    ts: pd.Timestamp
    kind: str                     # "hh_failure" | "ll_failure"
    prior_extreme_price: float
    prior_extreme_ts: pd.Timestamp
    detail: str


@dataclass
class TransitionBar:
    ts: pd.Timestamp
    direction: str                # direction of the NEW trend implied
    range_atr: float              # size of the bar in ATR units
    detail: str


@dataclass
class PriceActionContext:
    """Aggregated price-action reading for the last ~30 bars."""
    signal_bars: list = field(default_factory=list)
    entry_bars: list = field(default_factory=list)
    pullbacks: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    transitions: list = field(default_factory=list)
    # A single, most-actionable recommendation in plain text:
    best_setup: str = ""

    def to_dict(self) -> dict:
        def _ts(obj):
            d = asdict(obj)
            for k, v in d.items():
                if isinstance(v, pd.Timestamp):
                    d[k] = v.isoformat()
            return d
        return {
            "signal_bars": [_ts(s) for s in self.signal_bars],
            "entry_bars":  [_ts(s) for s in self.entry_bars],
            "pullbacks":   [_ts(s) for s in self.pullbacks],
            "failures":    [_ts(s) for s in self.failures],
            "transitions": [_ts(s) for s in self.transitions],
            "best_setup":  self.best_setup,
        }


# ---------------------------------------------------------------------------
# Small helpers.
# ---------------------------------------------------------------------------
def _candle_body(bar) -> tuple[float, float, float, float]:
    o = float(bar["Open"]); h = float(bar["High"])
    l = float(bar["Low"]);  c = float(bar["Close"])
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return body, upper, lower, (h - l)


def _atr_at(atr_series: pd.Series, i: int) -> float:
    if i < 0 or i >= len(atr_series):
        return 0.0
    v = atr_series.iloc[i]
    return float(v) if pd.notna(v) else 0.0


# ---------------------------------------------------------------------------
# 1. Signal bars (last 3 bars of df).
# ---------------------------------------------------------------------------
def _detect_signal_bars(df: pd.DataFrame,
                        atr_series: pd.Series,
                        trend_direction: str) -> list[SignalBar]:
    out: list[SignalBar] = []
    if len(df) < 3:
        return out
    for i in range(max(0, len(df) - 3), len(df)):
        bar = df.iloc[i]
        atr = _atr_at(atr_series, i)
        if atr <= 0:
            continue
        body, upper, lower, rng = _candle_body(bar)
        if rng <= 0:
            continue
        strength_raw = rng / atr   # size relative to ATR

        # Bullish reversal bar: long lower wick, small body, closes near high.
        if lower > 2 * body and upper < body and bar["Close"] > bar["Open"]:
            out.append(SignalBar(
                ts=df.index[i], direction="bullish", kind="reversal_bar",
                strength=min(1.0, 0.5 + 0.3 * strength_raw),
                detail=(
                    f"Bullish reversal bar: lower wick dominates, "
                    f"bar size {strength_raw:.1f}× ATR."
                ),
            ))
        # Bearish reversal bar
        if upper > 2 * body and lower < body and bar["Close"] < bar["Open"]:
            out.append(SignalBar(
                ts=df.index[i], direction="bearish", kind="reversal_bar",
                strength=min(1.0, 0.5 + 0.3 * strength_raw),
                detail=(
                    f"Bearish reversal bar: upper wick dominates, "
                    f"bar size {strength_raw:.1f}× ATR."
                ),
            ))
        # Trend bar in-direction: big body same as prevailing trend.
        if body / rng > 0.75 and strength_raw > 1.0:
            direction = "bullish" if bar["Close"] > bar["Open"] else "bearish"
            if (direction == "bullish" and trend_direction == "up") or \
               (direction == "bearish" and trend_direction == "down"):
                out.append(SignalBar(
                    ts=df.index[i], direction=direction, kind="trend_bar",
                    strength=min(1.0, 0.4 + 0.4 * strength_raw),
                    detail=(
                        f"{direction.capitalize()} trend bar aligned with "
                        f"current trend ({strength_raw:.1f}× ATR, "
                        f"body {body/rng:.0%} of range)."
                    ),
                ))
        # Doji at extreme: small body, notable range, closes near centre.
        if body / rng < 0.12 and strength_raw > 0.7:
            out.append(SignalBar(
                ts=df.index[i], direction="bullish",   # neutral, mark both
                kind="doji_at_level",
                strength=min(1.0, 0.4 + 0.25 * strength_raw),
                detail=(
                    f"Doji ({strength_raw:.1f}× ATR) — indecision. "
                    f"Meaningful if located at a key level."
                ),
            ))
    return out


# ---------------------------------------------------------------------------
# 2. Entry bars — a bar after a signal bar that breaks past it.
# ---------------------------------------------------------------------------
def _detect_entry_bars(df: pd.DataFrame,
                       signal_bars: list[SignalBar]) -> list[EntryBar]:
    out: list[EntryBar] = []
    if len(df) < 2 or not signal_bars:
        return out
    # For each signal bar, check the subsequent bar (if any).
    # Map signal timestamps to df row numbers.
    idx_map = {ts: i for i, ts in enumerate(df.index)}
    for sb in signal_bars:
        i = idx_map.get(sb.ts)
        if i is None or i + 1 >= len(df):
            continue
        sb_bar = df.iloc[i]
        next_bar = df.iloc[i + 1]
        if sb.direction == "bullish":
            if float(next_bar["High"]) > float(sb_bar["High"]):
                out.append(EntryBar(
                    ts=df.index[i + 1], direction="bullish",
                    triggered_signal_at=sb.ts,
                    entry_price=float(sb_bar["High"]) + 0.00005,
                    detail=(
                        f"Entry bar: broke signal bar high "
                        f"{float(sb_bar['High']):.5f}."
                    ),
                ))
        elif sb.direction == "bearish":
            if float(next_bar["Low"]) < float(sb_bar["Low"]):
                out.append(EntryBar(
                    ts=df.index[i + 1], direction="bearish",
                    triggered_signal_at=sb.ts,
                    entry_price=float(sb_bar["Low"]) - 0.00005,
                    detail=(
                        f"Entry bar: broke signal bar low "
                        f"{float(sb_bar['Low']):.5f}."
                    ),
                ))
    return out


# ---------------------------------------------------------------------------
# 3. Two-legged pullback.
# ---------------------------------------------------------------------------
def _detect_pullback(df: pd.DataFrame,
                     atr_series: pd.Series,
                     trend_direction: str) -> list[Pullback]:
    out: list[Pullback] = []
    if len(df) < 10 or trend_direction not in ("up", "down"):
        return out
    tail = df.tail(20)
    atr = _atr_at(atr_series, len(df) - 1)
    if atr <= 0:
        return out
    closes = tail["Close"].to_numpy()
    # Identify consecutive-bar legs.
    legs: list[tuple[str, float, int]] = []
    i = 0
    while i < len(closes) - 1:
        start = i
        if closes[i + 1] > closes[i]:
            # up leg
            while i + 1 < len(closes) and closes[i + 1] > closes[i]:
                i += 1
            legs.append(("up", closes[i] - closes[start], i - start))
        elif closes[i + 1] < closes[i]:
            # down leg
            while i + 1 < len(closes) and closes[i + 1] < closes[i]:
                i += 1
            legs.append(("down", closes[start] - closes[i], i - start))
        else:
            i += 1
    # Uptrend pullback = two down legs with a small up leg between.
    if len(legs) >= 3 and trend_direction == "up":
        last3 = legs[-3:]
        if (last3[0][0] == "down" and last3[1][0] == "up"
                and last3[2][0] == "down"):
            depth = (last3[0][1] + last3[2][1]) / atr
            if 0.5 < depth < 3.0:
                out.append(Pullback(
                    ts_end=tail.index[-1],
                    direction="bullish",   # pullback IN an uptrend ⇒ buy
                    legs=2,
                    depth_atr=depth,
                    detail=(
                        f"Two-legged pullback in uptrend, depth "
                        f"{depth:.1f}× ATR."
                    ),
                ))
    if len(legs) >= 3 and trend_direction == "down":
        last3 = legs[-3:]
        if (last3[0][0] == "up" and last3[1][0] == "down"
                and last3[2][0] == "up"):
            depth = (last3[0][1] + last3[2][1]) / atr
            if 0.5 < depth < 3.0:
                out.append(Pullback(
                    ts_end=tail.index[-1],
                    direction="bearish",
                    legs=2,
                    depth_atr=depth,
                    detail=(
                        f"Two-legged pullback in downtrend, depth "
                        f"{depth:.1f}× ATR."
                    ),
                ))
    return out


# ---------------------------------------------------------------------------
# 4/5. Structural failure (HH / LL failure).
# ---------------------------------------------------------------------------
def _detect_failures(df: pd.DataFrame,
                     swing_highs: list,
                     swing_lows: list,
                     trend_direction: str) -> list[StructuralFailure]:
    out: list[StructuralFailure] = []
    # HH failure: the most recent swing high is LOWER than the previous.
    if trend_direction == "up" and len(swing_highs) >= 2:
        latest, prior = swing_highs[-1], swing_highs[-2]
        if latest.price < prior.price:
            out.append(StructuralFailure(
                ts=latest.ts, kind="hh_failure",
                prior_extreme_price=prior.price,
                prior_extreme_ts=prior.ts,
                detail=(
                    f"Higher-high failure: last swing high "
                    f"{latest.price:.5f} below prior "
                    f"{prior.price:.5f} — uptrend weakening."
                ),
            ))
    if trend_direction == "down" and len(swing_lows) >= 2:
        latest, prior = swing_lows[-1], swing_lows[-2]
        if latest.price > prior.price:
            out.append(StructuralFailure(
                ts=latest.ts, kind="ll_failure",
                prior_extreme_price=prior.price,
                prior_extreme_ts=prior.ts,
                detail=(
                    f"Lower-low failure: last swing low "
                    f"{latest.price:.5f} above prior "
                    f"{prior.price:.5f} — downtrend weakening."
                ),
            ))
    return out


# ---------------------------------------------------------------------------
# 6. Trend-transition bars.
# ---------------------------------------------------------------------------
def _detect_transitions(df: pd.DataFrame,
                        atr_series: pd.Series,
                        trend_direction: str) -> list[TransitionBar]:
    out: list[TransitionBar] = []
    if len(df) < 5:
        return out
    tail = df.tail(5)
    for i in range(len(tail)):
        bar = tail.iloc[i]
        atr = _atr_at(atr_series, len(df) - len(tail) + i)
        if atr <= 0:
            continue
        body, upper, lower, rng = _candle_body(bar)
        if rng / atr < 1.5:   # must be a "wide-range" bar to qualify
            continue
        # Bullish transition: big bullish bar during a downtrend
        if trend_direction == "down" and bar["Close"] > bar["Open"] \
                and body / rng > 0.6:
            out.append(TransitionBar(
                ts=tail.index[i], direction="bullish",
                range_atr=rng / atr,
                detail=(
                    f"Transition bar: wide bullish candle "
                    f"({rng/atr:.1f}× ATR) inside an existing downtrend."
                ),
            ))
        # Bearish transition: big bearish bar during an uptrend
        if trend_direction == "up" and bar["Close"] < bar["Open"] \
                and body / rng > 0.6:
            out.append(TransitionBar(
                ts=tail.index[i], direction="bearish",
                range_atr=rng / atr,
                detail=(
                    f"Transition bar: wide bearish candle "
                    f"({rng/atr:.1f}× ATR) inside an existing uptrend."
                ),
            ))
    return out


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def read_price_action(
    df: pd.DataFrame,
    atr_series: pd.Series,
    reading,        # ChartReading
) -> PriceActionContext:
    """Produce the multi-bar price-action reading for the end of `df`.

    Uses the ChartReading's trend + swings to stay consistent with the
    rest of the brain.
    """
    tdir = reading.trend_direction if reading is not None else "flat"
    swing_h = reading.swing_highs if reading is not None else []
    swing_l = reading.swing_lows if reading is not None else []

    signals = _detect_signal_bars(df, atr_series, tdir)
    entries = _detect_entry_bars(df, signals)
    pullbacks = _detect_pullback(df, atr_series, tdir)
    failures = _detect_failures(df, swing_h, swing_l, tdir)
    transitions = _detect_transitions(df, atr_series, tdir)

    # Rank structures to suggest the best actionable setup.
    best_setup = _rank_best(signals, entries, pullbacks, failures, transitions,
                            tdir)

    return PriceActionContext(
        signal_bars=signals[-4:],
        entry_bars=entries[-2:],
        pullbacks=pullbacks[-2:],
        failures=failures[-2:],
        transitions=transitions[-2:],
        best_setup=best_setup,
    )


def _rank_best(signals, entries, pullbacks, failures, transitions,
               tdir: str) -> str:
    # 1. Entry bar aligned with trend — strongest actionable setup
    for eb in reversed(entries):
        if (eb.direction == "bullish" and tdir == "up") or \
           (eb.direction == "bearish" and tdir == "down"):
            return (
                f"Signal+Entry bar setup confirmed — "
                f"{eb.direction} continuation in {tdir}-trend."
            )
    # 2. Two-legged pullback
    if pullbacks:
        pb = pullbacks[-1]
        return (
            f"Two-legged pullback in {tdir}-trend "
            f"({pb.depth_atr:.1f}× ATR) — prepare continuation entry."
        )
    # 3. Transition bar
    if transitions:
        tb = transitions[-1]
        return (
            f"Trend transition bar ({tb.direction}, {tb.range_atr:.1f}× "
            f"ATR) — trend may be flipping."
        )
    # 4. Structural failure
    if failures:
        f = failures[-1]
        return f"Structural warning: {f.kind} — existing trend weakening."
    # 5. Signal bar only (no entry yet)
    if signals:
        sb = signals[-1]
        return f"Signal bar only ({sb.kind}, {sb.direction}) — wait for entry bar."
    return "No actionable price-action setup."
