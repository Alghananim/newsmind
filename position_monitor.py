# -*- coding: utf-8 -*-
"""Position monitor — closes the loop between an open trade and the journal.

Without this module the system has a hole: GateMind opens a position,
SmartNoteBook stores the pre-mortem, but no one ever:

    1. moves the stop to break-even at +1R
    2. takes a partial exit at midway
    3. closes a stale trade on time-decay
    4. converts the closed position into a TradeRecord for SmartNoteBook

The PositionMonitor sits between Engine.step()'s open-side and Engine's
record_close(): every cycle, for every open position, it asks ChartMind
"is this trade healthy?" and applies the recommended action via
GateMind.monitor(). When the action closes the trade, it builds the
TradeRecord (carrying the cached open-time context) and feeds it to
SmartNoteBook.

What we cache at open time
--------------------------
Building a complete TradeRecord at close time requires information that
only existed when the trade *opened*: the brain grades, the gate's
combined confidence, the market regime + news state, the planned
sizing parameters, the pre-mortem prediction. We capture all of that
in `OpenContext` the moment the gate accepts a trade, keyed by the
broker_order_id, and consume it when the trade closes.

What the monitor itself tracks per cycle
----------------------------------------
`bars_held` — incremented every step we see the position still open.
`mfe_pips`  — the max favourable excursion (best price reached).
`mae_pips`  — the max adverse excursion (worst price reached).

These three are needed by the post_mortem module to decide whether a
winning trade was "lucky" (had a deep MAE before recovering) and
whether a losing trade was "well-managed" (small MFE before stopping).

Reasoning canon
---------------
    * Brett Steenbarger — *The Daily Trading Coach*, lesson 9: "the
      most important trade is the one you exit; entries get the glory,
      exits make the equity curve."
    * Mark Douglas — *Trading in the Zone*: define exit rules *before*
      the trade; do not improvise mid-trade.
    * Van Tharp — *Trade Your Way to Financial Freedom*: stop placement
      AND stop *movement* are the two halves of risk control.

This module talks duck-typed to ChartMind and GateMind, so swapping
either out (e.g. paper broker -> OANDA broker) does not require
changing the monitor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ----------------------------------------------------------------------
# Cached open-time context.
# ----------------------------------------------------------------------
@dataclass
class OpenContext:
    """Everything we will need to build a TradeRecord at close time.

    Captured the moment the gate accepts a trade. Updated per cycle
    by the monitor with `bars_held`, MFE, MAE.
    """
    pair: str
    plan: Any                          # ChartMind.TradePlan
    brain_grades: list                 # list of GateMind.BrainGrade

    # Decision context at open time.
    gate_combined_confidence: float
    market_regime: str
    news_state: str
    spread_pips_at_entry: float
    spread_percentile_rank: float

    # Execution context at open time.
    requested_price: float
    filled_price: float
    slippage_pips: float
    lot_size: float
    risk_amount_currency: float
    sizing_method: str
    broker_order_id: str

    # Pre-mortem (optional).
    pre_mortem_top_risk: str = ""
    pre_mortem_predicted_outcome: str = ""

    # Per-cycle accumulators (mutated by monitor).
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    bars_held: int = 0
    mfe_pips: float = 0.0
    mae_pips: float = 0.0
    last_price: float = 0.0
    last_seen_at: Optional[datetime] = None

    def update_excursions(self, current_price: float, direction: str,
                          entry_price: float, pair_pip: float = 0.0001) -> None:
        """Update MFE/MAE with the latest price.

        MFE = best price excursion in our favour from entry, in pips.
        MAE = worst price excursion against us from entry, in pips.
        Both stored as positive numbers.
        """
        if pair_pip <= 0:
            return
        if direction == "long":
            move_pips = (current_price - entry_price) / pair_pip
        else:
            move_pips = (entry_price - current_price) / pair_pip
        if move_pips > self.mfe_pips:
            self.mfe_pips = move_pips
        if move_pips < -self.mae_pips:
            self.mae_pips = -move_pips
        self.last_price = current_price


# ----------------------------------------------------------------------
# Result of one monitor cycle.
# ----------------------------------------------------------------------
@dataclass
class MonitorResult:
    """One cycle of the monitor over all open positions."""
    timestamp: datetime
    positions_seen: int
    actions_applied: list[dict] = field(default_factory=list)
    trades_recorded: list[str] = field(default_factory=list)   # trade_ids
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"open={self.positions_seen}"]
        if self.actions_applied:
            parts.append(f"actions={len(self.actions_applied)}")
        if self.trades_recorded:
            parts.append(f"closed={len(self.trades_recorded)}")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return " ".join(parts)


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def monitor_open_positions(*,
                           gatemind: Any,
                           chartmind_planner: Any,
                           open_contexts: dict[str, OpenContext],
                           current_price: float,
                           bar_reading: Any = None,
                           pair: str,
                           pair_pip: float = 0.0001,
                           on_close: Any = None,
                           now: Optional[datetime] = None,
                           ) -> MonitorResult:
    """Run one monitor cycle over every open position for `pair`.

    Parameters
    ----------
    gatemind : GateMind orchestrator instance
        Used both to read open positions (gatemind._portfolio.open_for_pair)
        and to apply actions (gatemind.monitor).
    chartmind_planner : module
        Imported `ChartMind.planner`; we call planner.monitor_position().
    open_contexts : dict[broker_order_id -> OpenContext]
        Cache populated by Engine when a trade opens. Mutated here:
        bars_held, MFE, MAE, last_seen_at.
    current_price : float
        Latest mid price for the pair (from the bar feed).
    bar_reading : Any
        Latest ChartReading (optional). Allows planner.monitor_position
        to consider new structure (e.g. setup invalidation).
    on_close : callable(broker_order_id, position, action, exit_price,
                       open_ctx, monitor_result_dict) -> None
        Engine passes a function that builds the TradeRecord and calls
        record_close(). The signature is verbose so the on_close
        function does not need to import OpenContext.

    Returns
    -------
    MonitorResult with counters useful for the live status line.
    """
    now = now or datetime.now(timezone.utc)
    result = MonitorResult(timestamp=now, positions_seen=0)

    portfolio = getattr(gatemind, "_portfolio", None)
    if portfolio is None:
        result.errors.append("gatemind._portfolio is None")
        return result

    try:
        positions = portfolio.open_for_pair(pair)
    except Exception as e:
        result.errors.append(f"open_for_pair: {e}")
        return result

    result.positions_seen = len(positions)

    for pos in positions:
        boid = getattr(pos, "broker_order_id", "") or ""
        ctx = open_contexts.get(boid)
        if ctx is None:
            # Position exists but we have no cached open context — most
            # likely a position carried over from before this process
            # started, or one opened outside the gate's normal flow.
            # Build a minimal context so MFE/MAE can still be tracked.
            ctx = _synthesize_minimal_context(pos)
            open_contexts[boid] = ctx

        # Update per-cycle accumulators
        ctx.bars_held += 1
        ctx.update_excursions(
            current_price=current_price,
            direction=pos.direction,
            entry_price=pos.entry_price,
            pair_pip=pair_pip,
        )
        ctx.last_seen_at = now

        # Ask ChartMind whether to hold / move stop / partial / exit
        plan = ctx.plan
        if plan is None:
            # No plan to monitor against; skip mechanical action,
            # but keep tracking MFE/MAE.
            continue

        try:
            health = chartmind_planner.monitor_position(
                plan=plan,
                current_price=current_price,
                bars_held=ctx.bars_held,
                reading=bar_reading,
                pair_pip=pair_pip,
            )
        except Exception as e:
            result.errors.append(f"monitor_position({boid[:8]}): {e}")
            continue

        action = getattr(health, "recommended_action", "hold")
        if action == "hold":
            continue

        # Apply the action via GateMind
        try:
            gm_result = gatemind.monitor(
                pair=pair,
                current_price=current_price,
                action=action,
                exit_price=current_price if action in ("partial_exit", "full_exit") else None,
                bars_held=ctx.bars_held,
                reason=", ".join(getattr(health, "reasons", [])[:3]),
            )
        except Exception as e:
            result.errors.append(f"gatemind.monitor({boid[:8]}, {action}): {e}")
            continue

        result.actions_applied.append({
            "broker_order_id": boid,
            "action": action,
            "current_price": current_price,
            "bars_held": ctx.bars_held,
            "health_score": getattr(health, "health_score", 0.0),
            "reasons": list(getattr(health, "reasons", [])[:3]),
        })

        # If this action closed the position, build a TradeRecord
        if action == "full_exit" and on_close is not None:
            try:
                trade_id = on_close(
                    broker_order_id=boid,
                    position=pos,
                    action=action,
                    exit_price=current_price,
                    open_ctx=ctx,
                    health=health,
                    now=now,
                )
                if trade_id:
                    result.trades_recorded.append(trade_id)
            except Exception as e:
                result.errors.append(f"on_close({boid[:8]}): {e}")
            # Pop from cache — position is gone.
            open_contexts.pop(boid, None)

    return result


# ----------------------------------------------------------------------
# Build a TradeRecord from the cached context + close info.
# ----------------------------------------------------------------------
def build_trade_record(*,
                       trade_record_cls: Any,         # SmartNoteBook.TradeRecord
                       trade_outcome_cls: Any,        # SmartNoteBook.TradeOutcome
                       brain_grade_record_cls: Any,   # SmartNoteBook.BrainGradeRecord
                       new_trade_id_fn: Any,          # SmartNoteBook.journal.new_trade_id
                       position: Any,
                       open_ctx: OpenContext,
                       exit_price: float,
                       exit_reason: str,
                       bars_held: int,
                       now: datetime,
                       pair_pip: float = 0.0001,
                       pip_value_per_lot: float = 10.0,
                       ) -> Any:
    """Translate (Position + OpenContext + close info) into a TradeRecord.

    Pure function — no side effects. The caller is responsible for
    handing the record to SmartNoteBook (via Engine.record_close).
    """
    direction = position.direction
    entry = position.entry_price
    if direction == "long":
        move_pips = (exit_price - entry) / pair_pip
    else:
        move_pips = (entry - exit_price) / pair_pip
    pnl_pips = move_pips
    pnl_currency = pnl_pips * position.lot * pip_value_per_lot
    risk_amount = max(1e-6, open_ctx.risk_amount_currency)
    r_multiple = pnl_currency / risk_amount

    # Translate brain grades from GateMind.BrainGrade to SmartNoteBook.BrainGradeRecord
    bg_records = []
    for g in open_ctx.brain_grades or []:
        bg_records.append(brain_grade_record_cls(
            brain=str(getattr(g, "name", "unknown")).lower(),
            grade=getattr(g, "grade", "F"),
            direction=getattr(g, "direction", "neutral"),
            confidence=float(getattr(g, "confidence", 0.0)),
            rationale=(getattr(g, "notes", "") or "")[:500],
            veto_flags=[getattr(g, "veto_reason", "")] if getattr(g, "veto", False) else [],
        ))

    plan = open_ctx.plan
    setup_type = getattr(plan, "setup_type", "unknown")
    rr_planned = float(getattr(plan, "rr_ratio", 0.0))
    time_budget = int(getattr(plan, "time_budget_bars", 0))
    plan_rationale = str(getattr(plan, "rationale", ""))[:500]
    plan_confidence = float(getattr(plan, "confidence", 0.0))

    outcome = trade_outcome_cls(
        exit_price=float(exit_price),
        exit_reason=str(exit_reason),
        closed_at=now,
        pnl_currency=pnl_currency,
        pnl_pips=pnl_pips,
        r_multiple=r_multiple,
        bars_held=int(bars_held),
        max_favourable_excursion_pips=float(open_ctx.mfe_pips),
        max_adverse_excursion_pips=float(open_ctx.mae_pips),
    )

    record = trade_record_cls(
        trade_id=new_trade_id_fn(),
        pair=open_ctx.pair,
        opened_at=open_ctx.opened_at,
        closed_at=now,
        brain_grades=bg_records,
        gate_combined_confidence=open_ctx.gate_combined_confidence,
        market_regime=open_ctx.market_regime,
        news_state=open_ctx.news_state,
        spread_pips_at_entry=open_ctx.spread_pips_at_entry,
        spread_percentile_rank=open_ctx.spread_percentile_rank,
        setup_type=setup_type,
        direction=direction,
        entry_price=entry,
        stop_price=float(getattr(plan, "stop_price", 0.0)),
        target_price=float(getattr(plan, "target_price", 0.0)),
        rr_planned=rr_planned,
        time_budget_bars=time_budget,
        plan_rationale=plan_rationale,
        plan_confidence=plan_confidence,
        filled_price=float(open_ctx.filled_price),
        requested_price=float(open_ctx.requested_price),
        slippage_pips=float(open_ctx.slippage_pips),
        lot_size=float(open_ctx.lot_size),
        risk_amount_currency=float(open_ctx.risk_amount_currency),
        sizing_method=str(open_ctx.sizing_method),
        broker_order_id=str(open_ctx.broker_order_id),
        outcome=outcome,
        pre_mortem_top_risk=str(open_ctx.pre_mortem_top_risk),
        pre_mortem_predicted_outcome=str(open_ctx.pre_mortem_predicted_outcome),
    )
    return record


# ----------------------------------------------------------------------
# Helper: synthesize a minimal context for orphaned positions.
# ----------------------------------------------------------------------
def _synthesize_minimal_context(pos: Any) -> OpenContext:
    """Build the most defensive OpenContext we can from just a Position.

    Used when the monitor sees a position whose open-time context was
    lost (e.g. process restart). MFE/MAE will start fresh from the
    moment we see it; bars_held will under-count. Better than nothing.
    """
    return OpenContext(
        pair=getattr(pos, "pair", "EUR/USD"),
        plan=None,                # mechanical monitor will skip
        brain_grades=[],
        gate_combined_confidence=0.0,
        market_regime="unknown",
        news_state="calm",
        spread_pips_at_entry=0.5,
        spread_percentile_rank=0.5,
        requested_price=getattr(pos, "entry_price", 0.0),
        filled_price=getattr(pos, "entry_price", 0.0),
        slippage_pips=0.0,
        lot_size=getattr(pos, "lot", 0.0),
        risk_amount_currency=getattr(pos, "risk_amount", 200.0),
        sizing_method="unknown",
        broker_order_id=getattr(pos, "broker_order_id", ""),
        opened_at=getattr(pos, "opened_at", datetime.now(timezone.utc)),
    )
