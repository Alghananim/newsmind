# -*- coding: utf-8 -*-
"""Portfolio state — the live picture of equity, exposure, drawdown.

GateMind needs to answer four questions on every bar:

    1. What is my current equity? (cash + unrealised P&L)
    2. How much have I lost or made today?
    3. How far am I from my peak equity? (drawdown)
    4. What positions are open and how big is my exposure?

These four numbers gate every decision: a trade is rejected if it
breaches today's loss budget, if drawdown is already past a hard
limit, or if a position is already open in this pair (single-position
rule for scalping).

Doctrine
--------
Drawdown control is the single most cited principle across Schwager's
Market Wizards. Paul Tudor Jones: "I'm always thinking about losing
money as opposed to making money." Larry Hite: "If you don't bet,
you can't win. If you lose all your chips, you can't bet." Bruce
Kovner: "The first thing I think about is losing." Daily-loss limits
and drawdown caps are the codification of that doctrine.

State storage
-------------
We keep state in-process (a Python dict) and persist a JSON snapshot
to disk after every mutation. This is intentionally not a database —
the data volume is tiny and a file-on-disk is auditable, copyable,
and robust to crashes. The snapshot file is the single source of
truth between process restarts.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, asdict, field
from datetime import datetime, date, timezone
from typing import Optional


# --------------------------------------------------------------------
# Position record.
# --------------------------------------------------------------------
@dataclass
class Position:
    """One open position. Closed positions migrate to Ledger."""
    pair: str
    direction: str               # "long" | "short"
    lot: float
    entry_price: float
    stop_price: float
    target_price: float
    opened_at: datetime
    risk_amount: float           # currency at risk (1R)
    broker_order_id: str = ""    # filled by execution router
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(self.opened_at, datetime):
            d["opened_at"] = self.opened_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Position":
        d2 = dict(d)
        ts = d2.get("opened_at")
        if isinstance(ts, str):
            d2["opened_at"] = datetime.fromisoformat(ts)
        return cls(**d2)


# --------------------------------------------------------------------
# Snapshot of portfolio state.
# --------------------------------------------------------------------
@dataclass
class PortfolioSnapshot:
    equity: float                # cash equity, marked-to-market
    cash: float                  # uninvested cash
    peak_equity: float           # highest equity ever recorded
    drawdown: float              # 0..1 fraction (peak - equity) / peak
    today_realised: float        # P&L closed today
    today_open_unrealised: float # unrealised on still-open today
    today_starting_equity: float # equity at first bar today
    daily_pnl_pct: float         # (today_realised + today_open) / today_starting
    open_positions: list[Position]
    last_update: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "equity": self.equity,
            "cash": self.cash,
            "peak_equity": self.peak_equity,
            "drawdown": self.drawdown,
            "today_realised": self.today_realised,
            "today_open_unrealised": self.today_open_unrealised,
            "today_starting_equity": self.today_starting_equity,
            "daily_pnl_pct": self.daily_pnl_pct,
            "open_positions": [p.to_dict() for p in self.open_positions],
            "last_update": self.last_update.isoformat()
                if isinstance(self.last_update, datetime) else None,
        }


# --------------------------------------------------------------------
# Portfolio — thread-safe state container.
# --------------------------------------------------------------------
class Portfolio:
    """Live portfolio state with disk persistence.

    Usage:
        port = Portfolio.load_or_init(
            path="state/portfolio.json",
            starting_equity=10000.0,
            pair_pip=0.0001,
            pip_value_per_lot=10.0,
        )
        port.open_position(pos)
        port.mark_to_market(current_price=1.0850, pair="EUR_USD")
        port.close_position(pos, exit_price=1.0860)
    """

    def __init__(
        self,
        starting_equity: float,
        path: str,
        pair_pip: float = 0.0001,
        pip_value_per_lot: float = 10.0,
    ):
        self._lock = threading.RLock()
        self._path = path
        self._pair_pip = pair_pip
        self._pip_value_per_lot = pip_value_per_lot
        self._cash = float(starting_equity)
        self._peak_equity = float(starting_equity)
        self._open: list[Position] = []
        self._today_realised: float = 0.0
        self._today_starting_equity: float = float(starting_equity)
        self._today: Optional[date] = None
        self._last_update: Optional[datetime] = None
        # Mark-to-market cache: {pair: last_price}
        self._mtm: dict[str, float] = {}

    # ----- persistence --------------------------------------------
    @classmethod
    def load_or_init(
        cls,
        path: str,
        starting_equity: float,
        pair_pip: float = 0.0001,
        pip_value_per_lot: float = 10.0,
    ) -> "Portfolio":
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    state = json.load(f)
                p = cls(
                    starting_equity=state.get("cash", starting_equity),
                    path=path,
                    pair_pip=pair_pip,
                    pip_value_per_lot=pip_value_per_lot,
                )
                p._cash = float(state.get("cash", starting_equity))
                p._peak_equity = float(state.get("peak_equity", p._cash))
                p._today_realised = float(state.get("today_realised", 0.0))
                p._today_starting_equity = float(
                    state.get("today_starting_equity", p._cash)
                )
                today_str = state.get("today_date")
                if today_str:
                    try:
                        p._today = date.fromisoformat(today_str)
                    except Exception:
                        p._today = None
                p._open = [Position.from_dict(d) for d in state.get("open", [])]
                return p
            except Exception:
                # Corruption: fall through and start fresh. The caller
                # will see a fresh portfolio and can investigate the
                # damaged file at its leisure.
                pass
        return cls(
            starting_equity=starting_equity,
            path=path,
            pair_pip=pair_pip,
            pip_value_per_lot=pip_value_per_lot,
        )

    def _persist(self) -> None:
        # Atomic write: temp file + rename, so crashes can't corrupt.
        tmp = self._path + ".tmp"
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(tmp, "w") as f:
            json.dump({
                "cash": self._cash,
                "peak_equity": self._peak_equity,
                "today_realised": self._today_realised,
                "today_starting_equity": self._today_starting_equity,
                "today_date": self._today.isoformat() if self._today else None,
                "open": [p.to_dict() for p in self._open],
            }, f, indent=2)
        os.replace(tmp, self._path)

    # ----- daily roll ---------------------------------------------
    def _roll_day_if_needed(self, now_utc: Optional[datetime] = None) -> None:
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        if self._today is None or today != self._today:
            self._today = today
            self._today_realised = 0.0
            self._today_starting_equity = self._equity_value()

    # ----- mark to market -----------------------------------------
    def mark_to_market(self, pair: str, current_price: float) -> None:
        """Record latest price for a pair so unrealised P&L computes."""
        with self._lock:
            self._mtm[pair] = float(current_price)
            self._last_update = datetime.now(timezone.utc)

    def _unrealised_per_position(self, p: Position) -> float:
        last = self._mtm.get(p.pair)
        if last is None:
            return 0.0
        if p.direction == "long":
            move_price = last - p.entry_price
        else:
            move_price = p.entry_price - last
        if self._pair_pip <= 0:
            return 0.0
        pips = move_price / self._pair_pip
        return pips * p.lot * self._pip_value_per_lot

    def _unrealised_total(self) -> float:
        return sum(self._unrealised_per_position(p) for p in self._open)

    def _equity_value(self) -> float:
        return self._cash + self._unrealised_total()

    # ----- position lifecycle -------------------------------------
    def open_position(self, p: Position) -> None:
        with self._lock:
            self._roll_day_if_needed()
            self._open.append(p)
            self._last_update = datetime.now(timezone.utc)
            self._persist()

    def close_position(self, p: Position, exit_price: float,
                       now_utc: Optional[datetime] = None) -> float:
        """Close a position; return realised P&L (currency)."""
        with self._lock:
            self._roll_day_if_needed(now_utc)
            move_price = (
                exit_price - p.entry_price
                if p.direction == "long"
                else p.entry_price - exit_price
            )
            pips = move_price / self._pair_pip if self._pair_pip > 0 else 0.0
            pnl = pips * p.lot * self._pip_value_per_lot
            self._cash += pnl
            self._today_realised += pnl
            if p in self._open:
                self._open.remove(p)
            equity = self._equity_value()
            if equity > self._peak_equity:
                self._peak_equity = equity
            self._last_update = datetime.now(timezone.utc)
            self._persist()
            return pnl

    def has_open_in(self, pair: str) -> bool:
        with self._lock:
            return any(p.pair == pair for p in self._open)

    def open_count(self) -> int:
        with self._lock:
            return len(self._open)

    def open_for_pair(self, pair: str) -> list[Position]:
        with self._lock:
            return [p for p in self._open if p.pair == pair]

    # ----- snapshot -----------------------------------------------
    def snapshot(self) -> PortfolioSnapshot:
        with self._lock:
            self._roll_day_if_needed()
            equity = self._equity_value()
            unrealised = self._unrealised_total()
            peak = max(self._peak_equity, equity)
            drawdown = (
                max(0.0, peak - equity) / peak if peak > 0 else 0.0
            )
            daily_pnl = (
                self._today_realised + unrealised
            )
            daily_pnl_pct = (
                daily_pnl / self._today_starting_equity
                if self._today_starting_equity > 0 else 0.0
            )
            return PortfolioSnapshot(
                equity=float(equity),
                cash=float(self._cash),
                peak_equity=float(peak),
                drawdown=float(drawdown),
                today_realised=float(self._today_realised),
                today_open_unrealised=float(unrealised),
                today_starting_equity=float(self._today_starting_equity),
                daily_pnl_pct=float(daily_pnl_pct),
                open_positions=list(self._open),
                last_update=self._last_update,
            )
