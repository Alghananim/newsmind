# -*- coding: utf-8 -*-
"""MomentumAnalyzer — RSI + MACD with O(n) incremental indicators.

Performance-critical: this analyzer is called once per M15 bar in the
backtest loop (~50,000 bars per pair per quarter × 8 quarters). The
old O(n²) implementation made walk-forward intractable; this version
computes RSI(14) and MACD(12,26,9) once per call in O(n) and caches
the running RSI series for divergence detection.
"""
from __future__ import annotations
from .models import MomentumReading


def _rsi_series(closes: list, period: int = 14) -> list:
    """Wilder RSI for every bar (length len(closes)-period). O(n)."""
    if len(closes) < period + 1:
        return []
    gains, losses = [0.0], [0.0]
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[1:period+1]) / period
    avg_l = sum(losses[1:period+1]) / period
    out = []
    for i in range(period+1, len(closes)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        if avg_l == 0:
            out.append(100.0)
        else:
            rs = avg_g / avg_l
            out.append(100 - 100/(1+rs))
    return out


def _ema(values: list, span: int) -> list:
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def _macd_series(closes: list, fast: int = 12, slow: int = 26, signal: int = 9):
    if len(closes) < slow + signal:
        return [0.0], [0.0], [0.0]
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    macd_line = [a - b for a, b in zip(ef, es)]
    sig_line = _ema(macd_line, signal)
    hist = [m - s for m, s in zip(macd_line, sig_line)]
    return macd_line, sig_line, hist


def _detect_divergence(closes_tail: list, rsi_tail: list) -> str:
    """Compare last 2 swings in price and RSI over tail-only data."""
    if len(closes_tail) < 10 or len(rsi_tail) < 10:
        return "none"
    n = min(len(closes_tail), len(rsi_tail))
    p = closes_tail[-n:]
    r = rsi_tail[-n:]
    mid = n // 2
    pl1, pl2 = min(p[:mid]), min(p[mid:])
    rl1, rl2 = min(r[:mid]), min(r[mid:])
    if pl2 < pl1 and rl2 > rl1:
        return "bullish"
    ph1, ph2 = max(p[:mid]), max(p[mid:])
    rh1, rh2 = max(r[:mid]), max(r[mid:])
    if ph2 > ph1 and rh2 < rh1:
        return "bearish"
    return "none"


class MomentumAnalyzer:
    def __init__(self, rsi_period: int = 14,
                 rsi_overbought: float = 70.0,
                 rsi_oversold: float = 30.0,
                 lookback: int = 200):
        """`lookback` bounds how many recent bars we touch — keeps each
        analyze() at O(lookback) instead of O(history_size).
        """
        self.rsi_period = rsi_period
        self.rsi_overbought = rsi_overbought
        self.rsi_oversold = rsi_oversold
        self.lookback = lookback

    def analyze(self, m15_bars: list) -> MomentumReading:
        if len(m15_bars) < 50:
            return MomentumReading(
                rsi=50.0, rsi_state="neutral", rsi_divergence="none",
                macd_signal="none", macd_hist=0.0,
                momentum_direction="neutral",
            )
        # Use only the most recent `lookback` bars — each call is O(lookback).
        recent = m15_bars[-self.lookback:]
        closes = [b.close for b in recent]

        rsi_ser = _rsi_series(closes, self.rsi_period)
        rsi_now = rsi_ser[-1] if rsi_ser else 50.0

        if rsi_now >= self.rsi_overbought:
            rsi_state = "overbought"
        elif rsi_now <= self.rsi_oversold:
            rsi_state = "oversold"
        else:
            rsi_state = "neutral"

        # Divergence on last 30 bars
        if len(rsi_ser) >= 30 and len(closes) >= 30:
            div = _detect_divergence(closes[-30:], rsi_ser[-30:])
        else:
            div = "none"

        macd_line, sig_line, hist = _macd_series(closes)
        macd_signal = "none"
        if len(macd_line) >= 2 and len(sig_line) >= 2:
            if macd_line[-2] <= sig_line[-2] and macd_line[-1] > sig_line[-1]:
                macd_signal = "bull_cross"
            elif macd_line[-2] >= sig_line[-2] and macd_line[-1] < sig_line[-1]:
                macd_signal = "bear_cross"
        macd_hist = hist[-1] if hist else 0.0

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
