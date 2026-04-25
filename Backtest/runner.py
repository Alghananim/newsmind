# -*- coding: utf-8 -*-
"""BacktestRunner — the deterministic event loop for the EUR/USD backtest.

What this module does
---------------------
Walks through bars in chronological order, one at a time, feeding each
to ChartMind, deciding entries through the RiskManager, and resolving
exits using the CostModel. The whole loop is pure: same inputs ->
same outputs (after fixing the seed). No live time, no broker calls.

Architecture
------------
    BacktestRunner                          # this module
        ├── BacktestSession   (when to trade)
        ├── HistoricalCalendar (when not to trade due to news)
        ├── RiskManager        (sizing + circuit breakers)
        ├── CostModel          (realistic fills)
        ├── ChartMind          (signal generation)
        └── SmartNoteBook      (journaling closed trades)

Lookahead defence (the most important rule)
-------------------------------------------
ChartMind sees only completed bars up to and including bar[t]. Entry
signals from bar[t] fill at bar[t+1] open — never at bar[t] close.
Stop/target checks for an open position only consider bar high/low,
which are knowable at bar[t] close.

This is enforced structurally:
    * `_step_signal_phase` runs ChartMind on bar[t], records pending entry
    * `_step_fill_phase`  runs on bar[t+1], opens or rejects the entry
    * `_step_exit_phase`  runs on bar[t+1], checks SL/TP against bar high/low

A signal generated on bar[t] CANNOT see bar[t+1]'s data. By
construction.

Sample-size note
----------------
Lopez de Prado (*AFML* ch.13): a backtest result needs N >= 30 trades
in EACH of the cohort bins (setup × hour × regime) for the cohort
metrics to be reliable. With ~1000 trades over 2 years and ~6 cohort
slices, we average 167 trades/bin — comfortable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from .calendar import HistoricalCalendar
from .config import BacktestConfig
from .costs import CostModel, FillResult
from .data import BacktestBar
from .risk import RiskManager, RiskState, RiskVerdict
from .session import BacktestSession
from .variants import VariantFilter, get_variant
from .regime import RegimeDetector


# ----------------------------------------------------------------------
# In-flight pending entry signal (one bar's lookahead-safe holding zone).
# ----------------------------------------------------------------------
@dataclass
class _PendingSignal:
    """ChartMind decided to enter on bar[t]; we fill on bar[t+1] open."""
    direction: str
    setup_type: str
    entry_price: float
    stop_price: float
    target_price: float
    rr_ratio: float
    plan_confidence: float
    plan_rationale: str
    time_budget_bars: int
    sized_lot: float
    sized_risk_currency: float
    signal_bar_time: datetime


# ----------------------------------------------------------------------
# Live in-position tracking.
# ----------------------------------------------------------------------
@dataclass
class _OpenPosition:
    """A position that has been filled and is being actively managed."""
    direction: str
    setup_type: str
    plan_rationale: str
    plan_confidence: float
    entry_time: datetime
    entry_price: float
    stop_price: float
    target_price: float
    rr_planned: float
    lot_size: float
    risk_amount_currency: float
    requested_price: float
    bars_held: int = 0
    mfe_pips: float = 0.0
    mae_pips: float = 0.0
    initial_risk_pips: float = 0.0   # |entry-stop| in pips at open
    trailed_to_be: bool = False      # stop already moved to break-even
    spread_pips_at_entry: float = 0.5
    entry_commission: float = 0.0


# ----------------------------------------------------------------------
# Output dataclass.
# ----------------------------------------------------------------------
@dataclass
class BacktestResult:
    """Output of one BacktestRunner.run()."""
    config: BacktestConfig
    started_at: datetime
    ended_at: datetime
    bars_seen: int
    signals_generated: int
    entries_filled: int
    entries_rejected_by_session: int
    entries_rejected_by_calendar: int
    entries_rejected_by_risk: int
    entries_rejected_by_unfilled_limit: int
    closed_trades: int

    final_equity: float
    starting_equity: float
    peak_equity: float
    max_drawdown_pct: float
    halted_early: bool
    halt_reason: str

    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    daily_pnl: list[tuple[date, float]] = field(default_factory=list)

    # Reference to SmartNoteBook so the analyzer can read trades.
    snb: Any = None

    def summary(self) -> str:
        gain = (self.final_equity - self.starting_equity) / self.starting_equity * 100
        return (
            f"Backtest: {self.bars_seen} bars, "
            f"{self.entries_filled} entries, "
            f"{self.closed_trades} closes, "
            f"eq {self.starting_equity:.0f} -> {self.final_equity:.0f} "
            f"({gain:+.1f}%), maxDD {self.max_drawdown_pct:.1f}%"
        )


# ----------------------------------------------------------------------
# The runner.
# ----------------------------------------------------------------------
class BacktestRunner:
    """Deterministic backtest loop.

    Construct with the components, then call run(bars). Returns a
    BacktestResult; the SmartNoteBook journal is also populated for
    downstream analysis.
    """

    def __init__(self,
                 *,
                 config: BacktestConfig,
                 chartmind: Any,             # ChartMind instance
                 snb: Any = None,            # SmartNoteBook instance (optional)
                 session: Optional[BacktestSession] = None,
                 calendar: Optional[HistoricalCalendar] = None,
                 cost_model: Optional[CostModel] = None,
                 risk_manager: Optional[RiskManager] = None,
                 variant_filter: Optional[VariantFilter] = None):
        self.config = config
        self.cm = chartmind
        self.snb = snb
        self.variant = variant_filter or VariantFilter()
        self.regime_detector = RegimeDetector(pair_pip=config.pair_pip)

        self.session = session or BacktestSession(
            tz_name=config.session_tz,
            windows=config.session_windows,
        )
        self.calendar = calendar or HistoricalCalendar(
            pre_minutes_t1=config.news_blackout_minutes_pre,
            post_minutes_t1=config.news_blackout_minutes_post,
        )
        self.costs = cost_model or CostModel(
            pair_pip=config.pair_pip,
            pip_value_per_lot=config.pip_value_per_lot,
            units_per_lot=config.units_per_lot,
            entry_slippage_pips=config.entry_slippage_pips,
            stop_slippage_pips=config.stop_slippage_pips,
            target_slippage_pips=config.target_slippage_pips,
            fallback_spread_pips=config.fallback_spread_pips,
            commission_per_lot_per_side=config.commission_per_lot_per_side,
        )
        # Variant may override risk_per_trade_pct.
        _eff_risk_pct = (self.variant.risk_pct_override
                         if self.variant.risk_pct_override is not None
                         else config.risk_per_trade_pct)
        self.risk = risk_manager or RiskManager(
            risk_per_trade_pct=_eff_risk_pct,
            daily_loss_cap_pct=config.daily_loss_cap_pct,
            max_drawdown_cap_pct=config.max_drawdown_cap_pct,
            max_consecutive_losses=config.max_consecutive_losses,
            pip_value_per_lot=config.pip_value_per_lot,
            pair_pip=config.pair_pip,
            units_per_lot=config.units_per_lot,
        )

        # Internal state
        self._pending: Optional[_PendingSignal] = None
        self._halt_resume_date = None  # set when DD halt fires in pause-mode
        self._last_regime: Optional[str] = None
        self._last_adx: float = 0.0
        self._open: Optional[_OpenPosition] = None
        self._risk_state: Optional[RiskState] = None

        # Rolling bar history that we feed to ChartMind (which needs
        # a pandas DataFrame, not a single bar). Keep the last
        # `_history_window` bars in memory so analyze() always has
        # enough context to identify swings, levels, ICT structures.
        self._history_window = 250          # ~62 hours at M15
        self._history: list[BacktestBar] = []

        # Counters (reset per run)
        self._reset_counters()

    # ==================================================================
    # Public API.
    # ==================================================================
    def run(self, bars: Iterable[BacktestBar]) -> BacktestResult:
        """Run the backtest over `bars` (must be chronologically sorted).

        Returns a `BacktestResult`. The SmartNoteBook journal (if
        provided) is populated with one TradeRecord per closed trade.
        """
        bars = list(bars)
        if not bars:
            return self._empty_result()

        self._reset_counters()
        self._pending = None
        self._open = None
        self._risk_state = RiskState.initial(
            starting_equity=self.config.starting_equity,
            today=bars[0].time.date(),
        )

        equity_curve: list[tuple[datetime, float]] = []
        daily_pnl: list[tuple[date, float]] = []

        for i, bar in enumerate(bars):
            self.bars_seen += 1

            # Push to rolling history (cap at window size)
            self._history.append(bar)
            if len(self._history) > self._history_window:
                self._history = self._history[-self._history_window:]

            # Roll day if needed (resets daily counters).
            if bar.time.date() != self._risk_state.today:
                # Record the day we just left
                daily_pnl.append((self._risk_state.today,
                                  self._risk_state.today_realised_pnl))
                self._risk_state = self.risk.on_new_day(
                    state=self._risk_state, new_today=bar.time.date(),
                )

            # Halt handling: kill / pause / off based on variant.
            if self._risk_state.halted_permanent:
                if self.variant.halt_pause_days > 0:
                    # Pause-and-resume mode: wait N days, then reset the
                    # risk state with starting_equity = current_equity
                    # and resume trading. Counts each pause as a halt event.
                    if self._halt_resume_date is None:
                        self._halt_resume_date = bar.time.date() + timedelta(
                            days=self.variant.halt_pause_days)
                        self.halt_count += 1
                    if bar.time.date() < self._halt_resume_date:
                        continue   # skip this bar — still on pause
                    # Resume: rebuild risk state from current equity.
                    cur_eq = self._risk_state.current_equity
                    self._risk_state = RiskState.initial(
                        starting_equity=cur_eq,
                        today=bar.time.date(),
                    )
                    self._halt_resume_date = None
                elif not self.variant.disable_max_dd_halt:
                    break
                # else: disable_max_dd_halt is True; just continue

            # ----- Phase A: fill any pending signal from previous bar
            self._fill_pending(bar)

            # ----- Phase B: manage open position against this bar
            if self._open is not None:
                self._manage_open(bar)

            # ----- Phase C: emit a new signal from this bar (if no open)
            if self._open is None:
                self._maybe_emit_signal(bar)

            # ----- Equity curve sample
            equity_curve.append((bar.time, self._risk_state.current_equity))

        # Final close-of-day PnL bookkeeping
        daily_pnl.append((self._risk_state.today,
                          self._risk_state.today_realised_pnl))

        return BacktestResult(
            config=self.config,
            started_at=bars[0].time,
            ended_at=bars[-1].time,
            bars_seen=self.bars_seen,
            signals_generated=self.signals_generated,
            entries_filled=self.entries_filled,
            entries_rejected_by_session=self.rej_session,
            entries_rejected_by_calendar=self.rej_calendar,
            entries_rejected_by_risk=self.rej_risk,
            entries_rejected_by_unfilled_limit=self.rej_unfilled_limit,
            closed_trades=self.closed_trades,
            final_equity=self._risk_state.current_equity,
            starting_equity=self.config.starting_equity,
            peak_equity=self._risk_state.peak_equity,
            max_drawdown_pct=self._risk_state.drawdown_pct(),
            halted_early=self._risk_state.halted_permanent,
            halt_reason=self._risk_state.halt_reason,
            equity_curve=equity_curve,
            daily_pnl=daily_pnl,
            snb=self.snb,
        )

    # ==================================================================
    # Phases of one bar.
    # ==================================================================
    def _fill_pending(self, bar: BacktestBar) -> None:
        """Phase A: if a pending entry was queued on the previous bar,
        try to fill it on THIS bar's open. Either opens a position or
        records a rejection.
        """
        if self._pending is None:
            return
        sig = self._pending
        self._pending = None

        # MARKET fill at bar open with adverse slippage
        fill: FillResult = self.costs.simulate_entry(
            direction=sig.direction,
            order_type="market",
            requested_price=sig.entry_price,
            next_bar_open=bar.open,
            next_bar_bid=bar.bid_open,
            next_bar_ask=bar.ask_open,
            lot=sig.sized_lot,
            fill_time=bar.time,
        )
        if not fill.filled:
            self.rej_unfilled_limit += 1
            return

        self._open = _OpenPosition(
            direction=sig.direction,
            setup_type=sig.setup_type,
            plan_rationale=sig.plan_rationale,
            plan_confidence=sig.plan_confidence,
            entry_time=fill.fill_time,
            entry_price=fill.fill_price,
            stop_price=sig.stop_price,
            target_price=sig.target_price,
            rr_planned=sig.rr_ratio,
            lot_size=sig.sized_lot,
            risk_amount_currency=sig.sized_risk_currency,
            requested_price=sig.entry_price,
            bars_held=0,
            initial_risk_pips=abs(fill.fill_price - sig.stop_price) / self.config.pair_pip,
            spread_pips_at_entry=fill.spread_paid_pips,
            entry_commission=fill.commission_currency,
        )
        self.entries_filled += 1

    def _manage_open(self, bar: BacktestBar) -> None:
        """Phase B: see if THIS bar's range hit our stop or target.

        Order matters:
            * If a long bar took out BOTH the stop and the target
              (a "wide bar"), we conservatively assume the stop hit
              first — the worst-case for the trader. (Ask-side bias.)
            * Time-decay exit (bars_held > time_budget) only fires
              if neither stop nor target hit on this bar.
        """
        if self._open is None:
            return
        pos = self._open
        pos.bars_held += 1

        # Update MFE / MAE on this bar's range.
        if pos.direction == "long":
            mfe_candidate = (bar.high - pos.entry_price) / self.config.pair_pip
            mae_candidate = (pos.entry_price - bar.low) / self.config.pair_pip
        else:
            mfe_candidate = (pos.entry_price - bar.low) / self.config.pair_pip
            mae_candidate = (bar.high - pos.entry_price) / self.config.pair_pip
        if mfe_candidate > pos.mfe_pips:
            pos.mfe_pips = mfe_candidate
        if mae_candidate > pos.mae_pips:
            pos.mae_pips = mae_candidate

        # Trailing stop logic (variant-driven).
        # Once MFE reaches `trail_stop_after_r * initial_risk_pips`,
        # move stop to break-even. After every additional 0.5R of
        # MFE, ratchet the stop another 0.5R closer.
        if (self.variant.trail_stop_after_r > 0
                and pos.initial_risk_pips > 0):
            mfe_r = pos.mfe_pips / pos.initial_risk_pips
            trigger = self.variant.trail_stop_after_r
            if mfe_r >= trigger and not pos.trailed_to_be:
                # Move stop to break-even (entry price)
                pos.stop_price = pos.entry_price
                pos.trailed_to_be = True
            if pos.trailed_to_be and mfe_r > trigger + 0.5:
                # Ratchet: trail stop 0.5R behind current MFE peak.
                trail_offset_pips = (mfe_r - 0.5) * pos.initial_risk_pips
                if pos.direction == "long":
                    new_stop = pos.entry_price + trail_offset_pips * self.config.pair_pip
                    if new_stop > pos.stop_price:
                        pos.stop_price = new_stop
                else:
                    new_stop = pos.entry_price - trail_offset_pips * self.config.pair_pip
                    if new_stop < pos.stop_price:
                        pos.stop_price = new_stop

        # Stop check (worst-case first)
        stop_fill = self.costs.simulate_stop_hit(
            direction=pos.direction,
            stop_price=pos.stop_price,
            bar_high=bar.high, bar_low=bar.low,
            fill_time=bar.time, lot=pos.lot_size,
        )
        if stop_fill.filled:
            self._close(pos, fill=stop_fill, exit_reason="stop", bar=bar)
            return

        # Target check
        target_fill = self.costs.simulate_target_hit(
            direction=pos.direction,
            target_price=pos.target_price,
            bar_high=bar.high, bar_low=bar.low,
            fill_time=bar.time, lot=pos.lot_size,
        )
        if target_fill.filled:
            self._close(pos, fill=target_fill, exit_reason="target", bar=bar)
            return

        # Time-decay exit
        budget = max(1, pos.bars_held)   # avoid div-by-0
        plan_budget = self._open_time_budget()
        if pos.bars_held >= plan_budget:
            exit_fill = self.costs.simulate_market_exit(
                direction=pos.direction,
                current_bid=bar.bid_close,
                current_ask=bar.ask_close,
                current_mid=bar.close,
                fill_time=bar.time, lot=pos.lot_size,
            )
            self._close(pos, fill=exit_fill, exit_reason="time_decay", bar=bar)
            return

    def _maybe_emit_signal(self, bar: BacktestBar) -> None:
        """Phase C: ask ChartMind for a plan over this bar; if actionable
        and all gates pass, queue an entry to fill on the NEXT bar.
        """
        # Session filter
        if not self.session.is_trading_now(bar.time):
            return

        # Calendar (news) filter
        is_blackout, _ev = self.calendar.is_blackout(bar.time, tiers=("T1",))
        if is_blackout:
            self.rej_calendar += 1
            return

        # ChartMind expects a pandas DataFrame of historical OHLC data.
        # Need at least ~50 bars for swings; we accumulate up to
        # _history_window. Skip until we have enough.
        if len(self._history) < 50:
            return

        # ATR surge filter (variant-driven). Skip new entries when
        # short-term volatility blows out vs longer-term — catches
        # news spikes / wars / BoJ shocks where stops blow through.
        if self.variant.atr_surge_threshold > 0:
            atr_short = self._compute_atr(self._history, n=14)
            atr_long = self._compute_atr(self._history, n=50)
            if atr_long > 0 and (atr_short / atr_long) > self.variant.atr_surge_threshold:
                self.rej_atr_surge += 1
                return

        # Regime filter (variant-driven). Classify the current bar's
        # market regime and reject if not in the allow-list.
        # The walk-forward audit showed pattern detector collapses in
        # RANGING/QUIET regimes — production should restrict to TREND.
        if self.variant.allowed_regimes or self.variant.min_adx > 0:
            reading = self.regime_detector.classify(self._history)
            self._last_regime = reading.regime
            self._last_adx = reading.adx
            if (self.variant.allowed_regimes
                    and reading.regime not in self.variant.allowed_regimes):
                self.rej_regime += 1
                return
            if (self.variant.min_adx > 0
                    and reading.adx < self.variant.min_adx):
                self.rej_regime += 1
                return

        # Build the DataFrame from rolling history
        try:
            import pandas as pd
            df = pd.DataFrame([{
                "time": b.time, "open": b.open, "high": b.high,
                "low": b.low, "close": b.close, "volume": b.volume,
            } for b in self._history])
            df = df.set_index("time")
        except Exception:
            return

        try:
            analysis = self.cm.analyze(df, pair="EUR_USD",
                                       pair_pip=self.config.pair_pip)
        except Exception:
            return
        if analysis is None:
            return
        plan = getattr(analysis, "plan", None)
        if plan is None or not getattr(plan, "is_actionable", False):
            return

        # Variant filter: reject the plan based on hour / setup /
        # confidence / R:R thresholds. Counted as `rej_variant`.
        accept, reason = self.variant.accept(
            bar_time=bar.time,
            setup_type=getattr(plan, "setup_type", "unknown"),
            confidence=float(getattr(plan, "confidence", 0.0)),
            rr_ratio=float(getattr(plan, "rr_ratio", 0.0)),
        )
        if not accept:
            self.rej_variant += 1
            return

        self.signals_generated += 1

        # Risk check + sizing
        verdict: RiskVerdict = self.risk.evaluate_entry(
            state=self._risk_state,
            direction=plan.direction,
            entry_price=plan.entry_price,
            stop_price=plan.stop_price,
        )
        if not verdict.allow:
            self.rej_risk += 1
            return

        # Queue the signal for fill on the NEXT bar
        self._pending = _PendingSignal(
            direction=plan.direction,
            setup_type=getattr(plan, "setup_type", "unknown"),
            entry_price=plan.entry_price,
            stop_price=plan.stop_price,
            target_price=plan.target_price,
            rr_ratio=getattr(plan, "rr_ratio", 0.0),
            plan_confidence=float(getattr(plan, "confidence", 0.0)),
            plan_rationale=str(getattr(plan, "rationale", ""))[:500],
            time_budget_bars=int(getattr(plan, "time_budget_bars", 12)),
            sized_lot=verdict.sized_lot,
            sized_risk_currency=verdict.sized_risk_currency,
            signal_bar_time=bar.time,
        )

    # ==================================================================
    # Trade closing — book the P&L, journal the trade.
    # ==================================================================
    def _close(self, pos: _OpenPosition,
               *, fill: FillResult, exit_reason: str,
               bar: BacktestBar) -> None:
        """Close `pos` at `fill.fill_price`, update equity + risk state,
        write a TradeRecord into SmartNoteBook (if provided).
        """
        pip_move, pnl_currency = self.costs.pnl_currency(
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=fill.fill_price,
            lot=pos.lot_size,
            entry_commission=pos.entry_commission,
            exit_commission=fill.commission_currency,
        )
        # Update risk state
        self._risk_state = self.risk.on_trade_closed(
            state=self._risk_state, pnl_currency=pnl_currency,
        )
        self.closed_trades += 1

        # Write to SmartNoteBook
        if self.snb is not None:
            try:
                from SmartNoteBook import (
                    TradeRecord, TradeOutcome, BrainGradeRecord,
                )
                from SmartNoteBook.journal import new_trade_id
                rec = TradeRecord(
                    trade_id=new_trade_id(),
                    pair=self.config.pair,
                    opened_at=pos.entry_time,
                    closed_at=fill.fill_time,
                    brain_grades=[
                        BrainGradeRecord(
                            brain="chartmind",
                            grade=_grade_from_conf(pos.plan_confidence),
                            direction=pos.direction,
                            confidence=pos.plan_confidence,
                            rationale=pos.plan_rationale[:500],
                        ),
                    ],
                    gate_combined_confidence=pos.plan_confidence,
                    market_regime="unknown",
                    news_state="calm",
                    spread_pips_at_entry=pos.spread_pips_at_entry,
                    spread_percentile_rank=0.5,
                    setup_type=pos.setup_type,
                    direction=pos.direction,
                    entry_price=pos.entry_price,
                    stop_price=pos.stop_price,
                    target_price=pos.target_price,
                    rr_planned=pos.rr_planned,
                    time_budget_bars=int(self._open_time_budget()),
                    plan_rationale=pos.plan_rationale,
                    plan_confidence=pos.plan_confidence,
                    filled_price=pos.entry_price,
                    requested_price=pos.requested_price,
                    slippage_pips=(pos.entry_price - pos.requested_price) / self.config.pair_pip
                                   if pos.direction == "long"
                                   else (pos.requested_price - pos.entry_price) / self.config.pair_pip,
                    lot_size=pos.lot_size,
                    risk_amount_currency=pos.risk_amount_currency,
                    sizing_method="fixed_fractional",
                    broker_order_id=f"backtest-{new_trade_id()[:8]}",
                    outcome=TradeOutcome(
                        exit_price=fill.fill_price,
                        exit_reason=exit_reason,
                        closed_at=fill.fill_time,
                        pnl_currency=pnl_currency,
                        pnl_pips=pip_move,
                        r_multiple=pnl_currency / max(1e-6, pos.risk_amount_currency),
                        bars_held=pos.bars_held,
                        max_favourable_excursion_pips=pos.mfe_pips,
                        max_adverse_excursion_pips=pos.mae_pips,
                    ),
                )
                self.snb.record_trade(rec)
            except Exception:
                # Journaling failure must never block the backtest itself.
                pass

        self._open = None

    # ==================================================================
    # Helpers.
    # ==================================================================
    def _compute_atr(self, history: list, n: int = 14) -> float:
        """True-range based ATR over the last n bars in `history`.
        Returns 0.0 if insufficient data. Uses pip units for clarity.
        """
        if len(history) < n + 1:
            return 0.0
        recent = history[-(n+1):]
        trs = []
        for i in range(1, len(recent)):
            cur = recent[i]
            prev = recent[i-1]
            tr = max(
                cur.high - cur.low,
                abs(cur.high - prev.close),
                abs(cur.low - prev.close),
            )
            trs.append(tr / self.config.pair_pip)
        return sum(trs) / len(trs) if trs else 0.0

    def _open_time_budget(self) -> int:
        """The time budget the open position was opened with.

        Lives on `_pending` at signal time; we mirror it onto _open via
        the time_budget_bars constant. For now we use a sensible
        default if the budget isn't tracked on the open position.
        """
        # Variant may override the budget; otherwise default 24 bars.
        if self.variant.time_budget_override is not None:
            return self.variant.time_budget_override
        return 24

    def _reset_counters(self) -> None:
        self.bars_seen = 0
        self.signals_generated = 0
        self.rej_variant = 0
        self.rej_atr_surge = 0
        self.rej_regime = 0
        self.halt_count = 0
        self.entries_filled = 0
        self.rej_session = 0
        self.rej_calendar = 0
        self.rej_risk = 0
        self.rej_unfilled_limit = 0
        self.closed_trades = 0

    def _empty_result(self) -> BacktestResult:
        now = datetime.now(timezone.utc)
        return BacktestResult(
            config=self.config,
            started_at=now, ended_at=now,
            bars_seen=0, signals_generated=0,
            entries_filled=0,
            entries_rejected_by_session=0,
            entries_rejected_by_calendar=0,
            entries_rejected_by_risk=0,
            entries_rejected_by_unfilled_limit=0,
            closed_trades=0,
            final_equity=self.config.starting_equity,
            starting_equity=self.config.starting_equity,
            peak_equity=self.config.starting_equity,
            max_drawdown_pct=0.0,
            halted_early=False, halt_reason="empty bars",
            snb=self.snb,
        )


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _grade_from_conf(c: float) -> str:
    if c >= 0.80: return "A+"
    if c >= 0.65: return "A"
    if c >= 0.50: return "B"
    if c >= 0.35: return "C"
    return "F"
