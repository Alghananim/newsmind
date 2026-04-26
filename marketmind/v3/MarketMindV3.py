# -*- coding: utf-8 -*-
"""MarketMind v3.5 — orchestrator with speed + intelligence instrumentation.

Contract:
    mm = MarketMindV3()
    a  = mm.assess(pair, baskets={...}, bars_xau=..., bars_spx=...,
                   news_verdict=..., now_utc=...)

Every stage is timed via Stopwatch. Indicators are memoized via cache.
Final assessment carries: decision_latency_ms, sources_used, stale_sources,
intelligence_score, speed_score, scores per dimension, contradictions,
cross_market_confirmation, bottleneck_stage.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from typing import Optional, List, Dict
from .models import MarketAssessment, Bar
from . import (regime_detector, strength_index, synthetic_dxy,
               risk_sentiment, correlation, data_quality,
               news_alignment, pair_assessor, permission_engine,
               contradictions, scoring, cache, latency)


# Latency thresholds (ms). Source older than this = stale.
SOURCE_STALE_MS = 500
TARGET_TOTAL_MS = 50


def _session(now_utc: datetime) -> str:
    h = now_utc.hour
    if 0 <= h < 7: return "asia"
    if 7 <= h < 12: return "london"
    if 12 <= h < 16: return "london_ny"
    if 16 <= h < 21: return "ny"
    return "off"


def _spread_label(spread_pips: float, pair: str) -> str:
    if pair == "EUR/USD":
        if spread_pips <= 0.7: return "tight"
        if spread_pips <= 1.5: return "normal"
        if spread_pips <= 3.0: return "wide"
        return "dangerous"
    if pair == "USD/JPY":
        if spread_pips <= 1.0: return "tight"
        if spread_pips <= 2.0: return "normal"
        if spread_pips <= 4.0: return "wide"
        return "dangerous"
    if spread_pips <= 1.5: return "normal"
    if spread_pips <= 3.0: return "wide"
    return "dangerous"


def _liquidity_label(volume: float, avg_volume: float) -> str:
    if avg_volume == 0: return "unclear"
    ratio = volume / avg_volume
    if ratio >= 0.8: return "good"
    if ratio >= 0.4: return "thin"
    return "poor"


def _volatility_label(atr: float, atr_p95: float) -> str:
    if atr_p95 == 0: return "unclear"
    r = atr / atr_p95
    if r > 2.5: return "extreme"
    if r > 1.5: return "high"
    if r >= 0.4: return "normal"
    return "low"


class MarketMindV3:
    def assess(self, *, pair: str,
               baskets: Dict[str, List[Bar]],
               bars_xau: Optional[List[Bar]] = None,
               bars_spx: Optional[List[Bar]] = None,
               news_verdict=None,
               now_utc: Optional[datetime] = None,
               # Optional: per-source latency hints from caller (live mode)
               source_latencies_ms: Optional[Dict[str, float]] = None,
               ) -> MarketAssessment:
        now = now_utc or datetime.now(timezone.utc)
        sw = latency.Stopwatch().start()

        # Track sources used for transparency
        sources_used = list(baskets.keys())
        if bars_xau: sources_used.append("XAU/USD")
        if bars_spx: sources_used.append("SPX500")
        if news_verdict is not None: sources_used.append("NewsMind")
        stale_sources = []

        # Apply per-source latency hints + flag stale ones
        stale_count = 0
        if source_latencies_ms:
            for src, ms in source_latencies_ms.items():
                sw.record_source(src, ms,
                                 status="stale" if ms > SOURCE_STALE_MS else "ok")
                if ms > SOURCE_STALE_MS:
                    stale_sources.append(src)
                    stale_count += 1
        # If >=50% of sources stale, this is an unreliable input set — cap at B
        many_stale_warning = None
        if source_latencies_ms and stale_count >= max(1, len(source_latencies_ms) // 2):
            many_stale_warning = f"many_stale_sources:{stale_count}/{len(source_latencies_ms)}"

        # Sanity
        bars = baskets.get(pair, [])
        if not bars:
            sw.stop()
            return MarketAssessment(
                pair=pair, timestamp_utc=now,
                data_quality_status="missing",
                trade_permission="block", grade="C",
                reason="no_bars_for_pair",
                sources_used=tuple(sources_used),
                stale_sources=tuple(stale_sources),
                decision_latency_ms=round(sw.total_ms, 2),
            )

        # Stage 1: data quality
        with sw.stage("data_quality"):
            dq_status, dq_warnings = data_quality.assess(bars=bars, now_utc=now)

        # Stage 2: regime / direction / strength
        with sw.stage("regime"):
            regime, direction, strength, regime_diag = regime_detector.classify_regime(bars)

        # Stage 3: risk sentiment
        with sw.stage("risk_sentiment"):
            news_risk = "unclear"
            if news_verdict is not None:
                if hasattr(news_verdict, "risk_mode"):
                    news_risk = news_verdict.risk_mode or "unclear"
                elif hasattr(news_verdict, "get"):
                    news_risk = news_verdict.get("risk_mode", "unclear")
            risk = risk_sentiment.assess(
                bars_xau=bars_xau, bars_spx=bars_spx,
                bars_usdjpy=baskets.get("USD/JPY"),
                news_risk_mode=news_risk,
            )

        # Stage 4: strength + DXY
        with sw.stage("strength_dxy"):
            strength_snap = strength_index.assess_for_pair(
                pair=pair, baskets=baskets, risk_mode=risk.risk_mode)
            dxy = synthetic_dxy.compute(baskets=baskets)

        # Stage 4b: DXY adequacy gate — count of USD pairs in basket.
        # A single pair gives misleading dxy_dir; require >= 2 USD pairs.
        usd_pairs_in_basket = sum(1 for k in baskets.keys()
                                  if any(c in k for c in ("USD",)))
        dxy_low_coverage_warning = None
        if usd_pairs_in_basket < 2 or dxy.coverage < 0.7:
            dxy_low_coverage_warning = (
                f"dxy_coverage_{dxy.coverage:.2f}_pairs_{usd_pairs_in_basket}_below_threshold")

        # Stage 5: correlation
        with sw.stage("correlation"):
            corr = correlation.assess(
                bars_eurusd=baskets.get("EUR/USD"),
                bars_usdjpy=baskets.get("USD/JPY"),
                bars_xau=bars_xau, bars_spx=bars_spx,
            )

        # Stage 6: pair-specific
        with sw.stage("pair_logic"):
            other_pair = "USD/JPY" if pair == "EUR/USD" else "EUR/USD"
            pair_ctx = pair_assessor.assess(
                pair=pair, dollar_bias=strength_snap.dollar,
                counter_bias=strength_snap.counter,
                dxy_dir=dxy.direction, regime=regime, risk_mode=risk.risk_mode,
                bars=bars,
                bars_other_usd_pair=baskets.get(other_pair),
            )

        # Stage 7: news alignment
        with sw.stage("news_alignment"):
            align = news_alignment.assess(news_verdict, direction)

        # Stage 8: contradictions detection
        with sw.stage("contradictions"):
            news_bias = "unclear"
            news_perm = "allow"
            if news_verdict is not None:
                news_bias = (getattr(news_verdict, "market_bias", None)
                             or (news_verdict.get("market_bias", "unclear")
                                 if hasattr(news_verdict, "get") else "unclear"))
                news_perm = (getattr(news_verdict, "trade_permission", None)
                             or (news_verdict.get("trade_permission", "allow")
                                 if hasattr(news_verdict, "get") else "allow"))
            contr = contradictions.detect(
                pair=pair, bars=bars,
                dxy_dir=dxy.direction, dxy_strength=dxy.strength,
                bars_usdjpy=baskets.get("USD/JPY"),
                bars_eurusd=baskets.get("EUR/USD"),
                bars_xau=bars_xau, bars_spx=bars_spx,
                news_bias=news_bias, news_perm=news_perm,
                risk_mode=risk.risk_mode,
                market_direction=direction,
            )
            contr_perm_override, contr_grade_cap = contradictions.severity_to_outcome(contr)

        # Stage 9: labels
        with sw.stage("labels"):
            from .regime_detector import _atr, _atr_percentile
            atr = _atr(bars)
            atr_p95 = _atr_percentile(bars)
            vol_label = _volatility_label(atr, atr_p95)
            spread_label = _spread_label(bars[-1].spread_pips or 1.0, pair)
            avg_vol = sum((b.volume or 0) for b in bars[-20:]) / max(1, min(20, len(bars)))
            liq_label = _liquidity_label(bars[-1].volume or 0, avg_vol)

            gold_sig = "unavailable"
            if bars_xau and len(bars_xau) >= 21:
                chg = (bars_xau[-1].close - bars_xau[-20].close) / bars_xau[-20].close
                gold_sig = "rising" if chg > 0.005 else ("falling" if chg < -0.005 else "flat")

        # Build assessment
        warnings = list(dq_warnings) + list(pair_ctx.warnings) + list(contr.labels())
        if dxy_low_coverage_warning: warnings.append(dxy_low_coverage_warning)
        if many_stale_warning: warnings.append(many_stale_warning)
        if corr.anomalies: warnings.extend(corr.anomalies)

        a = MarketAssessment(
            pair=pair, timestamp_utc=now,
            session=_session(now),
            market_regime=regime, direction=direction, trend_strength=strength,
            dollar_bias=strength_snap.dollar, counter_currency_bias=strength_snap.counter,
            yield_signal="unavailable",
            gold_signal=gold_sig,
            risk_mode=risk.risk_mode,
            volatility_level=vol_label,
            liquidity_condition=liq_label,
            spread_condition=spread_label,
            correlation_status=corr.status,
            news_alignment=align.label,
            data_quality_status=dq_status,
            warnings=tuple(warnings),
            sources_used=tuple(sources_used),
            stale_sources=tuple(stale_sources),
            contradictions_detected=tuple(contr.labels()),
            reason=(f"regime_diag={regime_diag}|dxy={dxy.direction}/{dxy.strength}"
                    f"|risk={risk.risk_mode}/{risk.confidence}"
                    f"|corr={corr.status}/{corr.pairs}"
                    f"|contr={contr.summary()}"),
        )

        # Stage 10: permission engine — apply news cap AND contradiction outcome
        with sw.stage("permission"):
            # First the standard finalize
            news_cap = align.grade_cap
            # Pick the more restrictive of news_cap and contr_grade_cap
            GRADE_RANK = {"A+":4,"A":3,"B":2,"C":1}
            effective_cap = (contr_grade_cap if GRADE_RANK[contr_grade_cap] < GRADE_RANK[news_cap]
                             else news_cap)
            a = permission_engine.finalize(a, news_grade_cap=effective_cap)

            # Apply contradiction permission override if more restrictive
            if contr_perm_override == "block":
                a.trade_permission = "block"
                a.grade = "C"
                a.reason = (a.reason + "|contradiction_block:" +
                            ",".join(contr.labels()))
            elif contr_perm_override == "wait" and a.trade_permission == "allow":
                a.trade_permission = "wait"
                if a.grade in ("A+","A"): a.grade = "B"
                a.reason = (a.reason + "|contradiction_wait:" +
                            ",".join(contr.labels()))

        # Stop the clock
        sw.stop()

        # Compute scores
        a.trend_score = scoring.trend_score(regime, strength)
        a.volatility_score = scoring.volatility_score(vol_label)
        a.liquidity_score = scoring.liquidity_score(liq_label)
        a.data_quality_score = scoring.data_quality_score(dq_status)
        a.speed_score = scoring.speed_score(sw.total_ms, target_ms=TARGET_TOTAL_MS)
        a.market_intelligence_score = scoring.market_intelligence_score(
            trend=a.trend_score, vol=a.volatility_score,
            liq=a.liquidity_score,
            spread=scoring.spread_score(spread_label),
            dq=a.data_quality_score,
            contradictions=len(contr.items),
            correlation_status=corr.status,
            news_aligned=(align.label == "aligned"),
        )
        a.cross_market_confirmation = scoring.cross_market_confirmation(
            dxy_dir=dxy.direction, market_direction=direction,
            risk_mode=risk.risk_mode,
            news_aligned=(align.label == "aligned"),
            corr_status=corr.status,
        )
        a.confidence = round((a.market_intelligence_score + a.speed_score) / 2.0, 3)
        a.decision_latency_ms = round(sw.total_ms, 2)
        a.data_latency_ms = round(max(
            (s["ms"] for s in sw.sources_latency.values()), default=0.0), 2)
        a.cache_stats = cache.stats()
        sw_dict = sw.to_dict()
        a.bottleneck_stage = sw_dict.get("bottleneck_stage") or ""
        return a
