# -*- coding: utf-8 -*-
"""GateMind — the orchestrator that turns three brain grades into a trade.

Pipeline (the order matters; each step can short-circuit downward):

    ┌────────────────────────────────────────────────────────────────┐
    │  three BrainGrade objects                                      │
    │  + live ExecutionContext (equity, spread, news calendar)       │
    │  + an open Portfolio                                           │
    │  + a TradePlan from ChartMind (entry / stop / target / RR)     │
    └────────────────────────────────────────────────────────────────┘
                                │
                                ▼
                ┌──────────────────────────────┐
                │  1. decision.evaluate()      │  → GateDecision (pass / veto)
                │     - direction unanimity    │
                │     - grade floor (>= A)     │
                │     - 2-of-3 A+ rule         │
                │     - freshness < 90s        │
                │     - confidence floor       │
                └──────────────────────────────┘
                                │ pass_=True
                                ▼
                ┌──────────────────────────────┐
                │  2. kill_switches.evaluate() │  → KillSwitchVerdict
                │     - daily loss / DD        │
                │     - news blackout          │
                │     - spread / weekend       │
                │     - max_concurrent         │
                │     - margin floor           │
                └──────────────────────────────┘
                                │ halted=False
                                ▼
                ┌──────────────────────────────┐
                │  3. risk.size_trade()        │  → SizedTrade (lot, R)
                │     - fixed-fractional       │
                │     - or quarter-Kelly       │
                │     - capped by max_loss     │
                │     - capped by margin       │
                └──────────────────────────────┘
                                │
                                ▼
                ┌──────────────────────────────┐
                │  4. ExecutionRouter.submit() │  → ExecutionReceipt
                │     - retries, idempotent    │
                │     - records slippage       │
                └──────────────────────────────┘
                                │ accepted=True
                                ▼
                ┌──────────────────────────────┐
                │  5. portfolio.open_position()│
                │     ledger.fill()            │
                │     narrative.fill_message() │  → Telegram fill
                └──────────────────────────────┘

At every step we log to the Ledger; the Ledger is the source of
truth, the rest of the system is a side-effect of its records.

Position monitoring (the second cycle, run every bar)
------------------------------------------------------
Once a position is open, GateMind also runs a per-bar monitor:

    ┌─────────────────────────────────────────────────────────────┐
    │  ChartMind.monitor_position(plan, current_price, bars_held) │
    │    - move stop to BE at +1R                                 │
    │    - partial exit at midway                                 │
    │    - full exit on setup invalidation                        │
    │    - time-decay exit                                        │
    └─────────────────────────────────────────────────────────────┘
                                │
                                ▼
            execute the action via ExecutionRouter,
            update Portfolio, Ledger, Telegram.

The monitor lives in ChartMind (planner.py); GateMind merely calls
it and translates `recommended_action` into broker calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .decision import (
    BrainGrade, GateConfig, GateDecision, evaluate as gate_evaluate,
)
from .kill_switches import (
    KillSwitchConfig, KillSwitchInputs, KillSwitchVerdict,
    evaluate as kill_evaluate,
)
from .risk import RiskConfig, SizedTrade, size_trade, to_r_multiple
from .portfolio import Portfolio, Position
from .execution_router import (
    Broker, ExecutionRouter, OrderSpec, ExecutionReceipt, RouterConfig,
)
from .ledger import Ledger
from . import narrative


# --------------------------------------------------------------------
# Inputs to one decision cycle.
# --------------------------------------------------------------------
@dataclass
class GateMindContext:
    """Live snapshot the orchestrator needs for one decision."""
    pair: str
    grades: list[BrainGrade]              # exactly N (default 3)
    plan: "TradePlanLike"                 # any object with the duck-typed
                                          # fields used below
    current_price: float
    current_spread_pips: float
    spread_percentile_rank: float
    upcoming_news_events: list[dict] = field(default_factory=list)
    pair_pip: float = 0.0001
    pip_value_per_lot: float = 10.0


# Duck-typed plan (so we don't import ChartMind directly here).
class TradePlanLike:
    setup_type: str
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    rr_ratio: float
    time_budget_bars: int
    confidence: float
    rationale: str
    is_actionable: bool


# --------------------------------------------------------------------
# Output of one cycle.
# --------------------------------------------------------------------
@dataclass
class CycleResult:
    pair: str
    timestamp: datetime
    gate: GateDecision
    kill_switch: Optional[KillSwitchVerdict] = None
    sized: Optional[SizedTrade] = None
    receipt: Optional[ExecutionReceipt] = None
    position: Optional[Position] = None
    telegram_text: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        d = {
            "pair": self.pair,
            "timestamp": self.timestamp.isoformat()
                if isinstance(self.timestamp, datetime) else str(self.timestamp),
            "gate": self.gate.to_dict() if self.gate else None,
            "kill_switch": self.kill_switch.to_dict() if self.kill_switch else None,
            "sized": self.sized.__dict__ if self.sized else None,
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "position": self.position.to_dict() if self.position else None,
            "telegram_text": self.telegram_text,
            "error": self.error,
        }
        return d


# --------------------------------------------------------------------
# The orchestrator.
# --------------------------------------------------------------------
class GateMind:
    """The final-stage decision orchestrator.

    Construction:
        gm = GateMind(
            broker=OandaBroker(...),
            portfolio=Portfolio.load_or_init(...),
            ledger=Ledger(directory="state/ledger"),
            telegram_token="...", telegram_chat_id="...",
        )

    Then on every bar:
        result = gm.cycle(ctx)
        if result.position:
            ... a new trade was opened ...

    Configuration:
        GateMind owns the three sub-configs (gate, kill, risk). Edit
        once, never change mid-run.
    """

    def __init__(
        self,
        broker: Broker,
        portfolio: Portfolio,
        ledger: Ledger,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        gate_cfg: Optional[GateConfig] = None,
        kill_cfg: Optional[KillSwitchConfig] = None,
        risk_cfg: Optional[RiskConfig] = None,
        router_cfg: Optional[RouterConfig] = None,
    ):
        self._router = ExecutionRouter(broker, cfg=router_cfg)
        self._portfolio = portfolio
        self._ledger = ledger
        self._tg_token = telegram_token
        self._tg_chat = telegram_chat_id
        self._gate_cfg = gate_cfg or GateConfig()
        self._kill_cfg = kill_cfg or KillSwitchConfig()
        self._risk_cfg = risk_cfg or RiskConfig()

    # ----- public per-bar entry point ------------------------------
    def cycle(self, ctx: GateMindContext) -> CycleResult:
        """Run one full decision cycle. Returns a CycleResult."""
        now = datetime.now(timezone.utc)
        cr = CycleResult(pair=ctx.pair, timestamp=now,
                         gate=GateDecision(False, "neutral", 0.0, [], [], []))

        # --- 1. Gate ----------------------------------------------
        gate = gate_evaluate(ctx.grades, self._gate_cfg, now_utc=now)
        cr.gate = gate
        # Always log the gate evaluation, pass or fail.
        self._ledger.write(
            "decision" if gate.pass_ else "veto",
            ctx.pair,
            gate.to_dict(),
            ts=now,
        )
        if not gate.pass_:
            cr.telegram_text = narrative.veto_message(
                pair=ctx.pair, price=ctx.current_price,
                reasons=gate.gates_failed, ts=now,
            )
            self._notify(cr.telegram_text)
            return cr

        # --- 2. Kill switches -------------------------------------
        snap = self._portfolio.snapshot()
        # Estimate margin requirement for the proposed trade. We do a
        # cheap pre-check using the planner's expected risk (approx).
        # Actual margin cap is enforced inside size_trade().
        estimated_margin = snap.equity * 0.10  # conservative pre-est
        kill_in = KillSwitchInputs(
            now_utc=now,
            equity=snap.equity,
            today_starting_equity=snap.today_starting_equity,
            today_realised_pnl=snap.today_realised,
            today_unrealised_pnl=snap.today_open_unrealised,
            peak_equity=snap.peak_equity,
            open_positions_pair=sum(
                1 for p in snap.open_positions if p.pair == ctx.pair
            ),
            open_positions_total=len(snap.open_positions),
            current_spread_pips=ctx.current_spread_pips,
            spread_percentile_rank=ctx.spread_percentile_rank,
            proposed_margin=estimated_margin,
            cash_available=snap.cash,
            upcoming_news_events=list(ctx.upcoming_news_events),
        )
        kill = kill_evaluate(kill_in, self._kill_cfg)
        cr.kill_switch = kill
        if kill.halted:
            self._ledger.write("veto", ctx.pair, {
                "stage": "kill_switch",
                "verdict": kill.to_dict(),
                "gate": gate.to_dict(),
            }, ts=now)
            cr.telegram_text = narrative.kill_switch_message(
                fired_switches=kill.fired_switches,
                reasons=kill.reasons,
                equity=snap.equity, drawdown=snap.drawdown,
                daily_pnl_pct=snap.daily_pnl_pct, ts=now,
            )
            self._notify(cr.telegram_text)
            return cr

        # --- 3. Position sizing -----------------------------------
        plan = ctx.plan
        sized = size_trade(
            equity=snap.equity,
            entry_price=plan.entry_price,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            win_probability=gate.combined_confidence,
            cfg=self._risk_cfg,
        )
        cr.sized = sized
        self._ledger.write("decision", ctx.pair, {
            "stage": "sized",
            "sized": sized.__dict__,
            "plan": _plan_to_dict(plan),
            "gate_confidence": gate.combined_confidence,
        }, ts=now)

        # --- 4. Submit to broker ----------------------------------
        order = OrderSpec(
            pair=ctx.pair,
            direction=plan.direction,
            lot=sized.lot,
            order_type="market" if abs(plan.entry_price - ctx.current_price)
                                   <= 1.5 * ctx.pair_pip else "limit",
            entry_price=plan.entry_price,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
        )
        self._ledger.write("submit", ctx.pair, {
            "order": order.__dict__, "sized": sized.__dict__,
        }, ts=now)
        receipt = self._router.submit(order)
        cr.receipt = receipt
        if not receipt.accepted:
            self._ledger.write("error", ctx.pair, {
                "stage": "broker_submit",
                "receipt": receipt.to_dict(),
            }, ts=now)
            cr.error = receipt.error_message or "submit failed"
            cr.telegram_text = narrative.error_message(
                pair=ctx.pair,
                error_code=receipt.error_code or "submit_failed",
                error_text=receipt.error_message or "no message", ts=now,
            )
            self._notify(cr.telegram_text)
            return cr

        # --- 5. Persist position + notify -------------------------
        pos = Position(
            pair=ctx.pair,
            direction=plan.direction,
            lot=sized.lot,
            entry_price=receipt.filled_price or plan.entry_price,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            opened_at=receipt.filled_at or now,
            risk_amount=sized.risk_amount,
            broker_order_id=receipt.broker_order_id,
            notes=plan.rationale[:500] if hasattr(plan, "rationale") else "",
        )
        self._portfolio.open_position(pos)
        cr.position = pos
        self._ledger.write("fill", ctx.pair, {
            "position": pos.to_dict(),
            "receipt": receipt.to_dict(),
        }, ts=now)
        cr.telegram_text = narrative.fill_message(
            pair=ctx.pair,
            direction=plan.direction,
            lot=sized.lot,
            filled_price=receipt.filled_price or plan.entry_price,
            requested_price=plan.entry_price,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            risk_amount=sized.risk_amount,
            slippage_pips=receipt.actual_slippage_pips,
            broker_order_id=receipt.broker_order_id,
            ts=now,
        )
        self._notify(cr.telegram_text)
        return cr

    # ----- monitor an open position --------------------------------
    def monitor(self, pair: str, current_price: float,
                action: str, exit_price: Optional[float] = None,
                bars_held: int = 0, reason: str = "") -> Optional[CycleResult]:
        """Apply a recommendation from ChartMind.monitor_position().

        action: "hold" | "move_stop_to_be" | "trail" | "partial_exit" | "full_exit"
        """
        now = datetime.now(timezone.utc)
        positions = self._portfolio.open_for_pair(pair)
        if not positions:
            return None
        pos = positions[0]   # single-position rule

        if action in ("hold",):
            return None

        if action in ("move_stop_to_be",):
            # In production this would trigger a broker stop-modify.
            # We ledger it and update the local stop price.
            new_stop = pos.entry_price
            self._ledger.update(pair, {
                "action": "move_stop_to_be",
                "old_stop": pos.stop_price,
                "new_stop": new_stop,
                "current_price": current_price,
            })
            pos.stop_price = new_stop
            return None

        if action in ("partial_exit", "full_exit"):
            exit_p = exit_price if exit_price is not None else current_price
            pnl = self._portfolio.close_position(pos, exit_p, now_utc=now)
            pips = (
                (exit_p - pos.entry_price) / 0.0001
                if pos.direction == "long"
                else (pos.entry_price - exit_p) / 0.0001
            )
            r = to_r_multiple(pnl, pos.risk_amount)
            self._ledger.close(pair, {
                "position": pos.to_dict(),
                "exit_price": exit_p,
                "pnl": pnl,
                "pips": pips,
                "r_multiple": r,
                "bars_held": bars_held,
                "reason": reason,
            })
            text = narrative.close_message(
                pair=pair, direction=pos.direction,
                entry_price=pos.entry_price, exit_price=exit_p,
                pnl_currency=pnl, pnl_pips=pips, r_multiple=r,
                bars_held=bars_held, reason=reason, ts=now,
            )
            self._notify(text)
            cr = CycleResult(pair=pair, timestamp=now,
                             gate=GateDecision(True, pos.direction, 1.0, [], [], []),
                             telegram_text=text)
            return cr

        return None

    # ----- internal -----------------------------------------------
    def _notify(self, text: str) -> None:
        if not text:
            return
        if not self._tg_token or not self._tg_chat:
            return  # silent in non-telegram environments
        ok, err = narrative.send_telegram(
            self._tg_token, self._tg_chat, text,
        )
        if not ok:
            self._ledger.error("system", {
                "stage": "telegram_send",
                "error": err,
                "preview": text[:200],
            })


# --------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------
def _plan_to_dict(plan) -> dict:
    """Best-effort plan-to-dict for the ledger. Tolerates duck typing."""
    fields = (
        "setup_type", "direction", "entry_price", "stop_price",
        "target_price", "rr_ratio", "time_budget_bars", "confidence",
        "rationale", "is_actionable", "reason_if_not",
    )
    return {f: getattr(plan, f, None) for f in fields}
