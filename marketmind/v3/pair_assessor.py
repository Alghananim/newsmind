# -*- coding: utf-8 -*-
"""Per-pair specific assessment.

EUR/USD: focuses on USD strength, ECB context, London/NY overlap
USD/JPY: focuses on USD strength + JPY haven flow + intervention risk
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List
from .models import Bar


@dataclass
class PairContext:
    pair: str
    warnings: tuple = ()
    notes: tuple = ()


def assess(*, pair: str, dollar_bias: str, counter_bias: str,
           dxy_dir: str, regime: str, risk_mode: str,
           bars: List[Bar],
           bars_other_usd_pair: list = None) -> PairContext:
    """`bars_other_usd_pair`: for EUR/USD pass USD/JPY bars, and vice versa,
    so we can detect coincident-direction (broken USD signal)."""
    warnings = []
    notes = []

    if pair == "EUR/USD":
        # If DXY up but EUR/USD up over recent window — broken correlation
        if len(bars) >= 5:
            recent_pct = (bars[-1].close - bars[-5].close) / bars[-5].close
            if dxy_dir == "up" and recent_pct > 0.002:
                warnings.append("dxy_up_but_eurusd_up:correlation_break")
            if dxy_dir == "down" and recent_pct < -0.002:
                warnings.append("dxy_down_but_eurusd_down:correlation_break")
        # Coincident direction check: EUR/USD up + USD/JPY up = inconsistent USD
        if bars_other_usd_pair and len(bars_other_usd_pair) >= 11 and len(bars) >= 11:
            eur_pct = (bars[-1].close - bars[-11].close) / bars[-11].close
            jpy_pct = (bars_other_usd_pair[-1].close - bars_other_usd_pair[-11].close) / bars_other_usd_pair[-11].close
            if eur_pct > 0.001 and jpy_pct > 0.001:
                warnings.append(f"eurusd_up_with_usdjpy_up:inconsistent_usd")
            elif eur_pct < -0.001 and jpy_pct < -0.001:
                warnings.append(f"eurusd_down_with_usdjpy_down:inconsistent_usd")
        notes.append(f"eurusd_pair_logic:dollar={dollar_bias},eur={counter_bias}")

    elif pair == "USD/JPY":
        # Risk-off + USD/JPY rising = dangerous (JPY should be strengthening)
        if len(bars) >= 5:
            recent_pct = (bars[-1].close - bars[-5].close) / bars[-5].close
            if risk_mode == "risk_off" and recent_pct > 0.0005:    # 0.05% is enough on USD/JPY
                warnings.append("risk_off_but_usdjpy_up:dangerous")
            # BoJ intervention spike: huge candle (>3× ATR) without news = suspicious
            if len(bars) >= 14:
                from .regime_detector import _atr
                atr = _atr(bars[-15:])
                last_range = bars[-1].high - bars[-1].low
                if atr > 0 and last_range > 3 * atr:
                    warnings.append(f"usdjpy_spike:{last_range/atr:.1f}x_atr")
        notes.append(f"usdjpy_pair_logic:dollar={dollar_bias},jpy={counter_bias},risk={risk_mode}")

    return PairContext(pair=pair, warnings=tuple(warnings), notes=tuple(notes))
