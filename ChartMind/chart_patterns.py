# -*- coding: utf-8 -*-
"""Classical chart patterns — Phase D of the deepening plan.

Eight patterns every textbook teaches are detected here: Double Top,
Double Bottom, Head and Shoulders, Inverse Head and Shoulders,
ascending / descending / symmetric triangles, bull flag, bear flag,
rising wedge, and falling wedge. Each detection produces:

    * the pattern's name and implied direction
    * a confidence score [0, 1] built from geometric tightness
    * a "measured move" price target (for most patterns)
    * an invalidation level (beyond which the pattern fails)
    * a timestamp of completion

Inputs are the swing-point lists already produced by ChartMind (so we
stay consistent with the rest of the brain — no second set of
swings), plus the OHLCV frame and its ATR series for relative-size
scaling. All functions are pure, forward-scanning, no look-ahead.

Reference concepts: Edwards & Magee (1948), Murphy's Technical
Analysis of the Financial Markets, Bulkowski's Encyclopedia of Chart
Patterns. Code is original Python.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Output record.
# ---------------------------------------------------------------------------
@dataclass
class ChartPattern:
    name: str                       # "double_top", "head_shoulders", ...
    direction: str                  # "bullish" | "bearish"
    confidence: float               # 0..1
    target: Optional[float]         # measured-move price target
    invalidation: Optional[float]   # price that nullifies the pattern
    anchor_ts: pd.Timestamp         # completion (or current) bar ts
    detail: str

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(d.get("anchor_ts"), pd.Timestamp):
            d["anchor_ts"] = d["anchor_ts"].isoformat()
        return d


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------
def _approx_equal(a: float, b: float, tol_atr: float, atr: float) -> bool:
    return abs(a - b) <= tol_atr * max(atr, 1e-9)


def _fit_line(points: list[tuple[int, float]]) -> tuple[float, float, float]:
    """Least-squares slope + intercept, plus R² quality.
    Returns (slope, intercept, r_squared). points = [(x, y), ...].
    """
    if len(points) < 2:
        return 0.0, 0.0, 0.0
    xs = np.array([p[0] for p in points], dtype=float)
    ys = np.array([p[1] for p in points], dtype=float)
    if xs.std() == 0:
        return 0.0, ys.mean(), 0.0
    slope, intercept = np.polyfit(xs, ys, 1)
    y_pred = slope * xs + intercept
    ss_res = ((ys - y_pred) ** 2).sum()
    ss_tot = ((ys - ys.mean()) ** 2).sum()
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return float(slope), float(intercept), float(r2)


# ---------------------------------------------------------------------------
# 1. Double top / double bottom.
# ---------------------------------------------------------------------------
def _detect_double_top(swing_h: list, atr: float,
                       df: pd.DataFrame) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(swing_h) < 2 or atr <= 0:
        return out
    # Use the last 5 swing highs as candidates
    for i in range(len(swing_h) - 1):
        peak1 = swing_h[i]
        peak2 = swing_h[i + 1]
        # Peaks within 0.3 × ATR of each other
        if not _approx_equal(peak1.price, peak2.price, 0.3, atr):
            continue
        # Must be separated by at least 5 bars
        if peak2.ts <= peak1.ts:
            continue
        # Trough between them — find lowest low between peak1.ts and peak2.ts
        mask = (df.index > peak1.ts) & (df.index < peak2.ts)
        between = df.loc[mask]
        if len(between) < 3:
            continue
        trough = float(between["Low"].min())
        peak_mean = (peak1.price + peak2.price) / 2.0
        depth = peak_mean - trough
        if depth < atr * 0.8:
            continue
        # Invalidation: new high above peak_mean + small buffer
        invalidation = peak_mean + 0.25 * atr
        # Target: neckline (trough) minus depth
        target = trough - depth
        # Confidence: closer equal + deeper = stronger
        eq_err = abs(peak1.price - peak2.price) / max(atr, 1e-9)
        conf = 0.6 + 0.3 * min(1.0, depth / (atr * 2)) - 0.3 * eq_err
        conf = float(max(0.0, min(1.0, conf)))
        out.append(ChartPattern(
            name="double_top",
            direction="bearish",
            confidence=conf,
            target=target,
            invalidation=invalidation,
            anchor_ts=peak2.ts,
            detail=(
                f"Double top at ~{peak_mean:.5f}, neckline {trough:.5f}, "
                f"depth {depth / atr:.1f}× ATR."
            ),
        ))
    return out


def _detect_double_bottom(swing_l: list, atr: float,
                          df: pd.DataFrame) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(swing_l) < 2 or atr <= 0:
        return out
    for i in range(len(swing_l) - 1):
        trough1 = swing_l[i]
        trough2 = swing_l[i + 1]
        if not _approx_equal(trough1.price, trough2.price, 0.3, atr):
            continue
        if trough2.ts <= trough1.ts:
            continue
        mask = (df.index > trough1.ts) & (df.index < trough2.ts)
        between = df.loc[mask]
        if len(between) < 3:
            continue
        peak = float(between["High"].max())
        trough_mean = (trough1.price + trough2.price) / 2.0
        depth = peak - trough_mean
        if depth < atr * 0.8:
            continue
        invalidation = trough_mean - 0.25 * atr
        target = peak + depth
        eq_err = abs(trough1.price - trough2.price) / max(atr, 1e-9)
        conf = 0.6 + 0.3 * min(1.0, depth / (atr * 2)) - 0.3 * eq_err
        conf = float(max(0.0, min(1.0, conf)))
        out.append(ChartPattern(
            name="double_bottom",
            direction="bullish",
            confidence=conf,
            target=target,
            invalidation=invalidation,
            anchor_ts=trough2.ts,
            detail=(
                f"Double bottom at ~{trough_mean:.5f}, neckline {peak:.5f}, "
                f"depth {depth / atr:.1f}× ATR."
            ),
        ))
    return out


# ---------------------------------------------------------------------------
# 2. Head and shoulders (and inverse).
# ---------------------------------------------------------------------------
def _detect_head_shoulders(swing_h: list, atr: float,
                           df: pd.DataFrame) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(swing_h) < 3 or atr <= 0:
        return out
    # Consider the last 3 swing highs: left, head, right
    left, head, right = swing_h[-3], swing_h[-2], swing_h[-1]
    if not (head.price > left.price and head.price > right.price):
        return out
    # Shoulders roughly equal (within 0.4 × ATR)
    if not _approx_equal(left.price, right.price, 0.4, atr):
        return out
    # Head must exceed shoulders by at least 0.5 × ATR
    shoulder_mean = (left.price + right.price) / 2.0
    if head.price - shoulder_mean < 0.5 * atr:
        return out
    # Neckline — troughs between (left, head) and (head, right)
    lh_mask = (df.index > left.ts) & (df.index < head.ts)
    hr_mask = (df.index > head.ts) & (df.index < right.ts)
    left_trough = df.loc[lh_mask, "Low"].min()
    right_trough = df.loc[hr_mask, "Low"].min()
    if pd.isna(left_trough) or pd.isna(right_trough):
        return out
    neckline = float((left_trough + right_trough) / 2.0)
    depth = head.price - neckline
    if depth < atr * 1.0:
        return out
    target = neckline - depth
    invalidation = head.price + 0.25 * atr
    # Confidence
    shoulder_err = abs(left.price - right.price) / max(atr, 1e-9)
    conf = 0.55 + 0.3 * min(1.0, depth / (atr * 3)) - 0.3 * shoulder_err
    conf = float(max(0.0, min(1.0, conf)))
    out.append(ChartPattern(
        name="head_shoulders",
        direction="bearish",
        confidence=conf,
        target=target,
        invalidation=invalidation,
        anchor_ts=right.ts,
        detail=(
            f"H&S: shoulders ~{shoulder_mean:.5f}, head {head.price:.5f}, "
            f"neckline {neckline:.5f}, depth {depth / atr:.1f}× ATR."
        ),
    ))
    return out


def _detect_inverse_head_shoulders(swing_l: list, atr: float,
                                   df: pd.DataFrame) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(swing_l) < 3 or atr <= 0:
        return out
    left, head, right = swing_l[-3], swing_l[-2], swing_l[-1]
    if not (head.price < left.price and head.price < right.price):
        return out
    if not _approx_equal(left.price, right.price, 0.4, atr):
        return out
    shoulder_mean = (left.price + right.price) / 2.0
    if shoulder_mean - head.price < 0.5 * atr:
        return out
    lh_mask = (df.index > left.ts) & (df.index < head.ts)
    hr_mask = (df.index > head.ts) & (df.index < right.ts)
    left_peak = df.loc[lh_mask, "High"].max()
    right_peak = df.loc[hr_mask, "High"].max()
    if pd.isna(left_peak) or pd.isna(right_peak):
        return out
    neckline = float((left_peak + right_peak) / 2.0)
    depth = neckline - head.price
    if depth < atr * 1.0:
        return out
    target = neckline + depth
    invalidation = head.price - 0.25 * atr
    shoulder_err = abs(left.price - right.price) / max(atr, 1e-9)
    conf = 0.55 + 0.3 * min(1.0, depth / (atr * 3)) - 0.3 * shoulder_err
    conf = float(max(0.0, min(1.0, conf)))
    out.append(ChartPattern(
        name="inverse_head_shoulders",
        direction="bullish",
        confidence=conf,
        target=target,
        invalidation=invalidation,
        anchor_ts=right.ts,
        detail=(
            f"Inverse H&S: shoulders ~{shoulder_mean:.5f}, head "
            f"{head.price:.5f}, neckline {neckline:.5f}, "
            f"depth {depth / atr:.1f}× ATR."
        ),
    ))
    return out


# ---------------------------------------------------------------------------
# 3. Triangles + wedges.
# ---------------------------------------------------------------------------
def _detect_triangles_wedges(swing_h: list, swing_l: list,
                             df: pd.DataFrame, atr: float) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(swing_h) < 2 or len(swing_l) < 2 or atr <= 0 or len(df) < 40:
        return out

    recent_high = swing_h[-4:] if len(swing_h) >= 4 else swing_h
    recent_low = swing_l[-4:] if len(swing_l) >= 4 else swing_l

    def _idx_of(ts: pd.Timestamp) -> int:
        try:
            return int(df.index.get_loc(ts))
        except Exception:
            return -1

    hi_pts = [(i, p) for i, p in ((_idx_of(s.ts), s.price) for s in recent_high) if i >= 0]
    lo_pts = [(i, p) for i, p in ((_idx_of(s.ts), s.price) for s in recent_low) if i >= 0]
    if len(hi_pts) < 2 or len(lo_pts) < 2:
        return out

    hi_slope, hi_int, hi_r2 = _fit_line(hi_pts)
    lo_slope, lo_int, lo_r2 = _fit_line(lo_pts)
    # Normalise slope to "price per bar". We want direction comparisons
    # rather than absolute magnitudes.
    hi_flat = abs(hi_slope) * 10 < atr    # roughly horizontal
    lo_flat = abs(lo_slope) * 10 < atr
    hi_up = hi_slope > 0
    lo_up = lo_slope > 0
    lines_converging = (hi_slope < 0 and lo_slope > 0)
    lines_rising_wedge = (hi_slope > 0 and lo_slope > 0 and lo_slope > hi_slope)
    lines_falling_wedge = (hi_slope < 0 and lo_slope < 0 and hi_slope < lo_slope)

    anchor_ts = df.index[-1]

    # Ascending triangle — flat top, rising bottom
    if hi_flat and lo_up:
        flat_price = np.mean([p for _, p in hi_pts])
        depth = flat_price - np.mean([p for _, p in lo_pts])
        if depth > 0.5 * atr:
            out.append(ChartPattern(
                name="ascending_triangle", direction="bullish",
                confidence=min(1.0, 0.45 + 0.3 * lo_r2),
                target=float(flat_price + depth),
                invalidation=float(flat_price - 0.5 * atr);
                anchor_ts=anchor_ts,
                detail=(
                    f"Ascending triangle: flat resistance ~{flat_price:.5f}, "
                    f"rising support. Bullish breakout expected."
                ),
            ))

    # Descending triangle — flat bottom, falling top
    if lo_flat and not hi_up:
        flat_price = np.mean([p for _, p in lo_pts])
        depth = np.mean([p for _, p in hi_pts]) - flat_price
        if depth > 0.5 * atr:
            out.append(ChartPattern(
                name="descending_triangle", direction="bearish",
                confidence=min(1.0, 0.45 + 0.3 * hi_r2),
                target=float(flat_price - depth),
                invalidation=float(flat_price + 0.5 * atr),
                anchor_ts=anchor_ts,
                detail=(
                    f"Descending triangle: flat support ~{flat_price:.5f}, "
                    f"falling resistance. Bearish breakout expected."
                ),
            ))

    # Symmetric triangle — converging lines
    if lines_converging and not (hi_flat or lo_flat):
        pt_now_hi = hi_slope * (len(df) - 1) + hi_int
        pt_now_lo = lo_slope * (len(df) - 1) + lo_int
        depth = pt_now_hi - pt_now_lo
        # Direction inferred from which boundary price is closer to
        last_close = float(df["Close"].iloc[-1])
        direction = "bullish" if (last_close - pt_now_lo) < (pt_now_hi - last_close) else "bearish"
        target = pt_now_hi + depth if direction == "bullish" else pt_now_lo - depth
        invalidation = pt_now_lo - 0.5 * atr if direction == "bullish" else pt_now_hi + 0.5 * atr
        if depth > 0.4 * atr:
            out.append(ChartPattern(
                name="symmetric_triangle", direction=direction,
                confidence=min(1.0, 0.4 + 0.3 * (hi_r2 + lo_r2) / 2),
                target=float(target),
                invalidation=float(invalidation),
                anchor_ts=anchor_ts,
                detail=(
                    f"Symmetric triangle: converging trendlines, "
                    f"current range {depth / atr:.1f}× ATR. "
                    f"Breakout direction unknown — watch."
                ),
            ))

    # Rising wedge — bearish
    if lines_rising_wedge:
        out.append(ChartPattern(
            name="rising_wedge", direction="bearish",
            confidence=min(1.0, 0.45 + 0.25 * (hi_r2 + lo_r2) / 2),
            target=None, invalidation=None, anchor_ts=anchor_ts,
            detail=(
                "Rising wedge: both trendlines rising but support rising "
                "faster than resistance. Typically resolves bearishly."
            ),
        ))

    # Falling wedge — bullish
    if lines_falling_wedge:
        out.append(ChartPattern(
            name="falling_wedge", direction="bullish",
            confidence=min(1.0, 0.45 + 0.25 * (hi_r2 + lo_r2) / 2),
            target=None, invalidation=None, anchor_ts=anchor_ts,
            detail=(
                "Falling wedge: both trendlines falling but resistance "
                "falling faster than support. Typically resolves bullishly."
            ),
        ))

    return out


# ---------------------------------------------------------------------------
# 4. Flag / pennant — brief counter-trend consolidation after a strong move.
# ---------------------------------------------------------------------------
def _detect_flag(df: pd.DataFrame, atr: float,
                 trend_direction: str) -> list[ChartPattern]:
    out: list[ChartPattern] = []
    if len(df) < 30 or atr <= 0 or trend_direction not in ("up", "down"):
        return out
    # Pole detection: a strong run in the last 20-40 bars (before the
    # consolidation portion).
    flag_bars = 10
    pole_bars = 20
    if len(df) < pole_bars + flag_bars:
        return out
    pole_slice = df.iloc[-(pole_bars + flag_bars): -flag_bars]
    flag_slice = df.iloc[-flag_bars:]
    # Pole size
    pole_range = pole_slice["Close"].iloc[-1] - pole_slice["Close"].iloc[0]
    # Flag range
    flag_hi = float(flag_slice["High"].max())
    flag_lo = float(flag_slice["Low"].min())
    flag_range = flag_hi - flag_lo

    # Bull flag: up pole + narrow sideways/slightly down channel
    if trend_direction == "up" and pole_range > 3 * atr and flag_range < 1.5 * atr:
        depth_ratio = flag_range / abs(pole_range)
        if depth_ratio < 0.5:
            target = flag_hi + abs(pole_range)
            invalidation = flag_lo - 0.3 * atr
            out.append(ChartPattern(
                name="bull_flag", direction="bullish",
                confidence=min(1.0, 0.55 + 0.25 * (1.0 - depth_ratio)),
                target=float(target), invalidation=float(invalidation),
                anchor_ts=df.index[-1],
                detail=(
                    f"Bull flag after {pole_range / atr:.1f}× ATR pole, "
                    f"consolidating in {flag_range / atr:.1f}× ATR range."
                ),
            ))

    # Bear flag: down pole + narrow sideways/slightly up channel
    if trend_direction == "down" and pole_range < -3 * atr and flag_range < 1.5 * atr:
        depth_ratio = flag_range / abs(pole_range)
        if depth_ratio < 0.5:
            target = flag_lo - abs(pole_range)
            invalidation = flag_hi + 0.3 * atr
            out.append(ChartPattern(
                name="bear_flag", direction="bearish",
                confidence=min(1.0, 0.55 + 0.25 * (1.0 - depth_ratio)),
                target=float(target), invalidation=float(invalidation),
                anchor_ts=df.index[-1],
                detail=(
                    f"Bear flag after {abs(pole_range) / atr:.1f}× ATR pole, "
                    f"consolidating in {flag_range / atr:.1f}× ATR range."
                ),
            ))

    return out


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------
def detect_chart_patterns(
    df: pd.DataFrame,
    atr_series: pd.Series,
    swing_highs: list,
    swing_lows: list,
    trend_direction: str,
) -> list[ChartPattern]:
    """Run every pattern detector and return sorted by anchor_ts desc.

    Inputs:
      df            — OHLCV dataframe (tz-aware UTC index)
      atr_series    — ATR series aligned to df.index
      swing_highs   — list of SwingPoint (from ChartReading)
      swing_lows    — list of SwingPoint (from ChartReading)
      trend_direction — "up" | "down" | "flat"
    """
    if df is None or len(df) < 40:
        return []
    atr_last = float(atr_series.iloc[-1]) if len(atr_series) else 0.0
    if atr_last <= 0 or not np.isfinite(atr_last):
        return []

    patterns: list[ChartPattern] = []
    patterns += _detect_double_top(swing_highs, atr_last, df)
    patterns += _detect_double_bottom(swing_lows, atr_last, df)
    patterns += _detect_head_shoulders(swing_highs, atr_last, df)
    patterns += _detect_inverse_head_shoulders(swing_lows, atr_last, df)
    patterns += _detect_triangles_wedges(swing_highs, swing_lows, df, atr_last)
    patterns += _detect_flag(df, atr_last, trend_direction)

    patterns.sort(key=lambda p: p.anchor_ts, reverse=True)
    return patterns
