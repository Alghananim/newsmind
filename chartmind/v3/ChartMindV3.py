# -*- coding: utf-8 -*-
"""ChartMindV3 — orchestrator (V4: with cache + latency + scoring).

Stages timed individually:
    data_load
    structure
    support_resistance
    trend
    candle
    breakout
    pullback
    multi_timeframe
    entry_quality
    risk_reward
    traps
    permission
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Optional
from .models import ChartAssessment, Bar
from . import (market_structure, support_resistance, candles, breakout,
               pullback, trend, traps, multi_timeframe, entry_quality,
               stop_target, permission_engine, scoring, latency, cache)
from .trend import _atr


TARGET_LATENCY_MS = 50


def _vol_label(atr: float, atr_mean: float) -> str:
    if atr_mean == 0: return "unclear"
    r = atr / atr_mean
    if r > 2.0: return "extreme"
    if r > 1.3: return "high"
    if r >= 0.5: return "normal"
    return "low"


def _atr_status(atr: float, prev_atr: float) -> str:
    if prev_atr == 0: return "unclear"
    r = atr / prev_atr
    if r > 1.4: return "wide"
    if r < 0.7: return "tight"
    return "normal"


class ChartMindV3:
    def assess(self, *, pair: str,
               bars_m15: List[Bar],
               bars_m5: Optional[List[Bar]] = None,
               bars_m1: Optional[List[Bar]] = None,
               now_utc: Optional[datetime] = None) -> ChartAssessment:
        now = now_utc or datetime.now(timezone.utc)
        sw = latency.Stopwatch().start()

        with sw.stage("data_load"):
            if not bars_m15 or len(bars_m15) < 6:
                sw.stop()
                a = ChartAssessment(
                    pair=pair, timestamp_utc=now,
                    trade_permission="block", grade="C",
                    reason="insufficient_m15_bars",
                    warnings=("insufficient_m15_bars",),
                    chart_analysis_latency_ms=round(sw.total_ms, 3),
                    bottleneck_stage="data_load",
                )
                return a
            used_tfs = ["M15"]
            if bars_m5 and len(bars_m5) >= 6: used_tfs.append("M5")
            if bars_m1 and len(bars_m1) >= 6: used_tfs.append("M1")

        # Stage: structure
        with sw.stage("structure"):
            struct = market_structure.classify(bars_m15)

        # Stage: ATR (feature_calc) — used downstream
        with sw.stage("feature_calc"):
            atr = _atr(bars_m15)
            # Long-window ATR as baseline for vol comparison (cached, single call)
            atr_mean = _atr(bars_m15, period=50) if len(bars_m15) >= 50 else atr

        # Stage: support/resistance
        with sw.stage("support_resistance"):
            supports, resistances = support_resistance.levels_from_bars(
                bars_m15, atr=atr)
            nearest_price, nearest_role, nearest_level = support_resistance.nearest_key(
                bars_m15[-1].close, supports, resistances)
            nearest_dist_atr = (abs(bars_m15[-1].close - nearest_price) / atr
                                if nearest_price and atr > 0 else 0)

        # Stage: trend
        with sw.stage("trend"):
            tr = trend.assess(bars_m15)

        # Stage: candle
        with sw.stage("candle"):
            candle_bars = bars_m5 if bars_m5 and len(bars_m5) >= 6 else bars_m15
            candle_atr = _atr(candle_bars)
            cd = candles.detect(candle_bars, candle_atr,
                               nearest_level_price=nearest_price,
                               nearest_level_role=nearest_role,
                               trend_dir=tr["direction"])

        # Stage: breakout (test both directions, pick most actionable)
        with sw.stage("breakout"):
            bk_dir_primary = "up" if tr["direction"] in ("bullish", "neutral") else "down"
            bk_candidates = []
            if resistances:
                top_res = max(resistances, key=lambda r: r.price)
                bk_up = breakout.assess(bars_m15, top_res.price, atr, direction="up")
                bk_candidates.append(("up", top_res.price, bk_up))
            if supports:
                bot_sup = min(supports, key=lambda s: s.price)
                bk_dn = breakout.assess(bars_m15, bot_sup.price, atr, direction="down")
                bk_candidates.append(("down", bot_sup.price, bk_dn))
            STATUS_PRIO = {"fake": 5, "real": 4, "weak": 3, "pending": 2, "none": 0}
            if bk_candidates:
                bk_dir, bk_level, bk = max(bk_candidates,
                                            key=lambda x: STATUS_PRIO.get(x[2]["status"], 0))
            else:
                bk_dir, bk_level = bk_dir_primary, nearest_price or bars_m15[-1].close
                bk = breakout.assess(bars_m15, bk_level, atr, direction=bk_dir)

        # Stage: pullback
        with sw.stage("pullback"):
            pb = pullback.assess(bars_m15, bk_level, atr,
                                breakout_idx=bk.get("last_break_idx"),
                                direction=bk_dir)

        # Stage: MTF
        with sw.stage("multi_timeframe"):
            mtf = multi_timeframe.assess(bars_m15=bars_m15, bars_m5=bars_m5, bars_m1=bars_m1)

        # Stage: entry_quality
        with sw.stage("entry_quality"):
            opposing = None
            if tr["direction"] == "bullish":
                opposing_levels = [r.price for r in resistances if r.price > bars_m15[-1].close]
                if opposing_levels: opposing = abs(min(opposing_levels) - bars_m15[-1].close)
            elif tr["direction"] == "bearish":
                opposing_levels = [s.price for s in supports if s.price < bars_m15[-1].close]
                if opposing_levels: opposing = abs(max(opposing_levels) - bars_m15[-1].close)
            eq = entry_quality.assess(candle_bars, candle_atr,
                                      direction=tr["direction"],
                                      nearest_opposing_distance=opposing)

        # Stage: risk_reward
        with sw.stage("risk_reward"):
            st = stop_target.compute(
                bars=bars_m15, atr=atr, direction=tr["direction"],
                supports=supports, resistances=resistances)

        # Stage: traps
        with sw.stage("traps"):
            liq_sweep = traps.liquidity_sweep(bars_m15, nearest_price, atr,
                                              direction=bk_dir)
            bull_trap = traps.bull_trap(bars_m15, nearest_price, atr,
                                        higher_tf_trend=tr["direction"])
            bear_trap = traps.bear_trap(bars_m15, nearest_price, atr,
                                        higher_tf_trend=tr["direction"])
            chop_trap = traps.chop_trap(bars_m15)
            stop_hunt = traps.stop_hunt(bars_m15, nearest_price, atr)

        # Volatility labels
        vol_status = _vol_label(atr, atr_mean)
        prev_atr = _atr(bars_m15[:-1]) if len(bars_m15) > 1 else atr
        atr_status = _atr_status(atr, prev_atr)

        warnings = []
        if liq_sweep: warnings.append("liquidity_sweep_at_level")
        if bull_trap: warnings.append("bull_trap_detected")
        if bear_trap: warnings.append("bear_trap_detected")
        if chop_trap: warnings.append("chop_trap_detected")
        if stop_hunt: warnings.append("stop_hunt_pattern")
        if mtf.get("label") == "conflicting":
            warnings.append(f"mtf_conflict:{mtf.get('details','')}")
        if nearest_dist_atr < 0.3 and tr["direction"] in ("bullish","bearish"):
            opposing_role = "resistance" if tr["direction"] == "bullish" else "support"
            if nearest_role == opposing_role:
                warnings.append(f"key_level_within_0.3atr_{opposing_role}")

        # Breakout-direction vs trend-direction conflict — opposing signals
        if (tr["direction"] == "bullish" and bk_dir == "down" and bk["status"] in ("real","weak")):
            warnings.append(f"breakout_direction_conflicts_trend:trend_bull_break_down_{bk['status']}")
        elif (tr["direction"] == "bearish" and bk_dir == "up" and bk["status"] in ("real","weak")):
            warnings.append(f"breakout_direction_conflicts_trend:trend_bear_break_up_{bk['status']}")

        traps_count = sum([liq_sweep, bull_trap, bear_trap, chop_trap, stop_hunt])

        a = ChartAssessment(
            pair=pair, timestamp_utc=now,
            timeframes_used=tuple(used_tfs),
            market_structure=struct["structure"],
            trend_direction=tr["direction"],
            trend_strength=tr["strength"],
            trend_quality=tr["quality"],
            support_levels=tuple((s.price, s.strength) for s in supports),
            resistance_levels=tuple((r.price, r.strength) for r in resistances),
            nearest_key_level=nearest_price,
            nearest_key_distance_atr=round(nearest_dist_atr, 2),
            nearest_key_role=nearest_role,
            candlestick_signal=cd["signal"],
            candlestick_context=cd["context"],
            candlestick_quality=cd["quality"],
            breakout_status=bk["status"],
            retest_status=pb["status"],
            pullback_quality=pb["quality"],
            entry_quality=eq["quality"],
            entry_price_zone=(round(bars_m15[-1].close * 0.9998, 5),
                              round(bars_m15[-1].close * 1.0002, 5)),
            late_entry_risk=eq["late_entry_risk"],
            stop_loss=st["stop"],
            take_profit=st["target"],
            risk_reward=st["rr"],
            stop_logic=st["stop_logic"],
            target_logic=st["target_logic"],
            volatility_status=vol_status,
            atr_status=atr_status,
            fake_breakout_risk=(bk["status"] == "fake"),
            liquidity_sweep_detected=liq_sweep,
            timeframe_alignment=mtf.get("label", "n_a"),
            warnings=tuple(warnings),
            reason=(f"struct={struct['structure']}|trend={tr['direction']}/{tr['strength']}/{tr['quality']}|"
                    f"candle={cd['signal']}/{cd['context']}/{cd['quality']}|"
                    f"break={bk['status']}|retest={pb['status']}|"
                    f"entry={eq['quality']}|rr={st['rr']}|mtf={mtf.get('label','n_a')}|"
                    f"vol={vol_status}|atr={atr_status}"),
        )

        # Stage: permission
        with sw.stage("permission"):
            a = permission_engine.finalize(a)

        sw.stop()

        # Compute scores + populate latency fields
        a.chart_analysis_latency_ms = round(sw.total_ms, 3)
        a.data_load_latency_ms = round(sw.stages.get("data_load", 0), 3)
        a.feature_calc_latency_ms = round(sw.stages.get("feature_calc", 0), 3)
        a.structure_analysis_latency_ms = round(sw.stages.get("structure", 0), 3)
        a.candlestick_analysis_latency_ms = round(sw.stages.get("candle", 0), 3)
        a.support_resistance_latency_ms = round(sw.stages.get("support_resistance", 0), 3)
        a.breakout_detection_latency_ms = round(sw.stages.get("breakout", 0), 3)
        a.risk_reward_calc_latency_ms = round(sw.stages.get("risk_reward", 0), 3)
        a.bottleneck_stage = sw.bottleneck_stage
        a.stages_breakdown = {k: round(v, 3) for k, v in sw.stages.items()}
        a.cache_stats = cache.stats()

        a.data_quality_score = scoring.data_quality_score(
            "good" if len(bars_m15) >= 30 else "partial")
        a.entry_quality_score = scoring.entry_quality_score(a.entry_quality)
        a.timeframe_alignment_score = scoring.timeframe_alignment_score(a.timeframe_alignment)
        a.speed_score = scoring.speed_score(sw.total_ms, target_ms=TARGET_LATENCY_MS)
        a.chart_intelligence_score = scoring.chart_intelligence_score(
            structure=scoring.structure_score(a.market_structure),
            trend_q=scoring.trend_quality_score(a.trend_quality),
            candle=scoring.candle_score(a.candlestick_quality),
            breakout=scoring.breakout_score(a.breakout_status),
            retest=scoring.retest_score(a.retest_status),
            entry_q=a.entry_quality_score,
            vol=scoring.volatility_score(a.volatility_status),
            mtf=a.timeframe_alignment_score,
            traps_count=traps_count,
            rr=a.risk_reward or 1.0,
        )
        return a
