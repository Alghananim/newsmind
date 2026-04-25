# -*- coding: utf-8 -*-
"""EntryPlanner — turns a directional decision into entry/SL/TP/RR.

Stop placement
--------------
   * LONG: stop = min(current_low_5_bars, nearest_support) - buffer
   * SHORT: stop = max(current_high_5_bars, nearest_resistance) + buffer
   * Buffer = 0.3 × ATR (so we don't get knocked out by noise)
   * SL distance is clamped to [min_sl_pips, max_sl_pips]

Target placement
----------------
   * Default: target = entry + (entry - stop) × min_rr  (R:R 2:1)
   * If a structure level lies in the trade direction, prefer it
     when its R:R >= min_rr.

Time budget
-----------
   * 12 bars on M15 = 3 hours. Day-trading windows are usually 2-4h.
"""
from __future__ import annotations
from typing import Optional
from .models import (TradePlan, TrendReading, StructureReading,
                     CandleReading, MomentumReading, RegimeReading)


class EntryPlanner:
    def __init__(self,
                 pair_pip: float = 0.0001,
                 min_sl_pips: float = 5.0,
                 max_sl_pips: float = 30.0,
                 sl_buffer_atr_mult: float = 0.3,
                 min_rr: float = 2.0,
                 time_budget_bars: int = 12):
        self.pair_pip = pair_pip
        self.min_sl_pips = min_sl_pips
        self.max_sl_pips = max_sl_pips
        self.sl_buffer_atr_mult = sl_buffer_atr_mult
        self.min_rr = min_rr
        self.time_budget_bars = time_budget_bars

    def plan(self, *,
             direction: str,
             confluence_score: float,
             m15_bars: list,
             trend: TrendReading,
             structure: StructureReading,
             candle: CandleReading,
             momentum: MomentumReading,
             regime: Optional[RegimeReading] = None,
             ) -> Optional[TradePlan]:
        if direction not in ("long", "short") or len(m15_bars) < 20:
            return None

        cur = m15_bars[-1]
        entry = cur.close
        atr = self._atr(m15_bars, 14)
        if atr <= 0:
            return None

        # Stop placement
        buf = self.sl_buffer_atr_mult * atr
        if direction == "long":
            recent_low = min(b.low for b in m15_bars[-5:])
            sup_price = (structure.nearest_support.price
                         if structure.nearest_support else recent_low)
            raw_stop = min(recent_low, sup_price) - buf
            sl_pips = (entry - raw_stop) / self.pair_pip
            sl_pips = max(self.min_sl_pips, min(self.max_sl_pips, sl_pips))
            stop = entry - sl_pips * self.pair_pip
            target = entry + sl_pips * self.min_rr * self.pair_pip
            # Prefer structure target if better R:R
            if structure.nearest_resistance is not None:
                res_target = structure.nearest_resistance.price
                if res_target > entry:
                    res_rr = (res_target - entry) / (entry - stop)
                    if res_rr >= self.min_rr:
                        target = res_target
        else:
            recent_high = max(b.high for b in m15_bars[-5:])
            res_price = (structure.nearest_resistance.price
                         if structure.nearest_resistance else recent_high)
            raw_stop = max(recent_high, res_price) + buf
            sl_pips = (raw_stop - entry) / self.pair_pip
            sl_pips = max(self.min_sl_pips, min(self.max_sl_pips, sl_pips))
            stop = entry + sl_pips * self.pair_pip
            target = entry - sl_pips * self.min_rr * self.pair_pip
            if structure.nearest_support is not None:
                sup_target = structure.nearest_support.price
                if sup_target < entry:
                    sup_rr = (entry - sup_target) / (stop - entry)
                    if sup_rr >= self.min_rr:
                        target = sup_target

        # Compute final R:R
        if direction == "long":
            rr = (target - entry) / (entry - stop) if entry > stop else 0
        else:
            rr = (entry - target) / (stop - entry) if stop > entry else 0
        if rr < self.min_rr * 0.95:
            return None  # SL/TP geometry failed minimum R:R

        # Confidence (0..1) derived from confluence score
        confidence = min(1.0, confluence_score / 6.0)

        return TradePlan(
            setup_type=f"v2_{candle.pattern if candle.pattern != 'none' else 'trend'}",
            direction=direction,
            entry_price=entry, stop_price=stop, target_price=target,
            rr_ratio=rr, time_budget_bars=self.time_budget_bars,
            confidence=confidence,
            rationale=self._rationale(
                direction, confluence_score, trend, candle, momentum, regime),
            is_actionable=True,
            confluence_score=confluence_score,
            risks=self._risks(trend, candle, momentum, regime, atr),
            timing_ok=True,
            trend=trend, structure=structure,
            candle=candle, momentum=momentum, regime=regime,
        )

    def _atr(self, bars: list, period: int) -> float:
        if len(bars) < period + 1:
            return 0.0
        recent = bars[-(period + 1):]
        trs = []
        for i in range(1, len(recent)):
            cur, prev = recent[i], recent[i-1]
            trs.append(max(cur.high - cur.low,
                           abs(cur.high - prev.close),
                           abs(cur.low - prev.close)))
        return sum(trs) / len(trs)

    def _rationale(self, direction, score, trend, candle, momentum, regime) -> str:
        bits = [f"v2 confluence {score:.0f}/6 -> {direction}",
                f"trend {trend.aligned_direction} (h4={trend.h4_direction}/"
                f"h1={trend.h1_direction}/m15={trend.m15_direction})",
                f"adx h1={trend.h1_adx:.0f}/m15={trend.m15_adx:.0f}",
                f"candle={candle.pattern}@{candle.structure_label}",
                f"rsi={momentum.rsi:.0f}({momentum.rsi_state})/macd={momentum.macd_signal}"]
        if regime:
            bits.append(f"regime={regime.label}")
        return " | ".join(bits)

    def _risks(self, trend, candle, momentum, regime, atr) -> list:
        r = []
        if trend.m15_adx < 18:
            r.append("low_adx")
        if not candle.at_structure:
            r.append("not_at_structure")
        if regime and regime.label in ("RANGING", "QUIET"):
            r.append(f"unfavourable_regime:{regime.label}")
        if momentum.rsi_divergence == "none" and momentum.macd_signal == "none":
            r.append("no_momentum_trigger")
        return r
