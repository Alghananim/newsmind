# -*- coding: utf-8 -*-
"""Trap / trick detection — Phase A of the deepening plan.

A seasoned trader's edge comes as much from knowing when the market
is LYING as from knowing when to trade. This module detects the six
canonical deception patterns that appear on every intraday chart:

    1. Liquidity Grab        — stop-hunt at recent swing extreme
    2. Spring (Wyckoff)      — false break below support, quick recovery
    3. Upthrust (Wyckoff)    — false break above resistance, quick rejection
    4. Failed Breakout       — close beyond range, bars later back inside
    5. Judas Swing (ICT)     — manipulation move at session start
    6. Stop Hunt @ Round #   — spike into / rejection from round number

Each detection produces a Trap record with direction (bullish/bearish
— i.e. what the trap IMPLIES, not the direction of the fake move),
strength 0..1, timestamp of the offending bar, and a human-readable
explanation. Downstream layers (Confluence, Clarity, Narrative) use
these records to refine their decisions — a bullish spring near
strong support is one of the highest-conviction LONG setups.

All detections are pure-Python, look-ahead-free (scan only past
bars), and take O(n) time on the supplied window.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Output record.
# ---------------------------------------------------------------------------
@dataclass
class Trap:
    """A detected trap / fake move."""
    name: str                    # "spring" | "upthrust" | "liquidity_grab_low" | ...
    ts: pd.Timestamp             # timestamp of the bar where the trap completed
    direction: str               # "bullish" | "bearish" — what the trap implies
    strength: float              # 0..1 — structural size + speed of recovery
    detail: str                  # one-line description for narrative

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(d.get("ts"), pd.Timestamp):
            d["ts"] = d["ts"].isoformat()
        return d


# ---------------------------------------------------------------------------
# Configuration defaults — exposed so callers can tune per pair / TF.
# ---------------------------------------------------------------------------
@dataclass
class TrapConfig:
    recent_window: int = 100             # bars to scan for recent swings
    swing_width: int = 3                 # fractal width for swing detection
    recovery_bars: int = 3               # max bars allowed for recovery
    min_strength: float = 0.30           # below this, trap is discarded
    session_start_bars: int = 4          # bars counted as "session start"
    round_number_grid: float = 0.0050    # 50 pips for EUR/USD
    min_pierce_atr: float = 0.20         # how far past the level counts as "pierced"


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------
def _swing_points(df: pd.DataFrame, width: int) -> tuple[list, list]:
    """Return (swing_highs, swing_lows) as lists of (index, price) tuples.
    A bar is a swing extreme if it dominates `width` bars on each side.
    """
    if len(df) < 2 * width + 1:
        return [], []
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    swing_h: list[tuple[int, float]] = []
    swing_l: list[tuple[int, float]] = []
    for i in range(width, len(df) - width):
        window_h = highs[i - width: i + width + 1]
        window_l = lows[i - width: i + width + 1]
        if highs[i] == window_h.max() and window_h.argmax() == width:
            swing_h.append((i, float(highs[i])))
        if lows[i] == window_l.min() and window_l.argmin() == width:
            swing_l.append((i, float(lows[i])))
    return swing_h, swing_l


def _nearest_round(price: float, grid: float) -> float:
    return round(price / grid) * grid


def _atr_at(atr_series: pd.Series, i: int) -> float:
    if i < 0 or i >= len(atr_series):
        return 0.0
    v = atr_series.iloc[i]
    return float(v) if pd.notna(v) else 0.0


# ---------------------------------------------------------------------------
# Detectors — one function per trap type. All return lists of Trap.
# ---------------------------------------------------------------------------
def _detect_spring(df: pd.DataFrame, atr_series: pd.Series,
                   cfg: TrapConfig) -> list[Trap]:
    """Spring: a recent significant support is pierced, then price
    closes back above within cfg.recovery_bars.

    Optimized with numpy arrays; avoids pandas `.iloc` inside the
    nested loops (which dominated profiling). Also de-duplicates: one
    spring per swing-low (first valid completion wins). This halves
    output noise vs the previous implementation without changing
    semantics on a per-swing basis.
    """
    out: list[Trap] = []
    swing_h, swing_l = _swing_points(
        df.iloc[-cfg.recent_window:], cfg.swing_width,
    )
    if not swing_l:
        return out
    window_len = len(df)
    offset = max(0, len(df) - cfg.recent_window)

    # Pull the columns we need as numpy arrays ONCE. Every subsequent
    # access is an array index — ~100x faster than df["col"].iloc[i].
    lows_np = df["Low"].to_numpy()
    closes_np = df["Close"].to_numpy()
    atr_np = atr_series.to_numpy() if hasattr(atr_series, "to_numpy") \
        else np.asarray(atr_series)
    index_np = df.index

    for idx_local, sup_price in swing_l:
        i_sup = idx_local + offset
        if i_sup >= window_len - 1:
            continue
        atr_val = atr_np[i_sup] if i_sup < len(atr_np) else 0.0
        if not np.isfinite(atr_val) or atr_val <= 0:
            atr_val = 1e-9
        min_pierce = cfg.min_pierce_atr * atr_val
        # Look at bars AFTER the support was made
        found = False
        for j in range(i_sup + 1, window_len):
            low_j = float(lows_np[j])
            pierced_by = sup_price - low_j
            if pierced_by < min_pierce:
                continue
            recover_until = min(window_len, j + cfg.recovery_bars + 1)
            for k in range(j, recover_until):
                close_k = float(closes_np[k])
                if close_k > sup_price:
                    depth_norm = min(1.0, pierced_by / (atr_val * 1.5))
                    speed = 1.0 / (k - j + 1)
                    strength = 0.4 + 0.4 * depth_norm + 0.2 * speed
                    if strength < cfg.min_strength:
                        break
                    out.append(Trap(
                        name="spring",
                        ts=index_np[k],
                        direction="bullish",
                        strength=min(1.0, strength),
                        detail=(
                            f"Spring: support {sup_price:.5f} pierced "
                            f"by {pierced_by * (10**4):.1f} pips, "
                            f"closed back above after {k - j + 1} bar(s)."
                        ),
                    ))
                    found = True
                    break
            if found:
                break  # one spring per swing_low
    return out


def _detect_upthrust(df: pd.DataFrame, atr_series: pd.Series,
                     cfg: TrapConfig) -> list[Trap]:
    """Upthrust: mirror of Spring. A recent resistance is pierced
    above, then price closes back below within recovery_bars.

    Same numpy-array optimization as _detect_spring. Also de-dupes:
    one upthrust per swing-high.
    """
    out: list[Trap] = []
    swing_h, _ = _swing_points(
        df.iloc[-cfg.recent_window:], cfg.swing_width,
    )
    if not swing_h:
        return out
    window_len = len(df)
    offset = max(0, len(df) - cfg.recent_window)

    highs_np = df["High"].to_numpy()
    closes_np = df["Close"].to_numpy()
    atr_np = atr_series.to_numpy() if hasattr(atr_series, "to_numpy") \
        else np.asarray(atr_series)
    index_np = df.index

    for idx_local, res_price in swing_h:
        i_res = idx_local + offset
        if i_res >= window_len - 1:
            continue
        atr_val = atr_np[i_res] if i_res < len(atr_np) else 0.0
        if not np.isfinite(atr_val) or atr_val <= 0:
            atr_val = 1e-9
        min_pierce = cfg.min_pierce_atr * atr_val
        found = False
        for j in range(i_res + 1, window_len):
            high_j = float(highs_np[j])
            pierced_by = high_j - res_price
            if pierced_by < min_pierce:
                continue
            recover_until = min(window_len, j + cfg.recovery_bars + 1)
            for k in range(j, recover_until):
                close_k = float(closes_np[k])
                if close_k < res_price:
                    depth_norm = min(1.0, pierced_by / (atr_val * 1.5))
                    speed = 1.0 / (k - j + 1)
                    strength = 0.4 + 0.4 * depth_norm + 0.2 * speed
                    if strength < cfg.min_strength:
                        break
                    out.append(Trap(
                        name="upthrust",
                        ts=index_np[k],
                        direction="bearish",
                        strength=min(1.0, strength),
                        detail=(
                            f"Upthrust: resistance {res_price:.5f} pierced "
                            f"by {pierced_by * (10**4):.1f} pips, "
                            f"closed back below after {k - j + 1} bar(s)."
                        ),
                    ))
                    found = True
                    break
            if found:
                break  # one upthrust per swing_high
    return out


def _detect_liquidity_grab(df: pd.DataFrame, atr_series: pd.Series,
                           cfg: TrapConfig) -> list[Trap]:
    """Liquidity grab: recent swing HIGH or LOW is taken out (wick
    only) on a single bar and price closes back inside. The key
    distinction from Spring/Upthrust is that only the WICK exceeds the
    level — the close is already back inside the same bar.

    Same numpy-arrays optimization as spring/upthrust.
    """
    out: list[Trap] = []
    swing_h, swing_l = _swing_points(
        df.iloc[-cfg.recent_window:], cfg.swing_width,
    )
    window_len = len(df)
    offset = max(0, len(df) - cfg.recent_window)

    highs_np = df["High"].to_numpy()
    lows_np = df["Low"].to_numpy()
    closes_np = df["Close"].to_numpy()
    atr_np = atr_series.to_numpy() if hasattr(atr_series, "to_numpy") \
        else np.asarray(atr_series)
    index_np = df.index

    # Scan last 10 bars for grabs
    scan_start = max(0, window_len - 10)
    for j in range(scan_start, window_len):
        low_j = float(lows_np[j])
        high_j = float(highs_np[j])
        close_j = float(closes_np[j])
        atr_val = atr_np[j] if j < len(atr_np) else 0.0
        if not np.isfinite(atr_val) or atr_val <= 0:
            atr_val = 1e-9
        # Low grab — wick pierced a prior swing low but close is above
        for idx_local, sup in swing_l:
            i_sup = idx_local + offset
            if i_sup >= j:
                continue
            if low_j < sup and close_j > sup:
                pierced = sup - low_j
                if pierced < cfg.min_pierce_atr * atr_val:
                    continue
                depth_norm = min(1.0, pierced / (atr_val * 1.0))
                strength = 0.5 + 0.5 * depth_norm
                if strength < cfg.min_strength:
                    continue
                out.append(Trap(
                    name="liquidity_grab_low",
                    ts=index_np[j],
                    direction="bullish",
                    strength=min(1.0, strength),
                    detail=(
                        f"Liquidity grab at swing low {sup:.5f} — "
                        f"wicked {pierced * (10**4):.1f} pips, closed back."
                    ),
                ))
                break   # only count first (nearest) grab per bar
        # High grab
        for idx_local, res in swing_h:
            i_res = idx_local + offset
            if i_res >= j:
                continue
            if high_j > res and close_j < res:
                pierced = high_j - res
                if pierced < cfg.min_pierce_atr * atr_val:
                    continue
                depth_norm = min(1.0, pierced / (atr_val * 1.0))
                strength = 0.5 + 0.5 * depth_norm
                if strength < cfg.min_strength:
                    continue
                out.append(Trap(
                    name="liquidity_grab_high",
                    ts=index_np[j],
                    direction="bearish",
                    strength=min(1.0, strength),
                    detail=(
                        f"Liquidity grab at swing high {res:.5f} — "
                        f"wicked {pierced * (10**4):.1f} pips, closed back."
                    ),
                ))
                break
    return out


def _detect_failed_breakout(df: pd.DataFrame, atr_series: pd.Series,
                            cfg: TrapConfig) -> list[Trap]:
    """Failed breakout: take the last 20-bar range. Look for a CLOSE
    that exceeded the range, then a subsequent close back inside the
    range within cfg.recovery_bars. The direction flips: a failed
    upside breakout implies bearish bias.
    """
    out: list[Trap] = []
    if len(df) < 25:
        return out
    range_bars = 20
    anchor = df.iloc[-(range_bars + cfg.recovery_bars + 3): -(cfg.recovery_bars + 2)]
    if len(anchor) < 5:
        return out
    r_high = float(anchor["High"].max())
    r_low = float(anchor["Low"].min())
    # Scan last cfg.recovery_bars + 2 bars for breakout then return
    tail = df.iloc[-(cfg.recovery_bars + 3):]
    if len(tail) < 3:
        return out
    closes = tail["Close"].to_numpy()
    # Upside failure
    breakout_idx: Optional[int] = None
    for i, c in enumerate(closes):
        if c > r_high:
            breakout_idx = i
            break
    if breakout_idx is not None:
        for j in range(breakout_idx + 1, len(closes)):
            if closes[j] < r_high:
                # failed breakout upward
                atr = _atr_at(atr_series, len(df) - 1) or 1e-9
                penetration = closes[breakout_idx] - r_high
                norm = min(1.0, penetration / (atr * 1.0))
                speed = 1.0 / (j - breakout_idx + 1)
                strength = 0.35 + 0.4 * norm + 0.25 * speed
                if strength >= cfg.min_strength:
                    out.append(Trap(
                        name="failed_breakout_up",
                        ts=tail.index[j],
                        direction="bearish",
                        strength=min(1.0, strength),
                        detail=(
                            f"Failed upward breakout of range "
                            f"{r_low:.5f}–{r_high:.5f}; reversed after "
                            f"{j - breakout_idx + 1} bar(s)."
                        ),
                    ))
                break
    # Downside failure
    breakout_idx = None
    for i, c in enumerate(closes):
        if c < r_low:
            breakout_idx = i
            break
    if breakout_idx is not None:
        for j in range(breakout_idx + 1, len(closes)):
            if closes[j] > r_low:
                atr = _atr_at(atr_series, len(df) - 1) or 1e-9
                penetration = r_low - closes[breakout_idx]
                norm = min(1.0, penetration / (atr * 1.0))
                speed = 1.0 / (j - breakout_idx + 1)
                strength = 0.35 + 0.4 * norm + 0.25 * speed
                if strength >= cfg.min_strength:
                    out.append(Trap(
                        name="failed_breakout_down",
                        ts=tail.index[j],
                        direction="bullish",
                        strength=min(1.0, strength),
                        detail=(
                            f"Failed downward breakout of range "
                            f"{r_low:.5f}–{r_high:.5f}; reversed after "
                            f"{j - breakout_idx + 1} bar(s)."
                        ),
                    ))
                break
    return out


def _detect_judas_swing(df: pd.DataFrame, atr_series: pd.Series,
                        cfg: TrapConfig,
                        session_of) -> list[Trap]:
    """Judas swing: within the first few bars of a London or NY session,
    an initial strong move is reversed by the end of the opening
    window. Signals institutional manipulation.

    `session_of(ts)` is a callable that returns the session label for a
    timestamp — we pass the existing ChartMind session helper at call
    site, so the logic stays DRY.
    """
    out: list[Trap] = []
    if len(df) < 20:
        return out

    # Walk bars; group into runs of same session; take first N bars
    # of each run and check if the move was reversed.
    scan = df.iloc[-80:] if len(df) > 80 else df
    ts = scan.index
    sess = [session_of(t) for t in ts]
    highs = scan["High"].to_numpy()
    lows = scan["Low"].to_numpy()
    closes = scan["Close"].to_numpy()
    opens = scan["Open"].to_numpy()

    i = 0
    while i < len(scan):
        # Skip to session start (transition into london / ny_am / ny_pm)
        if sess[i] not in ("london", "ny_am", "ny_pm"):
            i += 1
            continue
        if i > 0 and sess[i - 1] == sess[i]:
            i += 1
            continue
        # Found session start at index i — examine next N bars
        end = min(i + cfg.session_start_bars, len(scan))
        first_open = opens[i]
        init_high = max(highs[i:end])
        init_low = min(lows[i:end])
        init_close = closes[end - 1]
        # Up-first then reversed down
        up_move = init_high - first_open
        down_move = first_open - init_close
        atr = _atr_at(atr_series, len(df) - len(scan) + i) or 1e-9
        if up_move > 0.6 * atr and down_move > 0.4 * atr:
            strength = min(1.0, 0.4 + 0.6 * down_move / (atr * 2))
            if strength >= cfg.min_strength:
                out.append(Trap(
                    name="judas_swing_bearish",
                    ts=scan.index[end - 1],
                    direction="bearish",
                    strength=strength,
                    detail=(
                        f"Judas swing: {sess[i]} opened up "
                        f"{up_move * 10**4:.1f} pips then reversed "
                        f"{down_move * 10**4:.1f} pips."
                    ),
                ))
        # Down-first then reversed up
        down_first = first_open - init_low
        up_back = init_close - first_open
        if down_first > 0.6 * atr and up_back > 0.4 * atr:
            strength = min(1.0, 0.4 + 0.6 * up_back / (atr * 2))
            if strength >= cfg.min_strength:
                out.append(Trap(
                    name="judas_swing_bullish",
                    ts=scan.index[end - 1],
                    direction="bullish",
                    strength=strength,
                    detail=(
                        f"Judas swing: {sess[i]} opened down "
                        f"{down_first * 10**4:.1f} pips then reversed "
                        f"{up_back * 10**4:.1f} pips."
                    ),
                ))
        # Advance past this session's start window
        i = end
    return out


def _detect_round_number_stophunt(df: pd.DataFrame, atr_series: pd.Series,
                                  cfg: TrapConfig) -> list[Trap]:
    """Stop hunt at round numbers: last few bars' wick touched (or
    briefly exceeded) a round-number grid point and price immediately
    rejected.

    Round-number psychology is documented in every microstructure text
    and shows up consistently in FX data.
    """
    out: list[Trap] = []
    if len(df) < 5:
        return out
    tail = df.iloc[-5:]
    for i in range(len(tail)):
        bar = tail.iloc[i]
        atr = _atr_at(atr_series, len(df) - len(tail) + i) or 1e-9
        high = float(bar["High"])
        low = float(bar["Low"])
        close = float(bar["Close"])
        near_high = _nearest_round(high, cfg.round_number_grid)
        near_low = _nearest_round(low, cfg.round_number_grid)

        # Upside stop-hunt: wick touched or pierced round number, close back below
        if abs(high - near_high) < 0.3 * atr and high >= near_high and close < near_high:
            pierced = max(0.0, high - near_high)
            if pierced >= 0.05 * atr:
                strength = min(1.0, 0.45 + 0.5 * pierced / atr)
                if strength >= cfg.min_strength:
                    out.append(Trap(
                        name="round_stophunt_high",
                        ts=tail.index[i],
                        direction="bearish",
                        strength=strength,
                        detail=(
                            f"Round-number stop-hunt at {near_high:.5f} — "
                            f"wicked {pierced * 10**4:.1f} pips, closed below."
                        ),
                    ))

        # Downside stop-hunt: wick touched or pierced round number, close back above
        if abs(low - near_low) < 0.3 * atr and low <= near_low and close > near_low:
            pierced = max(0.0, near_low - low)
            if pierced >= 0.05 * atr:
                strength = min(1.0, 0.45 + 0.5 * pierced / atr)
                if strength >= cfg.min_strength:
                    out.append(Trap(
                        name="round_stophunt_low",
                        ts=tail.index[i],
                        direction="bullish",
                        strength=strength,
                        detail=(
                            f"Round-number stop-hunt at {near_low:.5f} — "
                            f"wicked {pierced * 10**4:.1f} pips, closed above."
                        ),
                    ))
    return out


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------
def detect_traps(
    df: pd.DataFrame,
    atr_series: pd.Series,
    session_of,                    # callable(ts) -> session label
    cfg: Optional[TrapConfig] = None,
) -> list[Trap]:
    """Run every detector and return the combined list, de-duplicated
    by (name, timestamp), sorted newest first.

    `df` must be OHLCV indexed by timestamp.
    `atr_series` is the ATR series aligned to df (same index).
    `session_of(ts)` must return a session label; pass the one from
        ChartMind to keep single source of truth.
    """
    if cfg is None:
        cfg = TrapConfig()
    if df is None or len(df) < 10:
        return []
    atr = atr_series if atr_series is not None else pd.Series(
        [0.0] * len(df), index=df.index,
    )
    traps: list[Trap] = []
    traps += _detect_spring(df, atr, cfg)
    traps += _detect_upthrust(df, atr, cfg)
    traps += _detect_liquidity_grab(df, atr, cfg)
    traps += _detect_failed_breakout(df, atr, cfg)
    traps += _detect_judas_swing(df, atr, cfg, session_of)
    traps += _detect_round_number_stophunt(df, atr, cfg)
    # Deduplicate by (name, ts): keep strongest
    dedup: dict[tuple[str, pd.Timestamp], Trap] = {}
    for t in traps:
        key = (t.name, t.ts)
        if key not in dedup or dedup[key].strength < t.strength:
            dedup[key] = t
    # Sort newest-first
    return sorted(dedup.values(), key=lambda t: t.ts, reverse=True)
