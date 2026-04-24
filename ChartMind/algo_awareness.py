# -*- coding: utf-8 -*-
"""Algorithm Awareness — Phase G of the deepening plan.

The retail chart you look at is not drawn by humans. It is drawn by
algorithms: market-maker bots, liquidity-seeking HFT, statistical
arbitrageurs, and increasingly by machine-learning systems that adapt
to detectable patterns within seconds.

This module gives ChartMind a partial view of that machine layer.
Four signals:

    1. VWAP context — institutional algos frequently use Volume-Weighted
       Average Price (or tick-weighted in FX) as an anchor for both
       execution (TWAP/VWAP execution) and mean reversion. Distance
       from VWAP is information.

    2. Round-number zones — FX liquidity famously clusters at x.xx00
       and x.xx50. Stop orders pile up on the "wrong" side of these
       levels. Price approaching them has elevated stop-hunt risk.

    3. HFT footprint — when bar-to-bar ranges are unusually uniform,
       a single participant is likely making markets. A sudden large
       bar after such uniformity is an algo liquidating or a stop run.

    4. Combined warnings — the module produces explicit warnings the
       executor can fold into the clarity/abstain decision.

Sources:

    Harris (Trading & Exchanges) — VWAP as dealer benchmark; round-
    number clustering; distinction between liquidity provision and
    liquidity consumption.

    Aldridge (High-Frequency Trading: A Practical Guide) — HFT produces
    characteristic uniformity in bar ranges and inter-trade times;
    "burstiness" after uniformity indicates regime transitions.

    Osler (2003, "Currency Orders and Exchange-Rate Dynamics") —
    empirical evidence that stop orders cluster at round numbers in
    FX, producing predictable mean-reversion and stop-hunt patterns.

    Dalton (Mind Over Markets, Markets in Profile) — VWAP as fair
    value; deviation from VWAP as the primary mean-reversion signal
    in intraday trading.

    Huddleston / ICT — liquidity pools above swing highs and at round
    numbers are targeted before directional moves.

All code original Python. No copyrighted material reproduced.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, List, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Local column-normalization helper.
# Accepts both lowercase (close/high/low/volume) and TitleCase
# (Close/High/Low/Volume). Mirrors the normalizer in ChartMind.py so
# this module can be used standalone.
# ---------------------------------------------------------------------------
_OHLC_CANON = {
    "open": "Open", "high": "High", "low": "Low",
    "close": "Close", "volume": "Volume",
}


def _titlecase(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        return df
    rename = {}
    for c in df.columns:
        key = str(c).lower()
        if key in _OHLC_CANON and str(c) != _OHLC_CANON[key]:
            rename[c] = _OHLC_CANON[key]
    return df.rename(columns=rename) if rename else df


# ---------------------------------------------------------------------------
# Outputs.
# ---------------------------------------------------------------------------
@dataclass
class VWAPContext:
    """Where price sits relative to session VWAP."""
    vwap: float
    distance_pips: float            # current - vwap, in pips (signed)
    upper_1sigma: float
    lower_1sigma: float
    upper_2sigma: float
    lower_2sigma: float
    regime: str                     # "above_2s" "above_1s" "inside" "below_1s" "below_2s"
    mean_reversion_pressure: float  # 0..1, grows with distance in sigmas


@dataclass
class RoundNumberZone:
    """A round-number level near current price."""
    price: float
    distance_pips: float            # abs distance to current
    strength: str                   # "major" (x.xx00) | "minor" (x.xx50)
    zone_low: float                 # the ±1 pip envelope
    zone_high: float
    stop_hunt_risk: float           # 0..1 — higher when closer + liquidity side


@dataclass
class AlgoFootprint:
    """Statistical fingerprint of algorithmic presence on recent bars."""
    uniform_candles: bool           # True if ranges are unusually uniform
    uniformity_score: float         # 0..1, higher = more uniform = more algo-like
    range_cv: float                 # coefficient of variation of last N bar ranges
    burst_detected: bool            # a sudden large bar after uniformity
    burst_bar_index: Optional[int]  # relative index of the bursting bar (-N..-1)
    interpretation: str             # plain text


@dataclass
class AlgoAwareness:
    """Complete algo-awareness reading."""
    vwap: Optional[VWAPContext]
    nearby_round_numbers: List[RoundNumberZone]
    footprint: AlgoFootprint
    warnings: List[str] = field(default_factory=list)
    alignment_factors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Config.
# ---------------------------------------------------------------------------
# Window for VWAP computation (bars). Conventional intraday is full
# session; for M15 scalping we use the most recent trading session
# approximation — last 96 bars (24h on M15).
_VWAP_WINDOW_BARS = 96

# How many bars back to look when detecting uniform ranges.
_UNIFORMITY_WINDOW = 12

# A coefficient of variation below this threshold counts as "uniform".
# Normal human-driven bars have CV of range typically 0.5–1.0.
# Below 0.30 is suspicious of algorithmic market-making.
_CV_UNIFORM_THRESHOLD = 0.30

# A bar whose range exceeds this multiple of the recent mean range is
# a "burst" — likely an algo stop run or regime transition.
_BURST_RANGE_MULT = 2.5

# Round numbers: within this many pips of price, consider it "nearby".
_ROUND_NEARBY_PIPS = 15.0

# ±1 pip envelope around a round number is the "zone".
_ROUND_ZONE_PIPS = 1.0


# ---------------------------------------------------------------------------
# VWAP computation.
# ---------------------------------------------------------------------------
def _compute_vwap(df: pd.DataFrame, window: int = _VWAP_WINDOW_BARS) -> Optional[VWAPContext]:
    """Rolling VWAP + sigma bands.

    For FX we typically lack true volume; most feeds report tick volume
    as `volume`. Tick volume is an accepted proxy (Osler 2003). If no
    volume column is present we fall back to equal weights (tick-based
    average), which still captures the "fair value" concept even if
    less informative than true-volume VWAP.

    Source: Dalton (Markets in Profile) for VWAP + value-area concepts.
    The 1σ and 2σ deviation bands are standard constructions from the
    typical-price residual standard deviation.
    """
    if df is None or len(df) < 10:
        return None

    sub = _titlecase(df.tail(window).copy())
    # Typical price
    if set(["High", "Low", "Close"]).issubset(sub.columns):
        tp = (sub["High"] + sub["Low"] + sub["Close"]) / 3.0
    else:
        tp = sub["Close"]

    if "Volume" in sub.columns and sub["Volume"].sum() > 0:
        w = sub["Volume"].astype(float).values
    else:
        w = np.ones(len(sub))

    vwap = float(np.sum(tp.values * w) / max(np.sum(w), 1e-9))

    # Residual dispersion used for σ bands.
    residual = tp.values - vwap
    sigma = float(np.std(residual, ddof=1)) if len(residual) > 1 else 0.0

    current_price = float(sub["Close"].iloc[-1])
    # Conversion in pips assumes EUR/USD-style 0.0001 pip. For JPY pairs
    # the caller should multiply, but we keep it generic here.
    pip = 0.0001
    dist_pips = (current_price - vwap) / pip

    upper_1 = vwap + sigma
    upper_2 = vwap + 2 * sigma
    lower_1 = vwap - sigma
    lower_2 = vwap - 2 * sigma

    if current_price >= upper_2:
        regime = "above_2s"
        pressure = 1.0
    elif current_price >= upper_1:
        regime = "above_1s"
        pressure = 0.6
    elif current_price <= lower_2:
        regime = "below_2s"
        pressure = 1.0
    elif current_price <= lower_1:
        regime = "below_1s"
        pressure = 0.6
    else:
        regime = "inside"
        pressure = 0.2

    return VWAPContext(
        vwap=vwap,
        distance_pips=dist_pips,
        upper_1sigma=upper_1,
        lower_1sigma=lower_1,
        upper_2sigma=upper_2,
        lower_2sigma=lower_2,
        regime=regime,
        mean_reversion_pressure=pressure,
    )


# ---------------------------------------------------------------------------
# Round-number detection.
# ---------------------------------------------------------------------------
def _nearby_round_numbers(
    current_price: float,
    pair_pip: float = 0.0001,
) -> List[RoundNumberZone]:
    """Find x.xx00 and x.xx50 levels within _ROUND_NEARBY_PIPS of price.

    Source: Osler (2003) — stop orders cluster at round numbers, and
    empirical FX data shows exchange-rate dynamics are distorted
    around these levels (intraday reversals near them).

    Stop-hunt risk formula:
        - Major (x.xx00) get full weight.
        - Minor (x.xx50) get 0.6.
        - Closer to current → higher risk (linear within the window).
    """
    pip = pair_pip
    # Step for major rounds: 100 pips (e.g., 1.0800 → 1.0900 on EUR/USD).
    # The "x.xx00" level is price rounded to 0.01 (two decimals below whole).
    # For EUR/USD 1.0800 is "major"; 1.0850 is "minor".

    # Construct candidate majors within ±200 pips.
    majors = []
    minors = []
    # For pip=0.0001: major grid = 0.0100 steps. Minor = mid-way.
    major_step = 100 * pip
    base = round(current_price / major_step) * major_step
    for offset in [-2, -1, 0, 1, 2]:
        m = base + offset * major_step
        majors.append(m)
        minors.append(m + major_step / 2)

    out: List[RoundNumberZone] = []
    for price in majors:
        dist_pips = abs(price - current_price) / pip
        if dist_pips <= _ROUND_NEARBY_PIPS:
            risk = _stop_hunt_risk("major", dist_pips)
            out.append(
                RoundNumberZone(
                    price=price,
                    distance_pips=dist_pips,
                    strength="major",
                    zone_low=price - _ROUND_ZONE_PIPS * pip,
                    zone_high=price + _ROUND_ZONE_PIPS * pip,
                    stop_hunt_risk=risk,
                )
            )
    for price in minors:
        dist_pips = abs(price - current_price) / pip
        if dist_pips <= _ROUND_NEARBY_PIPS:
            risk = _stop_hunt_risk("minor", dist_pips)
            out.append(
                RoundNumberZone(
                    price=price,
                    distance_pips=dist_pips,
                    strength="minor",
                    zone_low=price - _ROUND_ZONE_PIPS * pip,
                    zone_high=price + _ROUND_ZONE_PIPS * pip,
                    stop_hunt_risk=risk,
                )
            )

    out.sort(key=lambda z: z.distance_pips)
    return out


def _stop_hunt_risk(strength: str, dist_pips: float) -> float:
    """Scale 0..1 — closer + major = higher risk."""
    weight = 1.0 if strength == "major" else 0.6
    closeness = max(0.0, 1.0 - dist_pips / _ROUND_NEARBY_PIPS)
    return round(weight * closeness, 3)


# ---------------------------------------------------------------------------
# HFT footprint (uniformity + burst).
# ---------------------------------------------------------------------------
def _algo_footprint(
    df: pd.DataFrame,
    window: int = _UNIFORMITY_WINDOW,
) -> AlgoFootprint:
    """Detect uniform-range regimes and post-uniform bursts.

    Method:
        1. Take last `window` completed bar ranges (high - low).
        2. Compute coefficient of variation (CV = std / mean).
        3. CV below threshold → "uniform" (algo-like).
        4. If the most recent bar's range exceeds mean × _BURST_RANGE_MULT
           while the preceding window was uniform → "burst" detected.

    Source: Aldridge (HFT: A Practical Guide) — steady market-making
    produces uniform bar ranges; regime transitions appear as bursts.

    Caveats:
        - Works best on M1/M5 where HFT is most visible. On M15 the
          signal is weaker but still informative for London/NY opens.
        - A "burst" is diagnostic, not directional — caller must
          combine with reading.direction.
    """
    if df is None or len(df) < window + 2:
        return AlgoFootprint(
            uniform_candles=False,
            uniformity_score=0.0,
            range_cv=0.0,
            burst_detected=False,
            burst_bar_index=None,
            interpretation="insufficient_bars",
        )

    df = _titlecase(df)
    # Ranges exclude the very last bar (used for burst check).
    prior = df.iloc[-(window + 1):-1]
    ranges = (prior["High"] - prior["Low"]).values
    mean_r = float(np.mean(ranges))
    std_r = float(np.std(ranges, ddof=1)) if len(ranges) > 1 else 0.0
    cv = std_r / mean_r if mean_r > 0 else 1.0

    uniform = cv < _CV_UNIFORM_THRESHOLD
    # Uniformity score: 1 at CV=0, 0 at CV=_CV_UNIFORM_THRESHOLD × 2.
    score = max(0.0, 1.0 - cv / (2 * _CV_UNIFORM_THRESHOLD))

    # Burst check: last bar range.
    last_range = float(df["High"].iloc[-1] - df["Low"].iloc[-1])
    burst = uniform and mean_r > 0 and (last_range / mean_r) >= _BURST_RANGE_MULT

    if burst:
        interp = (
            f"Uniform market-making regime broken by a burst bar "
            f"(range {last_range / mean_r:.1f}× mean). Likely algo "
            "liquidating, stop run, or news event."
        )
    elif uniform:
        interp = (
            f"Uniform bar ranges (CV={cv:.2f}) suggest algorithmic "
            "market-making. Counter-trend bets low-conviction; wait "
            "for burst or structural break."
        )
    else:
        interp = (
            f"Range variability normal (CV={cv:.2f}). No strong "
            "algo-uniformity signal."
        )

    return AlgoFootprint(
        uniform_candles=uniform,
        uniformity_score=round(score, 3),
        range_cv=round(cv, 3),
        burst_detected=burst,
        burst_bar_index=-1 if burst else None,
        interpretation=interp,
    )


# ---------------------------------------------------------------------------
# Main entry.
# ---------------------------------------------------------------------------
def read_algo_awareness(
    df: pd.DataFrame,
    direction: Optional[str] = None,
    pair_pip: float = 0.0001,
) -> AlgoAwareness:
    """Full algo-awareness reading.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain columns: high, low, close; volume optional.
    direction : Optional[str]
        If provided ("long" / "short"), warnings and alignment notes
        are specialized to that direction.
    pair_pip : float
        Pip size (0.0001 for EUR/USD, 0.01 for JPY pairs).

    Returns
    -------
    AlgoAwareness
    """
    df = _titlecase(df)
    vwap_ctx = _compute_vwap(df)
    current_price = float(df["Close"].iloc[-1])
    rounds = _nearby_round_numbers(current_price, pair_pip=pair_pip)
    footprint = _algo_footprint(df)

    warnings: List[str] = []
    aligned: List[str] = []

    # VWAP-driven notes.
    if vwap_ctx:
        if vwap_ctx.regime in ("above_2s", "below_2s"):
            warnings.append(
                f"price {vwap_ctx.regime.replace('_', ' ')} VWAP — "
                "strong mean-reversion pressure"
            )
        if direction == "long" and vwap_ctx.regime in ("above_1s", "above_2s"):
            warnings.append("long into overextended VWAP — fade risk")
        elif direction == "short" and vwap_ctx.regime in ("below_1s", "below_2s"):
            warnings.append("short into oversold VWAP — fade risk")
        elif direction == "long" and vwap_ctx.regime in ("below_1s", "below_2s"):
            aligned.append("long from below-VWAP = mean-reversion tailwind")
        elif direction == "short" and vwap_ctx.regime in ("above_1s", "above_2s"):
            aligned.append("short from above-VWAP = mean-reversion tailwind")

    # Round-number-driven notes.
    if rounds:
        closest = rounds[0]
        if closest.stop_hunt_risk >= 0.5:
            warnings.append(
                f"{closest.strength} round-number {closest.price:.5f} "
                f"within {closest.distance_pips:.1f} pips — expect stop hunt"
            )
        # Direction-sensitive: if direction=long and round just above,
        # elevated risk of sweep-through.
        if direction == "long":
            for z in rounds:
                if z.price > current_price and z.distance_pips <= 5:
                    warnings.append(
                        f"long target area includes round {z.price:.5f} — "
                        "liquidity pool above likely to be taken"
                    )
                    break
        elif direction == "short":
            for z in rounds:
                if z.price < current_price and z.distance_pips <= 5:
                    warnings.append(
                        f"short target area includes round {z.price:.5f} — "
                        "liquidity pool below likely to be taken"
                    )
                    break

    # Footprint-driven notes.
    if footprint.burst_detected:
        warnings.append(
            "burst after uniform regime — possible stop run or news; "
            "wait one bar for direction confirmation"
        )
    elif footprint.uniform_candles:
        warnings.append(
            "uniform algo market-making regime — avoid momentum entries"
        )

    return AlgoAwareness(
        vwap=vwap_ctx,
        nearby_round_numbers=rounds,
        footprint=footprint,
        warnings=warnings,
        alignment_factors=aligned,
    )
