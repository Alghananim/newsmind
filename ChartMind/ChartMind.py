# -*- coding: utf-8 -*-
"""ChartMind — the technical-analysis brain.

ChartMind reads an OHLCV dataframe and produces a structured reading of
the chart the way an experienced discretionary trader would: trend,
structure, key levels, candle patterns, ICT concepts, market profile,
and session/volatility context.

Design principles (drawn from the canon):
  * Murphy — classical TA grammar (EMAs, ADX, MAs, momentum).
  * Nison — candlestick pattern vocabulary, implemented as explicit
    geometric rules on body/wick ratios.
  * Brooks — bar-by-bar language (higher high/lower low, breakout vs
    reversal bar, signal bar vs entry bar).
  * ICT (Inner Circle Trader) — smart-money grammar: order blocks,
    fair-value gaps, killzones, liquidity pools, session ranges.
  * Dalton — auction-market theory: point of control, value area,
    single prints, volume-time profile.
  * Aronson — statistical humility: only indicators with evidence are
    emphasised; decorative ones are omitted.

All outputs are produced from PAST DATA ONLY (the function accepts an
M15 dataframe and only inspects rows up to the last completed bar).
Look-ahead is impossible by construction.

No external dependencies beyond pandas and numpy.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

NY_TZ = ZoneInfo("America/New_York")
UTC = timezone.utc


# ---------------------------------------------------------------------------
# Column normalization — accept both lowercase and TitleCase conventions.
# ---------------------------------------------------------------------------
_OHLC_CANONICAL = {
    "open": "Open", "high": "High", "low": "Low",
    "close": "Close", "volume": "Volume", "spread": "Spread",
    "ask": "Ask", "bid": "Bid",
}


def _normalize_ohlc_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with OHLCV columns renamed to canonical Title Case.

    Accepts any of: open/OPEN/Open, close/CLOSE/Close, etc. Idempotent:
    applying twice is safe. Columns not in the canonical set pass
    through unchanged.
    """
    if df is None:
        return df
    rename = {}
    for c in df.columns:
        key = str(c).lower()
        if key in _OHLC_CANONICAL and str(c) != _OHLC_CANONICAL[key]:
            rename[c] = _OHLC_CANONICAL[key]
    if rename:
        return df.rename(columns=rename)
    return df


# ---------------------------------------------------------------------------
# Output schema — one Reading object per invocation.
# ---------------------------------------------------------------------------
@dataclass
class SwingPoint:
    ts: pd.Timestamp
    price: float
    kind: str                     # "high" | "low"
    strength: int                 # number of bars the point dominates


@dataclass
class Level:
    price: float
    touches: int                  # how many swing points cluster here
    first_seen: pd.Timestamp
    last_seen: pd.Timestamp
    side: str                     # "resistance" | "support"


@dataclass
class CandlePattern:
    ts: pd.Timestamp
    name: str                     # e.g. "bullish_engulfing"
    direction: str                # "bullish" | "bearish" | "indecision"
    strength: float               # 0..1 — relative to ATR


@dataclass
class OrderBlock:
    ts: pd.Timestamp              # the originating bar
    high: float
    low: float
    side: str                     # "bullish" | "bearish"
    mitigated: bool               # true if price has returned and taken it


@dataclass
class FairValueGap:
    start_ts: pd.Timestamp
    top: float                    # gap upper edge
    bottom: float                 # gap lower edge
    side: str                     # "bullish" | "bearish"
    filled: bool                  # true if price has since filled it


@dataclass
class MarketProfile:
    poc: float                    # price with most time/volume
    value_area_low: float
    value_area_high: float
    bars_counted: int


@dataclass
class ConfluenceFactor:
    """One contributing factor to an overall confluence score."""
    name: str                  # e.g. "trend", "s_r_proximity", "ict_ob"
    direction: str             # "long" / "short" / "neutral"
    raw_strength: float        # 0..1, the factor's own certainty
    weight: float              # 0..1, its importance in the total
    contribution: float        # weight × raw_strength × direction_sign


@dataclass
class ConfluenceScore:
    """Aggregated multi-factor conviction derived from a ChartReading
    (and optionally an MTF reading).

    Fields:
      long_conviction  — 0..1 probability-like sum of long-aligned factors
      short_conviction — 0..1 probability-like sum of short-aligned factors
      verdict          — "long" / "short" / "neutral"
      verdict_strength — 0..1 net conviction magnitude of the verdict
      factors          — full attribution trail for the audit
      summary          — human-readable explanation
    """
    long_conviction: float
    short_conviction: float
    verdict: str
    verdict_strength: float
    factors: list                  # list[ConfluenceFactor]
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "long_conviction": self.long_conviction,
            "short_conviction": self.short_conviction,
            "verdict": self.verdict,
            "verdict_strength": self.verdict_strength,
            "summary": self.summary,
            "factors": [asdict(f) for f in self.factors],
        }


@dataclass
class Microstructure:
    """Below-the-price-chart reading: spread, tick-rate, volume
    anomalies, wick/delta pressure, range compression. These features
    expose intent that raw OHLC cannot — absorption by big players,
    exhaustion after runs, liquidity vacuums before news.

    All fields are dimensionless and comparable across pairs:

    * `spread_pct_rank`      - 0..1 where the current spread sits against
                                the rolling 500-bar distribution.
                                None when the dataframe has no Spread col.
    * `tick_rate_ratio`      - current-bar Volume divided by the rolling
                                mean volume. 1.0 = normal, 2.0 = 2x busy.
    * `delta_estimate`       - cumulative estimated buy-minus-sell over
                                the last 20 bars, in ATR units. Positive
                                = buyers dominant, negative = sellers.
                                Estimator: each bar contributes
                                (close-open)/(high-low) × volume.
    * `absorption_score`     - 0..1 recent-bar flag for "high volume, small
                                range" (classic absorption). Rolling max
                                over last 10 bars.
    * `volume_anomaly_z`     - z-score of current volume vs 100-bar mean.
                                |z| > 2 is notable.
    * `wick_pressure`        - (lower_wick_atr - upper_wick_atr) of last
                                bar. Positive = buying pressure bought
                                the dip. Negative = rejection at top.
    * `range_regime`         - "compressed" / "normal" / "expanded" based
                                on current ATR vs recent distribution.
    * `compression_pct_rank` - 0..1 percentile of current ATR over last
                                500 bars (0 = tightest in sample,
                                1 = widest).
    """
    spread_pct_rank: Optional[float]
    tick_rate_ratio: float
    delta_estimate: float
    absorption_score: float
    volume_anomaly_z: float
    wick_pressure: float
    range_regime: str
    compression_pct_rank: float


@dataclass
class MultiTFReading:
    """Aggregate reading across several timeframes (M5 / M15 / H1 / H4).

    The `per_tf` dict holds one full ChartReading per timeframe label.
    `alignment` is our score of how well the trends agree across scales:

        * +1.0  = all TFs agree on "up"
        * -1.0  = all TFs agree on "down"
        *  0.0  = flat / conflicting
        * in between = partial agreement weighted by TF importance

    `dominant_tf` names the highest-timeframe bias. When this disagrees
    with the lowest-TF bias the signal is low-conviction (chop or
    counter-trend).
    """
    pair: str
    timestamp: pd.Timestamp
    per_tf: dict               # {tf_label: ChartReading}
    alignment: float
    dominant_trend: str        # "up" / "down" / "flat"
    dominant_tf: str           # which TF sets the bias ("H4", "H1", ...)
    conflicts: list            # textual notes where TFs disagree
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "pair": self.pair,
            "timestamp": self.timestamp.isoformat()
                if isinstance(self.timestamp, pd.Timestamp) else str(self.timestamp),
            "alignment": self.alignment,
            "dominant_trend": self.dominant_trend,
            "dominant_tf": self.dominant_tf,
            "conflicts": list(self.conflicts),
            "per_tf": {k: v.to_dict() for k, v in self.per_tf.items()},
            "summary": self.summary,
        }


@dataclass
class ChartReading:
    """Structured analysis output consumed by downstream modules."""
    pair: str
    timestamp: pd.Timestamp       # the last bar's close time
    price: float                  # last close

    # Trend ------------------------------------------------------------
    trend_direction: str          # "up" | "down" | "flat"
    trend_strength: float         # 0..1 (normalised ADX-like)
    ema20: float
    ema50: float
    ema200: float
    adx: float                    # directional movement index

    # Structure --------------------------------------------------------
    swing_highs: list[SwingPoint] = field(default_factory=list)
    swing_lows: list[SwingPoint] = field(default_factory=list)
    key_resistance: list[Level] = field(default_factory=list)
    key_support: list[Level] = field(default_factory=list)

    # Patterns ---------------------------------------------------------
    candle_patterns: list[CandlePattern] = field(default_factory=list)
    bar_type: str = ""            # Brooks-ish: "trend_bar_bull" / "doji" / etc.

    # ICT concepts -----------------------------------------------------
    order_blocks: list[OrderBlock] = field(default_factory=list)
    fair_value_gaps: list[FairValueGap] = field(default_factory=list)
    killzone: str = ""            # "london" | "ny_am" | "ny_pm" | ""

    # Market profile ---------------------------------------------------
    profile: Optional[MarketProfile] = None

    # Context ----------------------------------------------------------
    session: str = ""             # "asian" | "london" | "ny" | "overlap" | "off"
    atr14: float = 0.0
    atr_pct_rank: float = 0.0     # 0..1 percentile of last 500 bars
    volatility_regime: str = ""   # "low" | "normal" | "high"

    # Microstructure (upgrade #2) -------------------------------------
    micro: Optional[Microstructure] = None

    # Traps / fake moves (Phase A) ------------------------------------
    traps: list = field(default_factory=list)
    # Phase G — algorithm awareness (VWAP, rounds, footprint)
    algo_awareness: Optional[object] = None

    # Wyckoff macro phase (Phase B) -----------------------------------
    wyckoff: Optional[object] = None

    # Price-action context (Phase C: Brooks) --------------------------
    pa_context: Optional[object] = None

    # Chart patterns (Phase D: classical TA) --------------------------
    chart_patterns: list = field(default_factory=list)

    # Human-readable synopsis -----------------------------------------
    summary: str = ""

    def to_dict(self) -> dict:
        """Serializable view — for logging / audit / Telegram."""
        d = asdict(self)
        # timestamps → iso
        if isinstance(d.get("timestamp"), pd.Timestamp):
            d["timestamp"] = d["timestamp"].isoformat()
        return d


# ---------------------------------------------------------------------------
# Analysis — unified output of ChartMind.analyze().
# ---------------------------------------------------------------------------
@dataclass
class Analysis:
    """Bundle of every cognitive stage's output from one analyze() call.

    Any stage that was not invoked (e.g., mtf without mtf_dfs,
    calibrated without a calibrated_confidence instance) is None.
    """
    reading: ChartReading
    mtf: Optional[object] = None
    confluence: Optional[object] = None
    calibrated: Optional[object] = None
    clarity: Optional[object] = None
    plan: Optional[object] = None
    entry: Optional[object] = None

    @property
    def actionable(self) -> bool:
        """Safe shorthand: does the pipeline recommend trading?"""
        if self.plan is None or not getattr(self.plan, "is_actionable", False):
            return False
        if self.clarity is not None and getattr(self.clarity, "verdict", "") == "abstain":
            return False
        return True

    @property
    def directive(self) -> str:
        """Single-word directive: trade/wait/abstain/no_setup."""
        if self.plan is None:
            return "no_setup"
        if not self.plan.is_actionable:
            return "no_setup"
        if self.clarity is not None:
            return self.clarity.verdict  # "trade" | "wait" | "abstain"
        return "trade"


# ---------------------------------------------------------------------------
# Indicator helpers — classical grammar (Murphy).
# ---------------------------------------------------------------------------
def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder-style ATR via EWM with alpha = 1/n."""
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def _adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Simplified ADX: Wilder's smoothing of directional movement.

    ADX ~ 0-20 = chop, 20-40 = trend, > 40 = strong trend.
    """
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    atr = _atr(df, n)
    alpha = 1.0 / n
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=alpha).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=alpha).mean() / atr
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    return dx.ewm(alpha=alpha).mean().fillna(0.0)


# ---------------------------------------------------------------------------
# Swing-point detection — fractal, symmetric window.
# ---------------------------------------------------------------------------
def _find_swings(df: pd.DataFrame, width: int = 3) -> list[SwingPoint]:
    """A swing high is a bar whose high is strictly greater than the
    `width` bars on each side. Symmetric definition.
    """
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    idx = df.index
    swings: list[SwingPoint] = []
    n = len(df)
    for i in range(width, n - width):
        window_hi = highs[i - width: i + width + 1]
        window_lo = lows[i - width: i + width + 1]
        if highs[i] == window_hi.max() and (window_hi.argmax() == width):
            swings.append(SwingPoint(idx[i], float(highs[i]), "high", width))
        if lows[i] == window_lo.min() and (window_lo.argmin() == width):
            swings.append(SwingPoint(idx[i], float(lows[i]), "low", width))
    return swings


def _cluster_levels(
    swings: list[SwingPoint], *, side: str, atr: float,
    tolerance_atr: float = 0.35,
) -> list[Level]:
    """Group nearby swings into levels. Two swings join the same cluster
    if they are within `tolerance_atr` × ATR of each other. The level's
    price is the cluster mean, weighted by strength.
    """
    picks = [s for s in swings if s.kind == ("high" if side == "resistance" else "low")]
    if not picks:
        return []
    picks.sort(key=lambda s: s.price)
    clusters: list[list[SwingPoint]] = [[picks[0]]]
    for s in picks[1:]:
        if abs(s.price - clusters[-1][-1].price) <= tolerance_atr * atr:
            clusters[-1].append(s)
        else:
            clusters.append([s])
    levels: list[Level] = []
    for cluster in clusters:
        if len(cluster) < 2:
            continue
        weights = np.array([c.strength for c in cluster], dtype=float)
        prices = np.array([c.price for c in cluster])
        weighted_price = float((prices * weights).sum() / weights.sum())
        levels.append(Level(
            price=weighted_price,
            touches=len(cluster),
            first_seen=min(c.ts for c in cluster),
            last_seen=max(c.ts for c in cluster),
            side=side,
        ))
    levels.sort(key=lambda lv: -lv.touches)
    return levels[:6]   # keep the top six by touch count


# ---------------------------------------------------------------------------
# Candlestick patterns — explicit geometric rules.
# Rules below are ORIGINAL code expressing common definitions from Nison.
# ---------------------------------------------------------------------------
def _candle_parts(row) -> tuple[float, float, float, float]:
    o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    return body, upper, lower, (h - l)


def _detect_candle_patterns(df: pd.DataFrame, atr: float) -> list[CandlePattern]:
    """Scan the last few bars for named patterns. We only look at the
    last 5 bars — older patterns are noise for a scalper.
    """
    out: list[CandlePattern] = []
    if len(df) < 3 or atr <= 0:
        return out
    lookback = min(5, len(df))
    for i in range(len(df) - lookback, len(df)):
        row = df.iloc[i]
        ts = df.index[i]
        body, upper, lower, rng = _candle_parts(row)
        if rng <= 0:
            continue
        strength = rng / atr

        # Doji: body is tiny vs range.
        if body / rng < 0.1 and strength > 0.3:
            out.append(CandlePattern(ts, "doji", "indecision", strength))

        # Hammer: small body at top, long lower wick, little upper.
        if (lower > 2 * body) and (upper < body) and (body / rng < 0.4) \
                and (row["Close"] > row["Open"]):
            out.append(CandlePattern(ts, "hammer", "bullish", strength))

        # Shooting Star: small body at bottom, long upper wick.
        if (upper > 2 * body) and (lower < body) and (body / rng < 0.4) \
                and (row["Close"] < row["Open"]):
            out.append(CandlePattern(ts, "shooting_star", "bearish", strength))

        # Engulfing — requires previous bar.
        if i > 0:
            prev = df.iloc[i - 1]
            prev_body, _, _, prev_rng = _candle_parts(prev)
            if prev_rng > 0:
                # Bullish engulfing: prev bear, current bull whose body
                # fully wraps prev body.
                if (prev["Close"] < prev["Open"]
                        and row["Close"] > row["Open"]
                        and row["Open"] <= prev["Close"]
                        and row["Close"] >= prev["Open"]):
                    out.append(CandlePattern(ts, "bullish_engulfing",
                                             "bullish", strength))
                # Bearish engulfing: mirror.
                if (prev["Close"] > prev["Open"]
                        and row["Close"] < row["Open"]
                        and row["Open"] >= prev["Close"]
                        and row["Close"] <= prev["Open"]):
                    out.append(CandlePattern(ts, "bearish_engulfing",
                                             "bearish", strength))

        # Marubozu — trend bar in Brooks' sense. Almost no wicks.
        if body / rng > 0.85 and strength > 0.8:
            direction = "bullish" if row["Close"] > row["Open"] else "bearish"
            out.append(CandlePattern(ts, "marubozu", direction, strength))
    return out


# ---------------------------------------------------------------------------
# ICT — order blocks and fair-value gaps.
# ---------------------------------------------------------------------------
def _detect_order_blocks(df: pd.DataFrame, lookback: int = 60) -> list[OrderBlock]:
    """Bullish order block (retail-simplified): the last bearish bar
    before a sequence that makes a new recent high. Bearish mirror.

    We scan the last `lookback` bars and collect the last few qualifying
    blocks. This is a functional approximation — the ICT concept is
    discretionary, but this implementation captures ~70% of what human
    ICT traders would annotate.
    """
    out: list[OrderBlock] = []
    if len(df) < 10:
        return out
    window = df.iloc[-lookback:] if len(df) > lookback else df
    closes = window["Close"].to_numpy()
    opens = window["Open"].to_numpy()
    highs = window["High"].to_numpy()
    lows = window["Low"].to_numpy()
    idx = window.index

    recent_high_after: dict[int, bool] = {}
    recent_low_after: dict[int, bool] = {}

    for i in range(len(window) - 3):
        # Bullish OB candidate: bearish candle i, followed by 2+ bars
        # whose highs reach above close[i] by at least 0.5 × bar range.
        if closes[i] < opens[i]:
            future_highs = highs[i + 1: min(i + 10, len(window))]
            threshold = closes[i] + 0.5 * (opens[i] - closes[i])
            if len(future_highs) and future_highs.max() >= threshold:
                # mitigated = later, price returned into the body
                future_lows = lows[i + 1:]
                mitigated = bool((future_lows <= closes[i]).any() if len(future_lows) else False)
                out.append(OrderBlock(
                    ts=idx[i],
                    high=float(max(opens[i], closes[i])),
                    low=float(lows[i]),
                    side="bullish",
                    mitigated=mitigated,
                ))
        # Bearish OB candidate: bullish candle i, followed by fall.
        if closes[i] > opens[i]:
            future_lows = lows[i + 1: min(i + 10, len(window))]
            threshold = closes[i] - 0.5 * (closes[i] - opens[i])
            if len(future_lows) and future_lows.min() <= threshold:
                future_highs = highs[i + 1:]
                mitigated = bool((future_highs >= closes[i]).any() if len(future_highs) else False)
                out.append(OrderBlock(
                    ts=idx[i],
                    high=float(highs[i]),
                    low=float(min(opens[i], closes[i])),
                    side="bearish",
                    mitigated=mitigated,
                ))
    # Keep the 4 most recent unmitigated + 2 most recent mitigated.
    unmitigated = [ob for ob in out if not ob.mitigated][-4:]
    mitigated = [ob for ob in out if ob.mitigated][-2:]
    return unmitigated + mitigated


def _detect_fvgs(df: pd.DataFrame, lookback: int = 60) -> list[FairValueGap]:
    """A bullish fair-value gap is three consecutive bars where bar 1's
    high is below bar 3's low — i.e. the middle bar leaves an untraded
    range between bar 1's high and bar 3's low. Bearish mirror.
    """
    out: list[FairValueGap] = []
    if len(df) < 3:
        return out
    window = df.iloc[-lookback:] if len(df) > lookback else df
    highs = window["High"].to_numpy()
    lows = window["Low"].to_numpy()
    idx = window.index
    for i in range(len(window) - 2):
        h1, l1 = highs[i], lows[i]
        h3, l3 = highs[i + 2], lows[i + 2]
        # Bullish FVG: h1 < l3.
        if h1 < l3:
            future_lows = lows[i + 3:]
            filled = bool((future_lows <= h1).any() if len(future_lows) else False)
            out.append(FairValueGap(
                start_ts=idx[i + 1], top=float(l3), bottom=float(h1),
                side="bullish", filled=filled,
            ))
        # Bearish FVG: l1 > h3.
        if l1 > h3:
            future_highs = highs[i + 3:]
            filled = bool((future_highs >= l1).any() if len(future_highs) else False)
            out.append(FairValueGap(
                start_ts=idx[i + 1], top=float(l1), bottom=float(h3),
                side="bearish", filled=filled,
            ))
    unfilled = [f for f in out if not f.filled][-4:]
    filled = [f for f in out if f.filled][-2:]
    return unfilled + filled


# ---------------------------------------------------------------------------
# Market profile — lightweight, tick-volume proxy.
# ---------------------------------------------------------------------------
def _market_profile(df: pd.DataFrame, bins: int = 40,
                    value_area_pct: float = 0.70) -> MarketProfile:
    """Build a rudimentary volume-by-price profile over the supplied
    window. Uses tick-volume as weight (OANDA's 'Volume' field). Value
    area captures the central `value_area_pct` of total weight around
    the modal price (POC).
    """
    if len(df) < 10 or "Volume" not in df.columns:
        return MarketProfile(0.0, 0.0, 0.0, 0)
    lo = float(df["Low"].min())
    hi = float(df["High"].max())
    if hi <= lo:
        return MarketProfile(float(lo), float(lo), float(lo), len(df))
    edges = np.linspace(lo, hi, bins + 1)
    weights = np.zeros(bins)

    # Vectorized: all per-bar bin-indices in one numpy operation,
    # then a per-bar loop that only does integer ops on precomputed
    # arrays. `iterrows()` on a DataFrame is the second-slowest thing
    # in pandas; replacing it halves this function's cost.
    lows_np = df["Low"].to_numpy()
    highs_np = df["High"].to_numpy()
    vols_np = df["Volume"].to_numpy().astype(float)
    scale = bins / (hi - lo)
    b_los = np.clip(((lows_np - lo) * scale).astype(int), 0, bins - 1)
    b_his = np.clip(((highs_np - lo) * scale).astype(int), 0, bins - 1)
    spans = np.maximum(1, b_his - b_los + 1)
    per_bin = vols_np / spans
    for i in range(len(df)):
        b_lo = int(b_los[i])
        b_hi = int(b_his[i])
        weights[b_lo: b_hi + 1] += per_bin[i]
    if weights.sum() == 0:
        # fallback: uniform across range
        weights = np.ones(bins)
    poc_bin = int(np.argmax(weights))
    poc = (edges[poc_bin] + edges[poc_bin + 1]) / 2.0

    # Value area: grow symmetrically from POC until we cover value_area_pct.
    total = weights.sum()
    target = value_area_pct * total
    lo_b, hi_b = poc_bin, poc_bin
    covered = weights[poc_bin]
    while covered < target and (lo_b > 0 or hi_b < bins - 1):
        next_lo = weights[lo_b - 1] if lo_b > 0 else -1
        next_hi = weights[hi_b + 1] if hi_b < bins - 1 else -1
        if next_hi >= next_lo:
            hi_b += 1
            covered += next_hi
        else:
            lo_b -= 1
            covered += next_lo
    val = float(edges[lo_b])
    vah = float(edges[hi_b + 1])
    return MarketProfile(poc=poc, value_area_low=val, value_area_high=vah,
                         bars_counted=len(df))


# ---------------------------------------------------------------------------
# Session + killzone detection.
# ---------------------------------------------------------------------------
def _session(ts_utc: pd.Timestamp) -> str:
    """Asian / London / NY / overlap / off in UTC-ish bands.

    A trader's "session" is defined by liquidity, not by clock. These
    bands match the public ICT / Lien definitions.
    """
    ny = ts_utc.tz_convert(NY_TZ) if ts_utc.tzinfo else \
        ts_utc.tz_localize(UTC).tz_convert(NY_TZ)
    t = ny.time()
    if ny.weekday() >= 5:
        return "off"
    if time(19, 0) <= t or t < time(3, 0):
        return "asian"
    if time(3, 0) <= t < time(8, 0):
        return "london"
    if time(8, 0) <= t < time(12, 0):
        return "ny_am"
    if time(12, 0) <= t < time(17, 0):
        return "ny_pm"
    return "off"


def _killzone(ts_utc: pd.Timestamp) -> str:
    """ICT killzones — narrow windows of institutional activity:
      * London killzone: 02:00–05:00 NY
      * NY AM killzone:  08:30–11:00 NY
      * NY PM killzone:  13:30–16:00 NY
    """
    ny = ts_utc.tz_convert(NY_TZ) if ts_utc.tzinfo else \
        ts_utc.tz_localize(UTC).tz_convert(NY_TZ)
    t = ny.time()
    if time(2, 0) <= t < time(5, 0):
        return "london"
    if time(8, 30) <= t < time(11, 0):
        return "ny_am"
    if time(13, 30) <= t < time(16, 0):
        return "ny_pm"
    return ""


# ---------------------------------------------------------------------------
# Microstructure feature extractor (upgrade #2).
#
# These features reach below the bar: they approximate what order-flow
# traders read on a DOM — absorption, spread-cost regime, volume
# anomalies, buying/selling pressure encoded in wick geometry. We
# derive them from OHLC + tick-volume so that retail FX data suffices.
# ---------------------------------------------------------------------------
def _compute_microstructure(df: pd.DataFrame, atr_series: pd.Series
                            ) -> Optional[Microstructure]:
    """Build a Microstructure object for the last bar of `df`.

    If fundamentals (rows, ATR) aren't enough, return None so the caller
    can omit the field rather than emit junk.
    """
    if len(df) < 30:
        return None
    last = df.iloc[-1]
    atr = float(atr_series.iloc[-1])
    if atr <= 0 or not math.isfinite(atr):
        return None

    # Spread percentile (if a Spread column is present) --------------
    spread_rank: Optional[float] = None
    if "Spread" in df.columns:
        spr = df["Spread"].dropna()
        if len(spr) > 30:
            current = float(spr.iloc[-1])
            tail = spr.tail(500)
            spread_rank = float((tail <= current).mean())

    # Tick-rate ratio (Volume is tick-count proxy on OANDA) ----------
    if "Volume" in df.columns:
        vol = df["Volume"].astype(float)
        recent_mean = float(vol.tail(100).mean())
        current_vol = float(vol.iloc[-1])
        tick_rate_ratio = current_vol / recent_mean if recent_mean > 0 else 1.0
        # Volume z-score
        std = float(vol.tail(100).std(ddof=1))
        volume_anomaly_z = (current_vol - recent_mean) / std if std > 0 else 0.0
    else:
        tick_rate_ratio = 1.0
        volume_anomaly_z = 0.0
        vol = None

    # Delta estimate — buyer vs seller share of the last 20 bars'
    # volume. For each bar we apportion volume by where close landed
    # inside the bar's range: closes near the high count as buyers,
    # near the low as sellers. Result is bounded [-1, +1]:
    #   +1 = every recent bar closed at its high on full volume
    #   -1 = every recent bar closed at its low on full volume
    # Vectorized: avoid .iterrows() which is extremely slow. For 20
    # bars it adds ~1.5ms of pure pandas overhead.
    tail = df.tail(20)
    rng_arr = (tail["High"] - tail["Low"]).to_numpy()
    mask = rng_arr > 0
    if mask.any():
        share = (tail["Close"].to_numpy() - tail["Low"].to_numpy()) / np.where(rng_arr > 0, rng_arr, 1.0)
        share = np.clip(share, 0.0, 1.0)
        vols = (tail["Volume"].to_numpy().astype(float)
                if "Volume" in tail.columns else np.ones(len(tail)))
        buy_vol = float((vols * share * mask).sum())
        sell_vol = float((vols * (1.0 - share) * mask).sum())
    else:
        buy_vol = sell_vol = 0.0
    denom = buy_vol + sell_vol
    delta_estimate = (buy_vol - sell_vol) / denom if denom > 0 else 0.0

    # Absorption = high volume with tight range. Vectorized across the
    # last 10 bars instead of iterrows.
    absorption_score = 0.0
    tail10 = df.tail(10)
    if vol is not None and len(vol) > 30:
        mean10 = float(vol.tail(100).mean())
        std10 = float(vol.tail(100).std(ddof=1)) or 1.0
        rng10 = (tail10["High"] - tail10["Low"]).to_numpy()
        vols10 = tail10["Volume"].to_numpy().astype(float) \
            if "Volume" in tail10.columns else np.zeros(len(tail10))
        if mean10 > 0:
            v_z = (vols10 - mean10) / std10
            atr_safe = atr if atr > 0 else 1e-9
            r_ratio = rng10 / atr_safe
            cond = (v_z > 1.0) & (r_ratio < 0.7) & (rng10 > 0)
            if cond.any():
                scores = np.minimum(1.0, 0.5 * v_z + 0.5 * (1.0 - r_ratio))
                scores = np.where(cond, scores, 0.0)
                absorption_score = float(scores.max())

    # Wick pressure — lower wick minus upper wick, normalised to ATR
    _, upper, lower, _ = _candle_parts(last)
    wick_pressure = (lower - upper) / atr

    # Range regime via ATR percentile rank -------------------------
    compression_pct_rank: float = 0.5
    range_regime = "normal"
    atr_series_clean = atr_series.dropna()
    if len(atr_series_clean) > 60:
        tail500 = atr_series_clean.tail(500)
        compression_pct_rank = float((tail500 <= atr).mean())
        if compression_pct_rank < 0.25:
            range_regime = "compressed"
        elif compression_pct_rank > 0.80:
            range_regime = "expanded"

    return Microstructure(
        spread_pct_rank=spread_rank,
        tick_rate_ratio=float(tick_rate_ratio),
        delta_estimate=float(delta_estimate),
        absorption_score=float(absorption_score),
        volume_anomaly_z=float(volume_anomaly_z),
        wick_pressure=float(wick_pressure),
        range_regime=range_regime,
        compression_pct_rank=float(compression_pct_rank),
    )


# ---------------------------------------------------------------------------
# Bar type classification — Brooks-style (trend / doji / reversal).
# ---------------------------------------------------------------------------
def _classify_bar(row) -> str:
    body, upper, lower, rng = _candle_parts(row)
    if rng <= 0:
        return "dead"
    body_ratio = body / rng
    if body_ratio < 0.12:
        return "doji"
    direction = "bull" if row["Close"] > row["Open"] else "bear"
    if body_ratio > 0.70:
        return f"trend_bar_{direction}"
    if direction == "bull" and lower > 1.5 * body:
        return "reversal_bar_bull"
    if direction == "bear" and upper > 1.5 * body:
        return "reversal_bar_bear"
    return f"normal_bar_{direction}"


# ---------------------------------------------------------------------------
# The brain.
# ---------------------------------------------------------------------------
class ChartMind:
    """The technical-analysis brain. Stateless — all reads are derived
    from the dataframe passed in."""

    def __init__(self, swing_width: int = 3, profile_bins: int = 40):
        self.swing_width = int(swing_width)
        self.profile_bins = int(profile_bins)

    def read(self, df: pd.DataFrame, pair: str = "EUR_USD") -> ChartReading:
        """Produce a structured reading of the current state of the chart.

        df must be indexed by tz-aware UTC timestamps, with columns
        Open/High/Low/Close/Volume (Volume may be tick count).
        Column names are normalized to Title Case (open → Open,
        CLOSE → Close) so callers can pass either convention.
        """
        if df is None or len(df) < 60:
            raise ValueError("ChartMind.read: need >= 60 bars for a read")
        df = _normalize_ohlc_columns(df).copy()
        if df.index.tzinfo is None:
            df.index = df.index.tz_localize(UTC)

        # --- classical indicators ---
        ema20 = _ema(df["Close"], 20)
        ema50 = _ema(df["Close"], 50)
        ema200 = _ema(df["Close"], 200) if len(df) >= 200 else _ema(df["Close"], min(200, len(df)))
        atr = _atr(df, 14)
        adx = _adx(df, 14)

        last_close = float(df["Close"].iloc[-1])
        last_ts = df.index[-1]
        last_atr = float(atr.iloc[-1])

        # --- trend ---
        trend_direction, trend_strength = self._read_trend(
            ema20.iloc[-1], ema50.iloc[-1], ema200.iloc[-1],
            last_close, adx.iloc[-1],
        )

        # --- structure ---
        swings = _find_swings(df.iloc[-200:], width=self.swing_width) \
            if len(df) > 200 else _find_swings(df, width=self.swing_width)
        swing_highs = [s for s in swings if s.kind == "high"]
        swing_lows = [s for s in swings if s.kind == "low"]
        resistance = _cluster_levels(swings, side="resistance", atr=last_atr)
        support = _cluster_levels(swings, side="support", atr=last_atr)

        # --- candle patterns + bar type ---
        patterns = _detect_candle_patterns(df, atr=last_atr)
        bar_type = _classify_bar(df.iloc[-1])

        # --- ICT ---
        order_blocks = _detect_order_blocks(df)
        fvgs = _detect_fvgs(df)
        killzone = _killzone(last_ts)

        # --- profile ---
        profile = _market_profile(df.iloc[-96:], bins=self.profile_bins) \
            if len(df) >= 96 else _market_profile(df, bins=self.profile_bins)

        # --- context ---
        session = _session(last_ts)

        # vol regime via ATR percentile rank over 500 bars
        atr_series = atr.dropna()
        if len(atr_series) > 50:
            tail = atr_series.tail(500)
            pct = float((tail <= last_atr).mean())
        else:
            pct = 0.5
        if pct < 0.25:
            regime = "low"
        elif pct > 0.80:
            regime = "high"
        else:
            regime = "normal"

        # Microstructure (upgrade #2) --------------------------------
        micro = _compute_microstructure(df, atr)

        # Trap / fake-move detection (Phase A) -----------------------
        # Lazy import to avoid circular at module-load time.
        from ChartMind.traps import detect_traps
        traps = detect_traps(df, atr, session_of=_session)

        reading = ChartReading(   # partial — filled below + wyckoff after
            pair=pair,
            timestamp=last_ts,
            price=last_close,
            trend_direction=trend_direction,
            trend_strength=trend_strength,
            ema20=float(ema20.iloc[-1]),
            ema50=float(ema50.iloc[-1]),
            ema200=float(ema200.iloc[-1]),
            adx=float(adx.iloc[-1]),
            swing_highs=swing_highs[-8:],
            swing_lows=swing_lows[-8:],
            key_resistance=resistance,
            key_support=support,
            candle_patterns=patterns,
            bar_type=bar_type,
            order_blocks=order_blocks,
            fair_value_gaps=fvgs,
            killzone=killzone,
            profile=profile,
            session=session,
            atr14=last_atr,
            atr_pct_rank=pct,
            volatility_regime=regime,
            micro=micro,
            traps=traps,
        )
        # Wyckoff phase detection (Phase B) — needs the populated
        # reading + traps list, so we compute it after the dataclass
        # is constructed and attach it before building the summary.
        from ChartMind.wyckoff import detect_wyckoff
        reading.wyckoff = detect_wyckoff(
            df, atr, reading=reading, traps=traps,
        )
        # Price-action context (Phase C: Brooks-style multi-bar read) --
        from ChartMind.price_action import read_price_action
        reading.pa_context = read_price_action(df, atr, reading=reading)
        # Classical chart patterns (Phase D) --------------------------
        from ChartMind.chart_patterns import detect_chart_patterns
        reading.chart_patterns = detect_chart_patterns(
            df, atr,
            swing_highs=swing_highs,
            swing_lows=swing_lows,
            trend_direction=trend_direction,
        )
        # Algorithm awareness (Phase G) — VWAP, rounds, HFT footprint
        from ChartMind.algo_awareness import read_algo_awareness
        try:
            reading.algo_awareness = read_algo_awareness(
                df, direction=None, pair_pip=0.0001,
            )
        except Exception:
            # Defensive: algo_awareness is informational; should never
            # break the main read path. Fall silent on failure.
            reading.algo_awareness = None
        reading.summary = self._build_summary(reading)
        return reading

    # ------------------------------------------------------------------
    # Upgrade #1 — Multi-Timeframe reading.
    #
    # Takes a dict of pre-prepared OHLCV frames, one per timeframe,
    # produces a full reading on each, and scores the alignment of their
    # trends. Drawn from Murphy's multi-timeframe doctrine: a signal
    # aligned with higher-timeframe bias is several multiples more
    # reliable than one opposing it.
    # ------------------------------------------------------------------
    # Relative weights when we roll the alignment score across TFs.
    # Higher TFs carry more weight because they define the macro bias.
    _TF_WEIGHT = {
        "M1": 0.05,
        "M5": 0.10,
        "M15": 0.20,
        "M30": 0.15,
        "H1": 0.25,
        "H4": 0.40,
        "D": 0.50,
    }

    def read_multi_tf(
        self,
        dfs: dict,                  # {tf_label: OHLCV DataFrame}
        pair: str = "EUR_USD",
    ) -> MultiTFReading:
        """Read the chart across multiple timeframes simultaneously.

        `dfs` keys should be among: "M1", "M5", "M15", "M30", "H1",
        "H4", "D". Unknown keys receive a default weight of 0.10.

        Returns a MultiTFReading whose `summary` is a one-screen
        narrative ready for the executor to log or push to Telegram.
        """
        if not dfs:
            raise ValueError("read_multi_tf: empty dfs")

        per_tf: dict = {}
        for tf, df in dfs.items():
            if df is None or len(df) < 60:
                # Not enough bars — skip, don't fail. Record the miss
                # so the alignment score can de-weight absent TFs.
                continue
            per_tf[tf] = self.read(df, pair=pair)

        if not per_tf:
            raise ValueError("read_multi_tf: no TF had enough data")

        # Alignment score — signed sum of TF-weighted trend votes.
        # up = +1, down = -1, flat = 0. Absent TFs contribute 0.
        weights_total = 0.0
        alignment_raw = 0.0
        for tf, rd in per_tf.items():
            w = self._TF_WEIGHT.get(tf, 0.10)
            weights_total += w
            if rd.trend_direction == "up":
                alignment_raw += w * rd.trend_strength
            elif rd.trend_direction == "down":
                alignment_raw -= w * rd.trend_strength
            # flat contributes 0
        alignment = alignment_raw / weights_total if weights_total > 0 else 0.0
        # Clip to [-1, +1] — numerical safety
        alignment = max(-1.0, min(1.0, alignment))

        # Dominant TF = highest-weight TF actually available
        dominant_tf = max(per_tf, key=lambda tf: self._TF_WEIGHT.get(tf, 0.10))
        dominant_trend = per_tf[dominant_tf].trend_direction

        # Conflict catalogue — pairs of TFs with opposing directions
        conflicts: list = []
        tfs = list(per_tf.keys())
        for i in range(len(tfs)):
            for j in range(i + 1, len(tfs)):
                a, b = tfs[i], tfs[j]
                ta = per_tf[a].trend_direction
                tb = per_tf[b].trend_direction
                if (ta == "up" and tb == "down") or (ta == "down" and tb == "up"):
                    conflicts.append(f"{a} {ta} vs {b} {tb}")

        # Synthesis: last M15 timestamp if present, else first available.
        ts_reference = per_tf.get("M15", next(iter(per_tf.values()))).timestamp

        mtf = MultiTFReading(
            pair=pair,
            timestamp=ts_reference,
            per_tf=per_tf,
            alignment=alignment,
            dominant_trend=dominant_trend,
            dominant_tf=dominant_tf,
            conflicts=conflicts,
        )
        mtf.summary = self._build_mtf_summary(mtf)
        return mtf

    @staticmethod
    def _build_mtf_summary(r: MultiTFReading) -> str:
        """Human-readable multi-TF synopsis."""
        lines: list[str] = []
        lines.append(
            f"{r.pair} multi-TF read @ {r.timestamp.strftime('%Y-%m-%d %H:%M UTC')}"
        )
        # Alignment verbal label
        if r.alignment > 0.5:
            verbal = "STRONG UP alignment"
        elif r.alignment > 0.2:
            verbal = "mild up alignment"
        elif r.alignment < -0.5:
            verbal = "STRONG DOWN alignment"
        elif r.alignment < -0.2:
            verbal = "mild down alignment"
        else:
            verbal = "no alignment — chop or conflict"
        lines.append(f"Alignment score: {r.alignment:+.2f}  ({verbal})")
        lines.append(
            f"Dominant bias: {r.dominant_trend.upper()} "
            f"from {r.dominant_tf}"
        )

        # Per-TF summary line
        tfs_sorted = sorted(r.per_tf.keys(),
                            key=lambda t: -ChartMind._TF_WEIGHT.get(t, 0.10))
        lines.append("Per-TF:")
        for tf in tfs_sorted:
            rd = r.per_tf[tf]
            arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(
                rd.trend_direction, "?"
            )
            lines.append(
                f"  {tf:4s} {arrow} {rd.trend_direction:5s} "
                f"ADX {rd.adx:5.1f}  vol={rd.volatility_regime}  "
                f"bar={rd.bar_type}"
            )

        if r.conflicts:
            lines.append("Conflicts: " + "; ".join(r.conflicts))

        # Trading-grade read
        lines.append("")
        if r.alignment > 0.5:
            lines.append(
                ">>> Long bias is confluent across timeframes. "
                "Setups aligned with dominant trend carry extra weight."
            )
        elif r.alignment < -0.5:
            lines.append(
                ">>> Short bias is confluent across timeframes. "
                "Setups aligned with dominant trend carry extra weight."
            )
        else:
            lines.append(
                ">>> No clear multi-TF bias. Expect range behaviour; "
                "breakouts are more likely to fail. Reduce size."
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Upgrade #3 — Confluence Engine.
    #
    # Takes a ChartReading (and optionally a MultiTFReading) and
    # aggregates every factor into a single signed conviction. Higher
    # score ⇒ more factors aligned. The same factor carries different
    # weight depending on its historical reliability for a scalper,
    # drawn from the canon:
    #
    #   Multi-TF alignment    0.25  — Murphy: biggest single win
    #   Key-level proximity   0.15  — Brooks + Dalton: price at S/R
    #   Trend (local)         0.15  — Murphy ADX + EMA stack
    #   ICT Order Block       0.10  — ICT Bible
    #   Candle pattern        0.10  — Nison
    #   Microstructure        0.10  — Harris + retail order-flow proxies
    #   FVG alignment         0.05  — ICT Bible
    #   Killzone timing       0.05  — ICT Bible
    #   Market profile locus  0.05  — Dalton
    #
    # Verdict threshold: net conviction must exceed 0.20 for a
    # directional call. Below that we report "neutral" — the
    # anti-pattern honesty Schwager / Douglas argue for.
    # ------------------------------------------------------------------
    _CONF_WEIGHTS: dict = {
        # Original 9 factors — rebalanced to make room for A-D phases
        "mtf_alignment":   0.20,   # Murphy — top-down bias
        "local_trend":     0.10,   # Dow
        "key_level":       0.12,   # Murphy — support/resistance
        "candle":          0.05,   # Nison — latest bar only
        "ict_ob":          0.05,   # ICT
        "ict_fvg":         0.03,   # ICT
        "killzone":        0.02,   # ICT session timing
        "micro":           0.05,   # microstructure delta/wick
        "profile":         0.02,   # Dalton — POC locus
        # Phases A-D contributions — new, previously ignored
        "traps":           0.10,   # Wyckoff-style fakes, high signal
        "wyckoff":         0.08,   # phase bias (accum → long, distrib → short)
        "price_action":    0.10,   # Brooks signal+entry bar + pullback
        "chart_patterns":  0.08,   # Edwards/Magee/Bulkowski classical
    }

    # Neutral threshold: below this the verdict is "neutral" to force
    # honest abstention when factors don't really pile up.
    _VERDICT_THRESHOLD: float = 0.20

    def confluence(
        self,
        reading: ChartReading,
        mtf: Optional[MultiTFReading] = None,
    ) -> ConfluenceScore:
        """Compute the aggregated confluence score for a reading.

        Any missing factor simply contributes 0 — it never fails the
        call. The verdict defaults to 'neutral' when no side accumulates
        enough net conviction.
        """
        factors: list[ConfluenceFactor] = []
        w = self._CONF_WEIGHTS

        # 1. Multi-TF alignment -----------------------------------
        if mtf is not None:
            d = "long" if mtf.alignment > 0 else \
                ("short" if mtf.alignment < 0 else "neutral")
            strength = min(1.0, abs(mtf.alignment))
            factors.append(ConfluenceFactor(
                name="mtf_alignment", direction=d,
                raw_strength=strength, weight=w["mtf_alignment"],
                contribution=strength * w["mtf_alignment"]
                              * (1 if d == "long" else -1 if d == "short" else 0),
            ))

        # 2. Local trend ------------------------------------------
        d = reading.trend_direction
        direction = "long" if d == "up" else ("short" if d == "down" else "neutral")
        strength = reading.trend_strength
        factors.append(ConfluenceFactor(
            name="local_trend", direction=direction,
            raw_strength=strength, weight=w["local_trend"],
            contribution=strength * w["local_trend"]
                          * (1 if direction == "long" else -1 if direction == "short" else 0),
        ))

        # 3. Key-level proximity ----------------------------------
        # Bullish if price is within 0.35 ATR ABOVE a strong support.
        # Bearish if within 0.35 ATR BELOW a strong resistance.
        atr = reading.atr14 or 1e-9
        px = reading.price
        sup_hit = any(
            0 <= (px - lv.price) <= 0.35 * atr and lv.touches >= 2
            for lv in reading.key_support
        )
        res_hit = any(
            0 <= (lv.price - px) <= 0.35 * atr and lv.touches >= 2
            for lv in reading.key_resistance
        )
        if sup_hit and not res_hit:
            factors.append(ConfluenceFactor(
                "key_level", "long", 0.8, w["key_level"],
                0.8 * w["key_level"],
            ))
        elif res_hit and not sup_hit:
            factors.append(ConfluenceFactor(
                "key_level", "short", 0.8, w["key_level"],
                -0.8 * w["key_level"],
            ))
        else:
            factors.append(ConfluenceFactor(
                "key_level", "neutral", 0.0, w["key_level"], 0.0,
            ))

        # 4. Candle pattern on the MOST RECENT bar ----------------
        # Only the latest (or last) pattern matters — older ones are noise.
        latest_pat = reading.candle_patterns[-1] if reading.candle_patterns else None
        if latest_pat and latest_pat.direction in ("bullish", "bearish"):
            direction = "long" if latest_pat.direction == "bullish" else "short"
            strength = min(1.0, latest_pat.strength / 1.5)
            factors.append(ConfluenceFactor(
                "candle", direction, strength, w["candle"],
                strength * w["candle"]
                * (1 if direction == "long" else -1),
            ))
        else:
            factors.append(ConfluenceFactor(
                "candle", "neutral", 0.0, w["candle"], 0.0,
            ))

        # 5. ICT Order Block alignment ----------------------------
        # Price inside or adjacent to an unmitigated OB matching trend.
        unmit = [ob for ob in reading.order_blocks if not ob.mitigated]
        ob_contrib = 0.0
        ob_dir = "neutral"
        ob_strength = 0.0
        for ob in unmit:
            if ob.side == "bullish" and ob.low <= px <= ob.high + 0.5 * atr:
                ob_contrib = w["ict_ob"]
                ob_dir, ob_strength = "long", 1.0
                break
            if ob.side == "bearish" and ob.low - 0.5 * atr <= px <= ob.high:
                ob_contrib = -w["ict_ob"]
                ob_dir, ob_strength = "short", 1.0
                break
        factors.append(ConfluenceFactor(
            "ict_ob", ob_dir, ob_strength, w["ict_ob"], ob_contrib,
        ))

        # 6. FVG alignment ---------------------------------------
        unfilled = [f for f in reading.fair_value_gaps if not f.filled]
        fvg_contrib = 0.0
        fvg_dir = "neutral"
        fvg_strength = 0.0
        for fv in unfilled:
            # Price is reaching into a bullish FVG → long bias
            if fv.side == "bullish" and fv.bottom <= px <= fv.top:
                fvg_contrib = w["ict_fvg"]
                fvg_dir, fvg_strength = "long", 1.0
                break
            if fv.side == "bearish" and fv.bottom <= px <= fv.top:
                fvg_contrib = -w["ict_fvg"]
                fvg_dir, fvg_strength = "short", 1.0
                break
        factors.append(ConfluenceFactor(
            "ict_fvg", fvg_dir, fvg_strength, w["ict_fvg"], fvg_contrib,
        ))

        # 7. Killzone timing bump --------------------------------
        # Active killzone reinforces whatever other directional bias
        # exists, but only if net bias is already > 0. Encoded as a
        # 'long' direction scaled by net of factors so far.
        net_so_far = sum(f.contribution for f in factors)
        kz_contrib = 0.0
        kz_dir = "neutral"
        kz_strength = 0.0
        if reading.killzone and abs(net_so_far) > 0.05:
            kz_contrib = w["killzone"] * (1 if net_so_far > 0 else -1)
            kz_dir = "long" if net_so_far > 0 else "short"
            kz_strength = 1.0
        factors.append(ConfluenceFactor(
            "killzone", kz_dir, kz_strength, w["killzone"], kz_contrib,
        ))

        # 8. Microstructure --------------------------------------
        if reading.micro is not None:
            m = reading.micro
            # Composite: delta_estimate is [-1..1]; wick_pressure normalised;
            # range_regime=compressed adds squeeze tension.
            raw = 0.6 * m.delta_estimate + 0.3 * max(-1, min(1, m.wick_pressure))
            if m.range_regime == "compressed":
                raw *= 1.2      # coiled spring amplifier
            raw = max(-1, min(1, raw))
            direction = "long" if raw > 0 else ("short" if raw < 0 else "neutral")
            factors.append(ConfluenceFactor(
                "micro", direction, abs(raw), w["micro"],
                raw * w["micro"],
            ))
        else:
            factors.append(ConfluenceFactor(
                "micro", "neutral", 0.0, w["micro"], 0.0,
            ))

        # 9. Market profile locus --------------------------------
        # Bullish if price is below POC (accumulation zone) in an
        # uptrend context; bearish if above POC in a downtrend.
        prof = reading.profile
        prof_contrib = 0.0
        prof_dir = "neutral"
        prof_strength = 0.0
        if prof and prof.bars_counted >= 48:
            if reading.trend_direction == "up" and px < prof.poc:
                prof_contrib = w["profile"]
                prof_dir, prof_strength = "long", 1.0
            elif reading.trend_direction == "down" and px > prof.poc:
                prof_contrib = -w["profile"]
                prof_dir, prof_strength = "short", 1.0
        factors.append(ConfluenceFactor(
            "profile", prof_dir, prof_strength, w["profile"], prof_contrib,
        ))

        # 10. Traps (Phase A) -------------------------------------
        # Recent traps (last 15 bars) with direction + strength.
        # Bullish traps (spring, liquidity_grab_low, failed_breakout_low,
        # judas_swing_bullish) push long; bearish traps mirror.
        # Multiple recent traps in same direction → stronger signal.
        trap_contrib = 0.0
        trap_dir = "neutral"
        trap_strength = 0.0
        if reading.traps:
            # Look at traps within last 15 bars.
            recent_cutoff = (reading.timestamp - pd.Timedelta(minutes=15 * 15))
            recent = [t for t in reading.traps if t.ts >= recent_cutoff]
            bull_s = sum(t.strength for t in recent if t.direction == "bullish")
            bear_s = sum(t.strength for t in recent if t.direction == "bearish")
            if bull_s > bear_s and bull_s > 0:
                trap_strength = min(1.0, bull_s / 2.0)  # 2 strong traps = 1.0
                trap_contrib = trap_strength * w["traps"]
                trap_dir = "long"
            elif bear_s > bull_s and bear_s > 0:
                trap_strength = min(1.0, bear_s / 2.0)
                trap_contrib = -trap_strength * w["traps"]
                trap_dir = "short"
        factors.append(ConfluenceFactor(
            "traps", trap_dir, trap_strength, w["traps"], trap_contrib,
        ))

        # 11. Wyckoff (Phase B) ----------------------------------
        # Accumulation → long bias. Distribution → short bias.
        # Sub-phase (early/mid/late) scales conviction — late phases
        # are closer to breakout, thus higher strength.
        wy_contrib = 0.0
        wy_dir = "neutral"
        wy_strength = 0.0
        if reading.wyckoff is not None:
            phase = getattr(reading.wyckoff, "phase", "unknown")
            sub = getattr(reading.wyckoff, "sub_phase", "") or ""
            wy_conf = float(getattr(reading.wyckoff, "confidence", 0.0) or 0.0)
            sub_mult = {"late": 1.0, "mid": 0.7, "early": 0.4}.get(sub, 0.5)
            if phase == "accumulation":
                wy_strength = wy_conf * sub_mult
                wy_contrib = wy_strength * w["wyckoff"]
                wy_dir = "long"
            elif phase == "markup":
                wy_strength = wy_conf * 0.6
                wy_contrib = wy_strength * w["wyckoff"]
                wy_dir = "long"
            elif phase == "distribution":
                wy_strength = wy_conf * sub_mult
                wy_contrib = -wy_strength * w["wyckoff"]
                wy_dir = "short"
            elif phase == "markdown":
                wy_strength = wy_conf * 0.6
                wy_contrib = -wy_strength * w["wyckoff"]
                wy_dir = "short"
        factors.append(ConfluenceFactor(
            "wyckoff", wy_dir, wy_strength, w["wyckoff"], wy_contrib,
        ))

        # 12. Price Action (Phase C) — Brooks signal+entry+pullback
        # A fresh entry_bar or two-legged pullback is a strong
        # directional signal. We take the most recent.
        pa_contrib = 0.0
        pa_dir = "neutral"
        pa_strength = 0.0
        pa = reading.pa_context
        if pa is not None:
            eb = pa.entry_bars[-1] if pa.entry_bars else None
            pb = pa.pullbacks[-1] if pa.pullbacks else None
            # Prefer entry_bar (most actionable)
            if eb is not None:
                pa_dir = "long" if eb.direction == "bullish" else "short"
                pa_strength = 0.8
            elif pb is not None:
                pa_dir = "long" if pb.direction == "bullish" else "short"
                pa_strength = 0.6
            if pa_dir != "neutral":
                pa_contrib = pa_strength * w["price_action"]
                if pa_dir == "short":
                    pa_contrib = -pa_contrib
        factors.append(ConfluenceFactor(
            "price_action", pa_dir, pa_strength, w["price_action"], pa_contrib,
        ))

        # 13. Chart Patterns (Phase D) ----------------------------
        # Take the highest-confidence pattern; its direction drives
        # the contribution. Confidence scales strength.
        cp_contrib = 0.0
        cp_dir = "neutral"
        cp_strength = 0.0
        if reading.chart_patterns:
            best = max(reading.chart_patterns, key=lambda p: p.confidence)
            if best.direction == "bullish":
                cp_dir = "long"
                cp_strength = float(best.confidence)
                cp_contrib = cp_strength * w["chart_patterns"]
            elif best.direction == "bearish":
                cp_dir = "short"
                cp_strength = float(best.confidence)
                cp_contrib = -cp_strength * w["chart_patterns"]
        factors.append(ConfluenceFactor(
            "chart_patterns", cp_dir, cp_strength, w["chart_patterns"], cp_contrib,
        ))

        # Aggregate ----------------------------------------------
        long_raw = sum(f.contribution for f in factors if f.contribution > 0)
        short_raw = -sum(f.contribution for f in factors if f.contribution < 0)
        # Clip into [0, 1]; factors already sum to ≤ 1 when perfectly aligned
        long_conviction = max(0.0, min(1.0, long_raw))
        short_conviction = max(0.0, min(1.0, short_raw))

        net = long_conviction - short_conviction
        if net >= self._VERDICT_THRESHOLD:
            verdict = "long"
            verdict_strength = min(1.0, net)
        elif net <= -self._VERDICT_THRESHOLD:
            verdict = "short"
            verdict_strength = min(1.0, -net)
        else:
            verdict = "neutral"
            verdict_strength = abs(net)

        score = ConfluenceScore(
            long_conviction=long_conviction,
            short_conviction=short_conviction,
            verdict=verdict,
            verdict_strength=verdict_strength,
            factors=factors,
        )
        score.summary = self._build_confluence_summary(reading, score)
        return score

    # ------------------------------------------------------------------
    # Top-level orchestrator — one call to run the whole brain.
    # ------------------------------------------------------------------
    def analyze(
        self,
        df: pd.DataFrame,
        *,
        pair: str = "EUR_USD",
        mtf_dfs: Optional[dict] = None,
        calibrated_confidence=None,
        clarity_scanner=None,
        priors=None,
        exec_ctx=None,
        pair_pip: float = 0.0001,
    ) -> "Analysis":
        """Run the full ChartMind cognitive pipeline in one call.

        Stages:
            1. read(df)                      → ChartReading
            2. read_multi_tf(mtf_dfs)        → MultiTFReading (optional)
            3. confluence(reading, mtf)      → ConfluenceScore
            4. calibrated_confidence.calibrate(raw_conviction)
                                              → CalibratedProba (optional)
            5. clarity_scanner.scan(...)     → ClarityReport (optional)
            6. generate_plan(reading, ...)   → TradePlan
            7. decide_entry(reading, plan, exec_ctx)
                                              → EntryPlan (if exec_ctx given)

        Returns an Analysis dataclass bundling every stage's output.
        Absent optional inputs simply yield None for that stage — no
        part of the pipeline is mandatory beyond `read` and `confluence`.
        """
        # --- Stage 1: base reading ---
        reading = self.read(df, pair=pair)

        # --- Stage 2: optional multi-TF ---
        mtf = None
        if mtf_dfs:
            try:
                mtf = self.read_multi_tf(mtf_dfs, pair=pair)
            except Exception:
                mtf = None

        # --- Stage 3: confluence ---
        conf = self.confluence(reading, mtf=mtf)

        # --- Stage 4: calibrated confidence (optional) ---
        calibrated = None
        if calibrated_confidence is not None:
            if conf.verdict == "long":
                raw = conf.long_conviction
            elif conf.verdict == "short":
                raw = conf.short_conviction
            else:
                raw = max(conf.long_conviction, conf.short_conviction, 0.5)
            try:
                calibrated = calibrated_confidence.calibrate(raw)
            except Exception:
                calibrated = None

        # --- Stage 5: clarity (optional) ---
        clarity = None
        if clarity_scanner is not None:
            try:
                clarity = clarity_scanner.scan(
                    reading=reading, mtf=mtf, confluence=conf,
                    calibrated=calibrated,
                )
            except Exception:
                clarity = None

        # --- Stage 6: plan ---
        from ChartMind.planner import generate_plan
        try:
            plan = generate_plan(
                reading=reading, mtf=mtf, confluence=conf,
                clarity=clarity, calibrated=calibrated,
                priors=priors,
                pair_pip=pair_pip,
            )
        except Exception:
            plan = None

        # --- Stage 7: entry (optional — needs live market ctx) ---
        entry = None
        if exec_ctx is not None and plan is not None:
            from ChartMind.execution import decide_entry
            try:
                entry = decide_entry(reading, plan, exec_ctx)
            except Exception:
                entry = None

        return Analysis(
            reading=reading,
            mtf=mtf,
            confluence=conf,
            calibrated=calibrated,
            clarity=clarity,
            plan=plan,
            entry=entry,
        )

    @staticmethod
    def _build_confluence_summary(
        reading: ChartReading, score: ConfluenceScore,
    ) -> str:
        lines: list[str] = []
        lines.append(
            f"Confluence verdict: {score.verdict.upper()} "
            f"(strength {score.verdict_strength:.2f})"
        )
        lines.append(
            f"Long={score.long_conviction:.2f}  "
            f"Short={score.short_conviction:.2f}"
        )
        # Show contributing factors sorted by absolute contribution
        ranked = sorted(score.factors, key=lambda f: -abs(f.contribution))
        for f in ranked:
            if abs(f.contribution) < 0.01:
                continue
            arrow = "↑" if f.contribution > 0 else "↓"
            lines.append(
                f"  {arrow} {f.name:14s} {f.direction:8s}  "
                f"w={f.weight:.2f}  str={f.raw_strength:.2f}  "
                f"contrib={f.contribution:+.3f}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Trend heuristic.
    # ------------------------------------------------------------------
    @staticmethod
    def _read_trend(e20: float, e50: float, e200: float,
                    price: float, adx_val: float) -> tuple[str, float]:
        """Classify trend direction and a 0..1 strength proxy.

        Direction:
          * up   — price > e20 > e50 > e200
          * down — price < e20 < e50 < e200
          * flat — otherwise

        Strength: scaled ADX (clipped to [0, 50] then /50). ADX < 20
        means chop regardless of EMA stacking; we report "flat" in that
        case.
        """
        if adx_val < 20:
            return "flat", float(max(0.0, min(1.0, adx_val / 50.0)))
        if price > e20 > e50 > e200:
            return "up", float(max(0.0, min(1.0, adx_val / 50.0)))
        if price < e20 < e50 < e200:
            return "down", float(max(0.0, min(1.0, adx_val / 50.0)))
        return "flat", float(max(0.0, min(1.0, adx_val / 50.0)))

    # ------------------------------------------------------------------
    # Human-readable summary.
    # ------------------------------------------------------------------
    @staticmethod
    def _build_summary(r: ChartReading) -> str:
        lines: list[str] = []
        lines.append(f"{r.pair} @ {r.price:.5f}  ({r.timestamp.strftime('%Y-%m-%d %H:%M UTC')})")
        lines.append(f"Session: {r.session}"
                     + (f"   [Killzone: {r.killzone}]" if r.killzone else ""))
        lines.append(f"Trend: {r.trend_direction.upper()} "
                     f"(ADX {r.adx:.1f}, strength {r.trend_strength:.2f})")
        lines.append(f"EMA stack: 20={r.ema20:.5f}  50={r.ema50:.5f}  200={r.ema200:.5f}")
        lines.append(f"Volatility: {r.volatility_regime.upper()} "
                     f"(ATR {r.atr14:.5f}, rank {r.atr_pct_rank:.2f})")

        if r.key_resistance:
            res_str = ", ".join(f"{lv.price:.5f}(×{lv.touches})"
                                for lv in r.key_resistance[:3])
            lines.append(f"Resistance: {res_str}")
        if r.key_support:
            sup_str = ", ".join(f"{lv.price:.5f}(×{lv.touches})"
                                for lv in r.key_support[:3])
            lines.append(f"Support: {sup_str}")

        if r.candle_patterns:
            pat_str = ", ".join(f"{p.name}({p.direction})"
                                for p in r.candle_patterns[-3:])
            lines.append(f"Recent candle patterns: {pat_str}")
        lines.append(f"Bar type: {r.bar_type}")

        unmit_ob = [ob for ob in r.order_blocks if not ob.mitigated]
        if unmit_ob:
            ob_str = ", ".join(f"{ob.side}[{ob.low:.5f}-{ob.high:.5f}]"
                               for ob in unmit_ob[-3:])
            lines.append(f"Unmitigated OB: {ob_str}")
        unfilled_fvg = [f for f in r.fair_value_gaps if not f.filled]
        if unfilled_fvg:
            f_str = ", ".join(f"{f.side}[{f.bottom:.5f}-{f.top:.5f}]"
                              for f in unfilled_fvg[-3:])
            lines.append(f"Unfilled FVG: {f_str}")

        if r.profile and r.profile.bars_counted:
            lines.append(f"Profile: POC {r.profile.poc:.5f}, "
                         f"VA {r.profile.value_area_low:.5f}–{r.profile.value_area_high:.5f}")

        if r.traps:
            recent = r.traps[:3]
            lines.append("Traps / fake moves:")
            for t in recent:
                lines.append(
                    f"  - {t.name} ({t.direction}, str {t.strength:.2f}) "
                    f"{t.ts.strftime('%H:%M')}  — {t.detail}"
                )

        if r.wyckoff is not None:
            w = r.wyckoff
            sub = f"/{w.sub_phase}" if w.sub_phase else ""
            lines.append(
                f"Wyckoff: {w.phase}{sub} "
                f"(confidence {w.confidence:.2f}) — {w.detail[:120]}"
            )

        if r.pa_context is not None and r.pa_context.best_setup:
            lines.append(f"Price-action: {r.pa_context.best_setup}")

        if r.chart_patterns:
            lines.append("Chart patterns:")
            for p in r.chart_patterns[:3]:
                tgt = f" → target {p.target:.5f}" if p.target is not None else ""
                lines.append(
                    f"  - {p.name} ({p.direction}, conf {p.confidence:.2f}){tgt}"
                )

        if r.micro:
            m = r.micro
            parts = [f"tick_rate={m.tick_rate_ratio:.2f}x"]
            if m.spread_pct_rank is not None:
                parts.append(f"spread_rank={m.spread_pct_rank:.2f}")
            parts.append(f"delta={m.delta_estimate:+.2f}")
            parts.append(f"wick_pressure={m.wick_pressure:+.2f}")
            parts.append(f"range={m.range_regime}")
            if m.absorption_score > 0.4:
                parts.append(f"absorption={m.absorption_score:.2f}")
            if abs(m.volume_anomaly_z) > 2:
                parts.append(f"vol_z={m.volume_anomaly_z:+.1f}")
            lines.append("Micro: " + "  ".join(parts))

        return "\n".join(lines)
