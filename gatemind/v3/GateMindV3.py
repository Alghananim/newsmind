# -*- coding: utf-8 -*-
"""GateMindV3 V4 — orchestrator with stopwatch + intelligence + contradictions.

Default-deny architecture preserved. New: per-stage latency, intelligence scores,
hidden-contradiction detection. Speed budget 5ms (current actual ~0.03ms).
"""
from __future__ import annotations
from datetime import datetime, timezone
try:
    from zoneinfo import ZoneInfo
    _HAS_ZONEINFO = True
except Exception:
    _HAS_ZONEINFO = False

from typing import Optional
from .models import GateDecision, BrainSummary, SystemState
from . import (alignment, risk_check, session, news_gate,
               execution_check, state_check, decision_engine,
               contradictions, scoring, latency)


TARGET_TOTAL_MS = 5.0


class GateMindV3:
    def decide(self, *, pair: str,
               news: Optional[BrainSummary],
               market: Optional[BrainSummary],
               chart: Optional[BrainSummary],
               state: Optional[SystemState],
               entry_price: Optional[float] = None,
               stop_loss: Optional[float] = None,
               take_profit: Optional[float] = None,
               position_size: Optional[float] = None,
               atr: float = 0.0,
               min_confidence: float = 0.6,
               now_utc: Optional[datetime] = None) -> GateDecision:
        now = now_utc or datetime.now(timezone.utc)
        sw = latency.Stopwatch().start()

        # Stage: input_parse
        with sw.stage("input_parse"):
            d = GateDecision(timestamp_utc=now, pair=pair,
                             entry_price=entry_price, stop_loss=stop_loss,
                             take_profit=take_profit, position_size=position_size)
            for label, b in (("news", news), ("market", market), ("chart", chart)):
                if b:
                    d.grades_received[label] = b.grade
                    d.permissions_received[label] = b.permission
                    d.confidences_received[label] = b.confidence

        # Stage: alignment
        with sw.stage("alignment_check"):
            try:
                ali = alignment.check(news, market, chart)
                d.alignment_status = ali["status"]
            except Exception as e:
                ali = {"status":"missing","direction":"none","details":f"err:{e}"}
                d.alignment_status = "missing"

        # Stage: risk
        with sw.stage("risk_check"):
            try:
                r = risk_check.check(entry=entry_price, stop=stop_loss,
                                    target=take_profit, atr=atr)
                d.risk_check_status = r["status"]
                if r["rr"] is not None: d.risk_reward = r["rr"]
            except Exception as e:
                r = {"status":"missing","rr":None,"details":f"err:{e}"}
                d.risk_check_status = "missing"

        # Stage: session
        ny_minute = -1
        with sw.stage("session_check"):
            try:
                s = session.check(now)
                d.session_check_status = s["status"]
                # Extract NY minute for contradiction check
                if _HAS_ZONEINFO and now.tzinfo is not None:
                    ny = now.astimezone(ZoneInfo("America/New_York"))
                    ny_minute = ny.minute
            except Exception as e:
                s = {"status":"outside","details":f"err:{e}"}
                d.session_check_status = "outside"

        # Stage: news_gate
        with sw.stage("news_gate"):
            try:
                ng = news_gate.check(news)
                d.news_check_status = ng["status"]
            except Exception as e:
                ng = {"status":"block","details":f"err:{e}"}
                d.news_check_status = "block"

        # Stage: execution
        with sw.stage("execution_check"):
            try:
                if state is None:
                    ec = {"status":"broker_unsafe","pair_status":"unknown","details":"no_state"}
                else:
                    ec = execution_check.check(
                        pair=pair, broker_mode=state.broker_mode,
                        live_enabled=state.live_enabled,
                        spread_pips=state.spread_pips,
                        max_spread_pips=state.max_spread_pips,
                        slippage_pips=state.expected_slippage_pips,
                        max_slippage_pips=state.max_slippage_pips,
                    )
                d.execution_check_status = ec["status"]
                d.spread_check_status = ("ok" if ec["status"]=="ok" else
                    ("dangerous" if ec["status"]=="spread_too_wide" else
                     ("unknown" if "unknown" in ec["status"] else "ok")))
                d.slippage_check_status = ("ok" if ec["status"]=="ok" else
                    ("high" if ec["status"]=="slippage_too_high" else "unknown"))
            except Exception as e:
                ec = {"status":"broker_unsafe","details":f"err:{e}"}
                d.execution_check_status = "broker_unsafe"

        # Stage: daily_limits + state
        with sw.stage("daily_limits_check"):
            try:
                sc = state_check.check(state, now)
                d.position_state_status = sc["position_state"]
                d.daily_limits_status = sc["daily_limits"]
            except Exception as e:
                sc = {"position_state":"missing","daily_limits":"missing","details":f"err:{e}"}
                d.position_state_status = "missing"

        if state:
            d.broker_mode = state.broker_mode
            d.live_enabled = state.live_enabled

        # Stage: contradictions (hidden traps)
        with sw.stage("contradictions"):
            try:
                contr = contradictions.detect(
                    news=news, market=market, chart=chart, state=state,
                    now_utc=now, rr=d.risk_reward,
                    session_status=d.session_check_status,
                    ny_minute=ny_minute,
                )
                d.contradictions_detected = contr.labels()
            except Exception as e:
                contr = contradictions.ContradictionResult()
                d.contradictions_detected = (f"contradiction_engine_error:{e}",)

        # Stage: final_decision (synthesis)
        with sw.stage("final_decision"):
            d = decision_engine.synthesize(
                d, news=news, market=market, chart=chart,
                alignment=ali, risk=r, session=s, news_gate=ng,
                execution=ec, state=sc, min_confidence=min_confidence,
            )

            # Apply contradiction overrides AFTER synthesis
            contr_perm, contr_floor = contradictions.severity_to_outcome(contr)
            if contr_perm == "block" and d.final_decision != "block":
                d.final_decision = "block"
                d.approved = False
                d.direction = "none"
                d.blocking_reasons = d.blocking_reasons + (
                    f"contradiction_block:{','.join(contr.labels())}",)
                d.reason = "BLOCK: contradiction_block: " + ",".join(contr.labels())
            elif contr_perm == "wait" and d.final_decision == "enter":
                d.final_decision = "wait"
                d.approved = False
                d.direction = "none"
                d.warnings = d.warnings + (
                    f"contradiction_wait:{','.join(contr.labels())}",)
                d.reason = "WAIT: contradiction_wait: " + ",".join(contr.labels())
            # Even MEDIUM contradictions block enter (any doubt → wait)
            elif contr.medium and d.final_decision == "enter":
                d.final_decision = "wait"
                d.approved = False
                d.direction = "none"
                d.warnings = d.warnings + (
                    f"contradiction_medium:{','.join(contr.labels())}",)
                d.reason = "WAIT: medium_contradiction: " + ",".join(contr.labels())

        sw.stop()

        # Compute scores + populate latency fields
        d.total_gate_latency_ms = round(sw.total_ms, 4)
        d.input_parse_latency_ms = round(sw.stages.get("input_parse", 0), 4)
        d.alignment_check_latency_ms = round(sw.stages.get("alignment_check", 0), 4)
        d.risk_check_latency_ms = round(sw.stages.get("risk_check", 0), 4)
        d.session_check_latency_ms = round(sw.stages.get("session_check", 0), 4)
        d.spread_check_latency_ms = round(sw.stages.get("execution_check", 0), 4)
        d.execution_check_latency_ms = round(sw.stages.get("execution_check", 0), 4)
        d.daily_limits_check_latency_ms = round(sw.stages.get("daily_limits_check", 0), 4)
        d.final_decision_latency_ms = round(sw.stages.get("final_decision", 0), 4)
        d.bottleneck_stage = sw.bottleneck_stage
        d.stages_breakdown = {k: round(v, 4) for k, v in sw.stages.items()}

        # Scores
        d.alignment_score = scoring.alignment_score(d.alignment_status)
        d.risk_score = scoring.risk_score(d.risk_check_status)
        d.execution_safety_score = scoring.execution_safety_score(d.execution_check_status)
        d.session_safety_score = scoring.session_safety_score(d.session_check_status)
        d.data_quality_score = scoring.data_quality_score(d.position_state_status)
        d.gate_speed_score = scoring.speed_score(sw.total_ms, target_ms=TARGET_TOTAL_MS)
        d.gate_intelligence_score = scoring.gate_intelligence_score(
            alignment=d.alignment_score, risk=d.risk_score,
            execution=d.execution_safety_score, session=d.session_safety_score,
            data_quality=d.data_quality_score,
            contradictions=len(d.contradictions_detected),
            confidence_summary=d.confidence_summary,
        )
        return d
