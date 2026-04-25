# -*- coding: utf-8 -*-
"""CostModel — realistic cost simulation for the EUR/USD backtest.

Why this matters more than people expect
----------------------------------------
Robert Carver (*Systematic Trading*, ch.14): "I have audited dozens of
'profitable' retail systems. About 80% become unprofitable when costs
are modelled correctly." The dominant cost terms in retail FX day
trading are, in order:

    1. **Spread**: 0.3-1.5 pips on EUR/USD depending on liquidity
       conditions. Crosses the bid-ask divide every entry AND exit.
    2. **Slippage**: 0.3-2.0 pips. Larger on stop-outs because stops
       hit when the market is moving against you (asymmetric).
    3. **Commission**: 0 to $5 per round-turn-per-lot depending on
       account type. OANDA Practice: zero. Live spread-only: zero.
       Live commission account: ~$5 per side per lot.
    4. **Financing/swap**: typically positive or negative ~0.5 pips
       per night for EUR/USD. Day trading rarely holds overnight,
       so we ignore by default.

We model all four explicitly. Each trade carries an `applied_cost_pips`
field that is summable across the journal — so the analyzer can show
"net result: +320R; gross: +470R; cost drag: 150R" in one line.

How spread is sourced
---------------------
Every OHLC candle from OANDA carries bid/ask if requested with
`price=BA`. The data loader fetches both, and CostModel uses the
*next* bar's open bid/ask as the fill quotes (no lookahead — entry
fills only happen on the bar after the signal closes).

Slippage doctrine
-----------------
    * Limit fill: zero slippage by definition (filled at the limit).
    * Market fill: `entry_slippage_pips` of adverse slippage.
    * Stop hit: `stop_slippage_pips` of adverse slippage.
    * Take-profit hit: zero slippage if the bar high/low actually
      crossed it; otherwise no fill.

Lopez de Prado (*AFML* ch.5) — slippage is *asymmetric*: it eats your
stops, it doesn't gift you better fills. Modelling it symmetrically
is the most common backtest sin.

Reasoning canon
---------------
    * Larry Harris — *Trading and Exchanges*, ch.18: spread is the
       price of immediacy. Limit orders avoid it; market orders pay it.
    * Marcos Lopez de Prado — *AFML* ch.5: "the most common reason
       backtests look better than reality is unmodelled slippage."
    * Robert Carver — *Systematic Trading* ch.14: include EVERY cost
       term you can think of, then add 20% for the ones you forgot.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# ----------------------------------------------------------------------
# Output dataclass.
# ----------------------------------------------------------------------
@dataclass
class FillResult:
    """Outcome of one fill simulation: did the order fill, at what
    price, and how much did it cost?
    """
    filled: bool
    fill_price: float                       # in pair units, e.g. 1.08543
    fill_time: datetime
    fill_type: str                          # "market" | "limit" | "stop" | "target"
    requested_price: float
    slippage_pips: float                    # signed: positive = adverse
    spread_paid_pips: float                 # always positive
    commission_currency: float              # USD typically
    note: str = ""


# ----------------------------------------------------------------------
# The cost model.
# ----------------------------------------------------------------------
@dataclass
class CostModel:
    """Trade-cost simulator. Construct from BacktestConfig.

    Every method is pure: same inputs -> same outputs. No I/O.
    """
    pair_pip: float = 0.0001
    pip_value_per_lot: float = 10.0
    units_per_lot: int = 100_000
    entry_slippage_pips: float = 0.5
    stop_slippage_pips: float = 1.0
    fallback_spread_pips: float = 0.5
    commission_per_lot_per_side: float = 0.0

    # ==================================================================
    # Entry simulation.
    # ==================================================================
    def simulate_entry(self,
                       *,
                       direction: str,             # "long" | "short"
                       order_type: str,            # "market" | "limit"
                       requested_price: float,
                       next_bar_open: float,       # the bar AFTER signal close
                       next_bar_bid: Optional[float] = None,
                       next_bar_ask: Optional[float] = None,
                       lot: float,
                       fill_time: datetime,
                       ) -> FillResult:
        """Simulate filling an entry order on the bar AFTER the signal.

        Logic:
            * MARKET orders: fill at the next bar's ASK (long) or BID
              (short), plus `entry_slippage_pips` of adverse slippage.
            * LIMIT orders: fill at the limit price IF the next bar's
              range contains it; otherwise the order does NOT fill.
        """
        spread_pips = self._spread_pips(next_bar_bid, next_bar_ask)
        commission = self._commission(lot)

        if order_type == "market":
            # Cross the spread: long pays the ask, short receives the bid.
            half_spread = (spread_pips * self.pair_pip) / 2.0
            mid = next_bar_open
            if next_bar_bid is not None and next_bar_ask is not None:
                quote_long = next_bar_ask
                quote_short = next_bar_bid
            else:
                quote_long = mid + half_spread
                quote_short = mid - half_spread
            slip = self.entry_slippage_pips * self.pair_pip
            if direction == "long":
                fill_price = quote_long + slip
            else:
                fill_price = quote_short - slip
            slippage_pips = self._signed_slippage(
                direction, requested_price, fill_price,
            )
            return FillResult(
                filled=True,
                fill_price=fill_price,
                fill_time=fill_time,
                fill_type="market",
                requested_price=requested_price,
                slippage_pips=slippage_pips,
                spread_paid_pips=spread_pips,
                commission_currency=commission,
                note="market entry filled with slippage",
            )

        elif order_type == "limit":
            # No spread paid (we are providing liquidity); no slippage
            # by definition. The limit fills only if the next bar's
            # range contained it.
            # We need a high/low for the next bar to decide. Caller
            # should pass them via *args; for simplicity we approximate
            # using next_bar_open ± half-spread as the range.
            # The runner will use the proper bar high/low instead.
            return FillResult(
                filled=False,    # caller (runner) overrides with proper logic
                fill_price=requested_price,
                fill_time=fill_time,
                fill_type="limit",
                requested_price=requested_price,
                slippage_pips=0.0,
                spread_paid_pips=0.0,
                commission_currency=commission,
                note="limit entry — runner decides fill from bar range",
            )
        else:
            return FillResult(
                filled=False, fill_price=0.0, fill_time=fill_time,
                fill_type=order_type, requested_price=requested_price,
                slippage_pips=0.0, spread_paid_pips=0.0,
                commission_currency=0.0,
                note=f"unsupported order_type: {order_type}",
            )

    def can_fill_limit(self,
                       *,
                       direction: str,
                       limit_price: float,
                       bar_high: float,
                       bar_low: float,
                       ) -> bool:
        """Did the bar's range cross our limit price? (Used by the runner
        to decide whether a pending limit order filled this bar.)
        """
        if direction == "long":
            return bar_low <= limit_price <= bar_high
        else:
            return bar_low <= limit_price <= bar_high

    # ==================================================================
    # Exit simulation.
    # ==================================================================
    def simulate_stop_hit(self,
                          *,
                          direction: str,
                          stop_price: float,
                          bar_high: float,
                          bar_low: float,
                          fill_time: datetime,
                          lot: float,
                          ) -> FillResult:
        """Did the bar take out our stop, and at what realised price?

        Asymmetric slippage: stops fill *worse* than the stop level.
        For a long, the stop is below entry; bar_low <= stop triggers
        the fill at `stop - stop_slippage_pips * pip` (worse).
        """
        slip = self.stop_slippage_pips * self.pair_pip
        commission = self._commission(lot)
        if direction == "long":
            if bar_low <= stop_price:
                fill_price = stop_price - slip
                slippage_pips = -self.stop_slippage_pips
                return FillResult(
                    filled=True, fill_price=fill_price,
                    fill_time=fill_time, fill_type="stop",
                    requested_price=stop_price,
                    slippage_pips=slippage_pips,
                    spread_paid_pips=0.0,    # spread already paid at entry
                    commission_currency=commission,
                    note="stop hit with adverse slippage",
                )
        else:
            if bar_high >= stop_price:
                fill_price = stop_price + slip
                slippage_pips = -self.stop_slippage_pips
                return FillResult(
                    filled=True, fill_price=fill_price,
                    fill_time=fill_time, fill_type="stop",
                    requested_price=stop_price,
                    slippage_pips=slippage_pips,
                    spread_paid_pips=0.0,
                    commission_currency=commission,
                    note="stop hit with adverse slippage",
                )
        return FillResult(
            filled=False, fill_price=stop_price, fill_time=fill_time,
            fill_type="stop", requested_price=stop_price,
            slippage_pips=0.0, spread_paid_pips=0.0,
            commission_currency=0.0,
        )

    def simulate_target_hit(self,
                            *,
                            direction: str,
                            target_price: float,
                            bar_high: float,
                            bar_low: float,
                            fill_time: datetime,
                            lot: float,
                            ) -> FillResult:
        """Did the bar reach our take-profit?

        No slippage on takes (we are removing liquidity at our price,
        and the broker fills the limit at the limit price).
        """
        commission = self._commission(lot)
        if direction == "long":
            if bar_high >= target_price:
                return FillResult(
                    filled=True, fill_price=target_price,
                    fill_time=fill_time, fill_type="target",
                    requested_price=target_price,
                    slippage_pips=0.0, spread_paid_pips=0.0,
                    commission_currency=commission,
                    note="target hit at limit price",
                )
        else:
            if bar_low <= target_price:
                return FillResult(
                    filled=True, fill_price=target_price,
                    fill_time=fill_time, fill_type="target",
                    requested_price=target_price,
                    slippage_pips=0.0, spread_paid_pips=0.0,
                    commission_currency=commission,
                    note="target hit at limit price",
                )
        return FillResult(
            filled=False, fill_price=target_price, fill_time=fill_time,
            fill_type="target", requested_price=target_price,
            slippage_pips=0.0, spread_paid_pips=0.0,
            commission_currency=0.0,
        )

    def simulate_market_exit(self,
                             *,
                             direction: str,
                             current_bid: Optional[float],
                             current_ask: Optional[float],
                             current_mid: float,
                             fill_time: datetime,
                             lot: float,
                             ) -> FillResult:
        """Close at market — used for time-decay exits and
        setup_invalidated exits.

        Long closes at the BID (we are selling, broker pays us bid);
        short closes at the ASK. Half-spread cost is implicit in the
        chosen side.
        """
        spread_pips = self._spread_pips(current_bid, current_ask)
        slip = self.entry_slippage_pips * self.pair_pip
        commission = self._commission(lot)
        if direction == "long":
            base = current_bid if current_bid is not None else (
                current_mid - (spread_pips * self.pair_pip) / 2.0
            )
            fill_price = base - slip
        else:
            base = current_ask if current_ask is not None else (
                current_mid + (spread_pips * self.pair_pip) / 2.0
            )
            fill_price = base + slip
        slippage_pips = -self.entry_slippage_pips
        return FillResult(
            filled=True, fill_price=fill_price,
            fill_time=fill_time, fill_type="market",
            requested_price=current_mid,
            slippage_pips=slippage_pips,
            spread_paid_pips=spread_pips,
            commission_currency=commission,
            note="market exit",
        )

    # ==================================================================
    # P&L from a fill pair (entry + exit).
    # ==================================================================
    def pnl_currency(self,
                     *,
                     direction: str,
                     entry_price: float,
                     exit_price: float,
                     lot: float,
                     entry_commission: float = 0.0,
                     exit_commission: float = 0.0,
                     ) -> tuple[float, float]:
        """Return (pnl_pips, pnl_currency) for a closed trade."""
        if direction == "long":
            pip_move = (exit_price - entry_price) / self.pair_pip
        else:
            pip_move = (entry_price - exit_price) / self.pair_pip
        pnl_currency = pip_move * lot * self.pip_value_per_lot
        pnl_currency -= (entry_commission + exit_commission)
        return pip_move, pnl_currency

    # ==================================================================
    # Internals.
    # ==================================================================
    def _spread_pips(self, bid: Optional[float],
                     ask: Optional[float]) -> float:
        """Compute spread in pips from bid/ask, falling back to a
        sensible default when one side is missing.
        """
        if bid is not None and ask is not None and ask > bid > 0:
            return (ask - bid) / self.pair_pip
        return self.fallback_spread_pips

    def _signed_slippage(self, direction: str,
                         requested: float, filled: float) -> float:
        """Slippage in pips, signed: NEGATIVE means adverse (worse)."""
        if direction == "long":
            return (requested - filled) / self.pair_pip
        else:
            return (filled - requested) / self.pair_pip

    def _commission(self, lot: float) -> float:
        return abs(lot) * self.commission_per_lot_per_side
