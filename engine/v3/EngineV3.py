# -*- coding: utf-8 -*-
"""EngineV3 — wires NewsMind + MarketMind + ChartMind + GateMind + SmartNoteBook
              under strict Live Validation safety rails.

Single decide_and_maybe_trade() per cycle:
  1. Build NewsMind, MarketMind, ChartMind verdicts
  2. Pass to GateMind for final decision
  3. If enter: run safety_rails.check_all() — DOUBLE check
  4. If still ok: compute position size from 0.25% risk
  5. Submit order to broker (paper/live)
  6. SmartNoteBook journal: full mind_outputs + decision + trade

If at any step a brain is None / errors / data missing → block + journal.
"""
from __future__ import annotations
import sys, uuid
from datetime import datetime, timezone
from typing import Optional
import os

# Ensure brain packages are importable (caller must have outputs/ on sys.path)
from newsmind.v2 import NewsMindV2
from marketmind.v3 import MarketMindV3
from chartmind.v3 import ChartMindV3
from gatemind.v3 import GateMindV3, BrainSummary, SystemState
from smartnotebook.v3 import (SmartNoteBookV3, TradeAuditEntry, DecisionEvent,
                               MindOutputs)
from .validation_config import ValidationConfig
from .position_sizer import calculate_position_size
from . import safety_rails


def _brain_summary_from_news(nv) -> BrainSummary:
    if nv is None: return BrainSummary("news", "block", "C", 0.0, "unclear", "missing")
    # NewsVerdict has no `warnings`; fall back to conflicting_sources for audit signal
    warnings = (getattr(nv, "warnings", None)
                or getattr(nv, "conflicting_sources", None)
                or ())
    return BrainSummary(
        name="news", permission=nv.trade_permission, grade=nv.grade,
        confidence=nv.confidence, direction=nv.market_bias, reason=nv.reason,
        warnings=tuple(warnings))


def _brain_summary_from_market(mv) -> BrainSummary:
    if mv is None: return BrainSummary("market", "block", "C", 0.0, "unclear", "missing")
    return BrainSummary(
        name="market", permission=mv.trade_permission, grade=mv.grade,
        confidence=mv.confidence, direction=mv.direction, reason=mv.reason,
        warnings=tuple(getattr(mv, "warnings", ()) or ()))


def _brain_summary_from_chart(cv) -> BrainSummary:
    if cv is None: return BrainSummary("chart", "block", "C", 0.0, "unclear", "missing")
    return BrainSummary(
        name="chart", permission=cv.trade_permission, grade=cv.grade,
        confidence=cv.confidence, direction=cv.trend_direction, reason=cv.reason,
        warnings=tuple(getattr(cv, "warnings", ()) or ()))


def _build_mind_outputs(nv, mv, cv, gd) -> MindOutputs:
    mo = MindOutputs()
    if nv:
        mo.news_grade = nv.grade; mo.news_perm = nv.trade_permission
        mo.news_confidence = nv.confidence; mo.news_bias = nv.market_bias
        mo.news_freshness = nv.freshness_status
        mo.news_impact_level = nv.impact_level
        mo.news_source_type = nv.source_type
        mo.news_verified = nv.verified
        mo.news_market_bias = nv.market_bias
        mo.news_reason = nv.reason
        mo.news_warnings = tuple()
    if mv:
        mo.market_grade = mv.grade; mo.market_perm = mv.trade_permission
        mo.market_confidence = mv.confidence
        mo.market_regime = mv.market_regime
        mo.market_direction = mv.direction
        mo.market_dollar_bias = mv.dollar_bias
        mo.market_risk_mode = mv.risk_mode
        mo.market_volatility = mv.volatility_level
        mo.market_liquidity = mv.liquidity_condition
        mo.market_spread = mv.spread_condition
        mo.market_reason = mv.reason
        mo.market_warnings = tuple(getattr(mv, "warnings", ()) or ())
    if cv:
        mo.chart_grade = cv.grade; mo.chart_perm = cv.trade_permission
        mo.chart_confidence = cv.confidence
        mo.chart_structure = cv.market_structure
        mo.chart_trend_direction = cv.trend_direction
        mo.chart_candle_context = cv.candlestick_context
        mo.chart_breakout_status = cv.breakout_status
        mo.chart_retest_status = cv.retest_status
        mo.chart_entry_quality = cv.entry_quality
        mo.chart_fake_breakout = cv.fake_breakout_risk
        mo.chart_late_entry = cv.late_entry_risk
        mo.chart_stop_loss = cv.stop_loss
        mo.chart_take_profit = cv.take_profit
        mo.chart_rr = cv.risk_reward
        mo.chart_reason = cv.reason
        mo.chart_warnings = tuple(getattr(cv, "warnings", ()) or ())
    if gd:
        mo.gate_decision = gd.final_decision
        mo.gate_approved = gd.approved
        mo.gate_blocking = gd.blocking_reasons
        mo.gate_warnings = gd.warnings
        mo.gate_audit_id = gd.audit_id
        mo.gate_reason = gd.reason
    return mo


class EngineV3:
    def __init__(self, *, cfg: ValidationConfig, broker=None,
                 account_balance: float = 10000.0):
        self.cfg = cfg
        self.broker = broker
        self.account_balance = account_balance
        self.nb = SmartNoteBookV3(cfg.smartnotebook_dir, enable_async=True)
        self.gate = GateMindV3()
        # Stats (would be loaded from notebook in production)
        self.daily_loss_pct = 0.0
        self.consecutive_losses = 0
        self.trades_today = 0

    def decide_and_maybe_trade(self, *, pair: str,
                              news_verdict, market_assessment, chart_assessment,
                              spread_pips: float = 0.5,
                              slippage_pips: float = 0.5,
                              now_utc: Optional[datetime] = None) -> dict:
        now = now_utc or datetime.now(timezone.utc)

        # 1. Build state for GateMind
        state = SystemState(
            pair=pair,
            broker_mode=self.cfg.broker_env,
            live_enabled=(self.cfg.broker_env == "live"),
            spread_pips=spread_pips,
            max_spread_pips=self.cfg.max_spread_pips.get(pair, 2.0),
            expected_slippage_pips=slippage_pips,
            max_slippage_pips=self.cfg.max_slippage_pips,
            daily_loss_pct=self.daily_loss_pct,
            daily_loss_limit_pct=self.cfg.daily_loss_limit_pct,
            trades_today=self.trades_today,
            daily_trade_limit=self.cfg.daily_trade_limit,
            consecutive_losses=self.consecutive_losses,
            pair_status=self.cfg.pair_status.get(pair, "unknown"),
        )

        # 2. GateMind decision
        news_brain = _brain_summary_from_news(news_verdict)
        market_brain = _brain_summary_from_market(market_assessment)
        chart_brain = _brain_summary_from_chart(chart_assessment)
        # Use chart's stop/target for the trade
        gate_decision = self.gate.decide(
            pair=pair,
            news=news_brain, market=market_brain, chart=chart_brain,
            state=state,
            entry_price=getattr(chart_assessment, "nearest_key_level", None) or 1.0,
            stop_loss=getattr(chart_assessment, "stop_loss", None),
            take_profit=getattr(chart_assessment, "take_profit", None),
            atr=0.001,
            min_confidence=0.6, now_utc=now,
        )

        # 3. Always journal the decision
        mo = _build_mind_outputs(news_verdict, market_assessment, chart_assessment, gate_decision)
        event = DecisionEvent(
            event_id=str(uuid.uuid4()),
            audit_id=gate_decision.audit_id,
            timestamp=now,
            event_type="trade" if gate_decision.final_decision == "enter" else gate_decision.final_decision,
            pair=pair, system_mode=self.cfg.broker_env,
            mind_outputs=mo,
            gate_decision=gate_decision.final_decision,
            blocking_reasons=gate_decision.blocking_reasons,
            warnings=gate_decision.warnings,
            rejected_reason=gate_decision.reason if gate_decision.final_decision != "enter" else "",
        )
        self.nb.record_decision(event)

        # 4. If not enter, stop here
        if gate_decision.final_decision != "enter":
            return {"decision": gate_decision.final_decision,
                    "reason": gate_decision.reason,
                    "audit_id": gate_decision.audit_id}

        # 5. Compute position size from 0.25% risk
        ps = calculate_position_size(
            balance=self.account_balance,
            risk_pct=self.cfg.risk_pct_per_trade,
            entry_price=gate_decision.entry_price,
            stop_loss=gate_decision.stop_loss,
            pair=pair,
        )

        # 6. SAFETY RAILS — final hard checks
        smartnotebook_writable = (self.nb.storage_health() in ("ok", "warnings"))
        ok, blocks = safety_rails.check_all(
            gate_decision_result=gate_decision,
            position_size=ps,
            cfg=self.cfg,
            account_balance=self.account_balance,
            daily_loss_pct=self.daily_loss_pct,
            consecutive_losses=self.consecutive_losses,
            trades_today=self.trades_today,
            smartnotebook_writable=smartnotebook_writable,
            spread_pips=spread_pips,
            slippage_pips=slippage_pips,
            pair=pair,
            broker_mode=self.cfg.broker_env,
        )

        if not ok:
            # Override gate's enter — record as block
            self.nb.record_decision(DecisionEvent(
                event_id=str(uuid.uuid4()),
                audit_id=gate_decision.audit_id,
                timestamp=now, event_type="block", pair=pair,
                system_mode=self.cfg.broker_env, mind_outputs=mo,
                gate_decision="block",
                blocking_reasons=tuple(blocks),
                rejected_reason="safety_rails_blocked: " + " | ".join(blocks[:3])))
            return {"decision": "block_by_safety_rails",
                    "blocking_reasons": blocks,
                    "audit_id": gate_decision.audit_id}

        # 7. Submit order to broker (no broker = dry run)
        if self.broker is None:
            return {"decision": "enter_dry_run",
                    "would_submit": {"pair": pair,
                                     "direction": gate_decision.direction,
                                     "units": ps.units,
                                     "entry": gate_decision.entry_price,
                                     "stop": gate_decision.stop_loss,
                                     "target": gate_decision.take_profit,
                                     "risk_amount": ps.risk_amount,
                                     "risk_pct_actual": (ps.risk_amount/self.account_balance*100)},
                    "audit_id": gate_decision.audit_id}

        # Actual broker submit (caller provides broker)
        try:
            order_result = self.broker.submit_market_order(
                pair=pair, units=ps.units if gate_decision.direction == "buy" else -ps.units,
                stop_loss=gate_decision.stop_loss,
                take_profit=gate_decision.take_profit)
        except Exception as e:
            self.nb.record_bug(affected_mind="execution", bug_type="broker_submit_failed",
                              severity="high", example_event_id=event.event_id,
                              impact=f"order_not_submitted:{e}")
            return {"decision": "broker_submit_failed", "error": str(e),
                    "audit_id": gate_decision.audit_id}

        # 8. Journal the trade
        trade = TradeAuditEntry(
            trade_id=order_result.get("trade_id", str(uuid.uuid4())),
            audit_id=gate_decision.audit_id,
            pair=pair, system_mode=self.cfg.broker_env,
            direction=gate_decision.direction,
            entry_time=now,
            entry_price=order_result.get("filled_price", gate_decision.entry_price),
            position_size=ps.units,
            stop_loss=gate_decision.stop_loss,
            take_profit=gate_decision.take_profit,
            expected_rr=gate_decision.risk_reward,
            spread_at_entry=spread_pips,
            slippage_estimate=slippage_pips,
            actual_slippage=order_result.get("slippage", 0.0),
            mind_outputs=mo,
        )
        self.nb.record_trade(trade)
        self.trades_today += 1

        return {"decision": "entered",
                "trade_id": trade.trade_id,
                "audit_id": gate_decision.audit_id,
                "units": ps.units,
                "risk_pct_actual": ps.risk_amount/self.account_balance*100,
                "fills": order_result}

    def update_after_close(self, *, trade_id: str, pnl: float, exit_price: float,
                            exit_reason: str = ""):
        """Update consecutive_losses + daily_loss_pct after a trade closes."""
        if pnl < 0:
            self.consecutive_losses += 1
            self.daily_loss_pct += abs(pnl) / self.account_balance * 100
        else:
            self.consecutive_losses = 0
        # SmartNoteBook integration: update existing trade record (extension)

    def stop(self):
        self.nb.stop()
