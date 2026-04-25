# -*- coding: utf-8 -*-
"""MomentumAnalyzer — RSI + MACD diagnostics with divergence.

The two oscillators we care about:
   * RSI(14) — overbought >= 70, oversold <= 30. We also detect
     bullish/bearish divergence vs price over the last 30 bars.
   * MACD(12,26,9) — bull cross when MACD line crosses above signal,
     bear cross when below. We track the last cross + histogram value.

Why these two only
------------------
Adding more oscillators creates redundancy without edge. Connors,
Schwager interviews, and ample backtests all converge on RSI+MACD
as the minimum set that captures both extremes (RSI) and trend
shifts (MACD).
"""
from __future__ import annotations
from .models import MomentumReading


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    # Wilder smoothing
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


def _ema(values: list, span: int) -> list:
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _macd(closes: list, fast: int = 12, slow: int = 26, signal: int = 9):
    if len(closes) < slow + signal:
        return [0.0], [0.0], [0.0]
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    macd_line = [a - b for a, b in zip(ef, es)]
    sig_line = _ema(macd_line, signal)
    hist = [m - s for m, s in zip(macd_line, sig_line)]
    return macd_line, sig_line, hist


def _detect_divergence(closes: list, rsi_series: list, lookback: int = 30) -> str:
    """Compare last 2 swings in price and RSI.
    Bullish div: price lower-low, RSI higher-low.
    Bearish div: price higher-high, RSI lower-high.
    """
    n = len(closes)
    if n < lookback or len(rsi_series) < lookback:
        return "none"
    p = closes[-lookback:]
    r = rsi_series[-lookback:]
    mid = lookback // 2
    p1, p2 = min(p[:mid]), min(p[mid:])
    r1, r2 = min(r[:mid]), min(r[mid:])
    if p2 < p1 and r2 > r1:
        return "bullish"
    p1, p2 = max(p[:mid]), max(p[mid:])
    r1, r2 = max(r[:mid]), max(r[mid:])
    if p2 > p1 and r2 < r1:
        return "bearish"
    return "none"


class MomentumAnalyzer:
    def __init__(self, rsi_period: int = 14,
                 rsi_overbought: float = 70.0,
                 rsi_oversold: float = 30.0):
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold

    def analyze(self, m15_bars: list) -> MomentumReading:
        if len(m15_bars) < 50:
            return MomentumReading(
                rsi=50.0, rsi_state="neutral", rsi_divergence="none",
                macd_signal="none", macd_hist=0.0,
                momentum_direction="neutral",
            )
        closes = [b.close for b in m15_bars]

        # RSI series for divergence check
        rsi_series = []
        for i in range(self.rsi_period + 1, len(closes) + 1):
            rsi_series.append(_rsi(closes[:i], self.rsi_period))
        rsi_now = rsi_series[-1] if rsi_series else 50.0

        # State
        if rsi_now >= self.rsi_overbought:
            rsi_state = "overbought"
        elif rsi_now <= self.rsi_oversold:
            rsi_state = "oversold"
        else:
            rsi_state = "neutral"

        div = _detect_divergence(closes, rsi_series, lookback=30)

        # MACD
        macd_line, sig_line, hist = _macd(closes)
        macd_signal = "none"
        if len(macd_line) >= 2 and len(sig_line) >= 2:
            if macd_line[-2] <= sig_line[-2] and macd_line[-1] > sig_line[-1]:
                macd_signal = "bull_cross"
            elif macd_line[-2] >= sig_line[-2] and macd_line[-1] < sig_line[-1]:
                macd_signal = "bear_cross"
        macd_hist = hist[-1] if hist else 0.0

        # Direction synthesis
        d = "neutral"
        if (rsi_state == "oversold" or div == "bullish") and macd_signal != "bear_cross":
            d = "long"
        elif (rsi_state == "overbought" or div == "bearish") and macd_signal != "bull_cross":
            d = "short"
        elif macd_signal == "bull_cross" and rsi_now > 50:
            d = "long"
        elif macd_signal == "bear_cross" and rsi_now < 50:
            d = "short"

        return MomentumReading(
            rsi=rsi_now, rsi_state=rsi_state, rsi_divergence=div,
            macd_signal=macd_signal, macd_hist=macd_hist,
            momentum_direction=d,
        )
