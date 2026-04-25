# -*- coding: utf-8 -*-
"""RiskManager — circuit breakers that match a real prudent operator.

Three layered defences
----------------------
1. **Per-trade risk cap** — every trade risks at most
   `risk_per_trade_pct` of *current* equity. This is the standard
   fixed-fractional sizing rule (Tharp, *Trade Your Way to Financial
   Freedom*, ch.13). With 0.5% per trade, a 20-loss streak draws
   the account down ~10% — survivable.

2. **Daily loss cap** — if the day's realised P&L drops below
   `-daily_loss_cap_pct` of starting-of-day equity, no further
   entries are allowed for the day. Open positions continue to be
   managed (stops still work). This is Steenbarger's "bench yourself"
   rule (*Trading Psychology 2.0*, ch.5).

3. **Max drawdown kill switch** — if equity falls
   `max_drawdown_cap_pct` below the running peak, the entire backtest
   halts. Reproduces what a real risk officer would impose. Mark
   Douglas (*Trading in the Zone*): "the trader who has never been
   stopped from trading by an outside rule has never seriously risked
   their own capital."

Plus a streak-based pause:

4. **Consecutive losses** — after `max_consecutive_losses` losses,
   pause new entries until a winning trade arrives or the day rolls
   over. Catches revenge-trading at the gate.

The RiskManager is queried by the runner BEFORE every entry; verdict
is one of {ALLOW, BLOCK_DAY, BLOCK_PERMANENT, BLOCK_STREAK}. Open
positions are never force-closed by the RiskManager itself — that
authority belongs to the position monitor and the kill_switches in
GateMind.

Reasoning canon
---------------
    * Van Tharp — *Trade Your Way to Financial Freedom*: position
      sizing is the single biggest determinant of long-term outcome.
    * Brett Steenbarger — *Trading Psychology 2.0*: the trader who
      cannot stop is the trader who blows up.
    * Robert Carver — *Systematic Trading*: limits should be set
      *before* you need them, not in response to a drawdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional


# ----------------------------------------------------------------------
# State and verdict types.
# ----------------------------------------------------------------------
@dataclass
class RiskState:
    """Live counters the RiskManager updates as the backtest progresses.

    The runner mutates these between bars; the manager reads them to
    decide each entry.
    """
    starting_equity: float
    current_equity: float
    peak_equity: float
    today: date
    today_starting_equity: float
    today_realised_pnl: float
    today_trade_count: int = 0
    consecutive_losses: int = 0
    halted_permanent: bool = False           # max-DD kill switch fired
    halt_reason: str = ""

    @classmethod
    def initial(cls, starting_equity: float,
                today: date) -> "RiskState":
        return cls(
            starting_equity=starting_equity,
            current_equity=starting_equity,
            peak_equity=starting_equity,
            today=today,
            today_starting_equity=starting_equity,
            today_realised_pnl=0.0,
        )

    def drawdown_pct(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity * 100.0

    def daily_pnl_pct(self) -> float:
        if self.today_starting_equity <= 0:
            return 0.0
        return self.today_realised_pnl / self.today_starting_equity * 100.0


@dataclass
class RiskVerdict:
    """Outcome of one risk check."""
    allow: bool                              # True = entry permitted
    reason: str                              # "OK" | short rejection
    sized_lot: float = 0.0                   # only valid when allow=True
    sized_risk_currency: float = 0.0         # only valid when allow=True
    state: Optional[RiskState] = None        # snapshot at decision time


# ----------------------------------------------------------------------
# The manager.
# ----------------------------------------------------------------------
@dataclass
class RiskManager:
    """All risk policies in one place. Construct from BacktestConfig.

    Pure aside from the explicit `update_*` methods which the runner
    calls when a trade closes / a new day begins.
    """
    risk_per_trade_pct: float = 0.5
    daily_loss_cap_pct: float = 3.0
    max_drawdown_cap_pct: float = 15.0
    max_consecutive_losses: int = 3
    pip_value_per_lot: float = 10.0
    pair_pip: float = 0.0001
    units_per_lot: int = 100_000

    # ==================================================================
    # Pre-trade check.
    # ==================================================================
    def evaluate_entry(self,
                       *,
                       state: RiskState,
                       direction: str,
                       entry_price: float,
                       stop_price: float,
                       ) -> RiskVerdict:
        """Decide whether a proposed trade may proceed and at what size.

        Side effect: returns a RiskVerdict carrying the suggested lot
        size if allowed. Does NOT mutate state.
        """
        # ---- 1. Permanent halt ------------------------------------
        if state.halted_permanent:
            return RiskVerdict(
                allow=False,
                reason=f"halted_permanent: {state.halt_reason}",
                state=state,
            )

        # ---- 2. Max drawdown kill switch --------------------------
        dd = state.drawdown_pct()
        if dd >= self.max_drawdown_cap_pct:
            return RiskVerdict(
                allow=False,
                reason=f"max_drawdown_breached: {dd:.2f}%",
                state=state,
            )

        # ---- 3. Daily loss cap ------------------------------------
        daily = state.daily_pnl_pct()
        if daily <= -self.daily_loss_cap_pct:
            return RiskVerdict(
                allow=False,
                reason=f"daily_loss_cap_breached: {daily:.2f}%",
                state=state,
            )

        # ---- 4. Consecutive-loss streak pause ---------------------
        if state.consecutive_losses >= self.max_consecutive_losses:
            return RiskVerdict(
                allow=False,
                reason=f"consecutive_losses: {state.consecutive_losses}",
                state=state,
            )

        # ---- 5. Sane stop distance --------------------------------
        if direction == "long":
            risk_distance = entry_price - stop_price
        else:
            risk_distance = stop_price - entry_price
        if risk_distance <= 0:
            return RiskVerdict(
                allow=False,
                reason=f"invalid_stop_distance: {risk_distance:.5f}",
                state=state,
            )

        # ---- 6. Compute lot size ----------------------------------
        risk_currency = state.current_equity * (self.risk_per_trade_pct / 100.0)
        risk_pips = risk_distance / self.pair_pip
        if risk_pips <= 0:
            return RiskVerdict(
                allow=False,
                reason=f"invalid_risk_pips: {risk_pips:.2f}",
                state=state,
            )
        lot = risk_currency / (risk_pips * self.pip_value_per_lot)
        # Round to 0.01 lot (typical retail broker minimum granularity).
        lot = round(lot, 2)
        if lot <= 0:
            return RiskVerdict(
                allow=False,
                reason=f"computed_lot_too_small: {lot}",
                state=state,
            )

        # ---- 7. Sanity ceiling ------------------------------------
        # Reject absurd sizes (>50 lots on $10k = >50,000:1 leverage).
        if lot > state.current_equity / 200.0:
            return RiskVerdict(
                allow=False,
                reason=f"lot_size_unsane: {lot} on equity {state.current_equity}",
                state=state,
            )

        return RiskVerdict(
            allow=True, reason="OK",
            sized_lot=lot,
            sized_risk_currency=risk_currency,
            state=state,
        )

    # ==================================================================
    # State updates (called by the runner).
    # ==================================================================
    def on_trade_closed(self,
                        *,
                        state: RiskState,
                        pnl_currency: float,
                        ) -> RiskState:
        """Update equity, peak, drawdown counters, streak after a trade."""
        new_equity = state.current_equity + pnl_currency
        new_peak = max(state.peak_equity, new_equity)
        new_today_realised = state.today_realised_pnl + pnl_currency
        new_streak = (
            state.consecutive_losses + 1
            if pnl_currency < 0 else 0
        )

        next_state = RiskState(
            starting_equity=state.starting_equity,
            current_equity=new_equity,
            peak_equity=new_peak,
            today=state.today,
            today_starting_equity=state.today_starting_equity,
            today_realised_pnl=new_today_realised,
            today_trade_count=state.today_trade_count + 1,
            consecutive_losses=new_streak,
            halted_permanent=state.halted_permanent,
            halt_reason=state.halt_reason,
        )

        # Trip the kill switch if max DD is breached
        dd = next_state.drawdown_pct()
        if dd >= self.max_drawdown_cap_pct and not next_state.halted_permanent:
            next_state.halted_permanent = True
            next_state.halt_reason = f"max_dd_breached: {dd:.2f}%"
        return next_state

    def on_new_day(self,
                   *,
                   state: RiskState,
                   new_today: date,
                   ) -> RiskState:
        """Reset daily counters when the calendar day rolls forward.

        consecutive_losses is also reset — a fresh day, fresh start
        (Steenbarger's "today is not yesterday" rule).
        """
        return RiskState(
            starting_equity=state.starting_equity,
            current_equity=state.current_equity,
            peak_equity=state.peak_equity,
            today=new_today,
            today_starting_equity=state.current_equity,
            today_realised_pnl=0.0,
            today_trade_count=0,
            consecutive_losses=0,
            halted_permanent=state.halted_permanent,
            halt_reason=state.halt_reason,
        )
