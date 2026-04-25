# -*- coding: utf-8 -*-
"""OandaAccount — snapshot the OANDA account and reconcile against local state.

Why reconciliation matters
--------------------------
The system maintains its own Portfolio (GateMind/portfolio.py) on
disk. The broker (OANDA) maintains its own authoritative view. These
can drift if:
    * a process crash leaves a position open on OANDA but not in our
      local file
    * a manual close from the OANDA web UI removes a position the
      local file still thinks is open
    * a partial fill writes one transaction OANDA-side but the
      local file recorded it as full

`fetch_account_snapshot()` returns a clean snapshot of the OANDA
side; `reconcile_with_local_portfolio()` produces a diff that the
operator can act on at process boot — the system refuses to start
when there's a meaningful drift, because trading on a wrong portfolio
view is the fastest way to compound errors.

What the snapshot carries
-------------------------
    balance, NAV, unrealised P&L, margin used / available
    list of open trades (instrument, units, price, stop, take, time)
    list of pending orders (rare for our system but tracked)
    account currency (USD / EUR / etc.)

We deliberately keep this small — anything more should go in a
dedicated module.

Reasoning canon
---------------
    * Carver — *Systematic Trading*: never trade on stale state. The
      first thing a live system does at boot is reconcile its own
      view against the broker's authoritative view.
    * Schwager — Market Wizards: every champion has the same answer
      to "what blew up your worst trade?" — "I didn't know what
      I had on." Snapshot + reconcile prevents that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from .client import OandaClient
from .instruments import from_oanda_pair, to_oanda_pair


# ----------------------------------------------------------------------
# Snapshot dataclass.
# ----------------------------------------------------------------------
@dataclass
class OandaTrade:
    """One open trade as OANDA reports it."""
    trade_id: str                   # OANDA's trade id
    pair: str                       # in our "EUR/USD" form
    direction: str                  # "long" | "short"
    units: int                      # signed; positive = long
    open_price: float
    open_time: Optional[datetime]
    stop_loss_price: Optional[float]
    take_profit_price: Optional[float]
    unrealized_pl: float
    margin_used: float
    client_order_id: str = ""

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        if isinstance(self.open_time, datetime):
            d["open_time"] = self.open_time.isoformat()
        return d


@dataclass
class OandaAccountSnapshot:
    """Authoritative view of the account at one instant."""
    fetched_at: datetime
    account_id: str
    environment: str
    currency: str
    balance: float
    nav: float
    unrealized_pl: float
    margin_used: float
    margin_available: float
    open_trade_count: int
    open_position_count: int
    pending_order_count: int
    open_trades: list[OandaTrade] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "fetched_at": self.fetched_at.isoformat(),
            "account_id": self.account_id,
            "environment": self.environment,
            "currency": self.currency,
            "balance": self.balance,
            "nav": self.nav,
            "unrealized_pl": self.unrealized_pl,
            "margin_used": self.margin_used,
            "margin_available": self.margin_available,
            "open_trade_count": self.open_trade_count,
            "open_position_count": self.open_position_count,
            "pending_order_count": self.pending_order_count,
            "open_trades": [t.to_dict() for t in self.open_trades],
        }

    def one_line_summary(self) -> str:
        return (
            f"OANDA[{self.environment}/{self.account_id}] "
            f"NAV={self.nav:.2f} {self.currency} "
            f"openTrades={self.open_trade_count} "
            f"unrealPL={self.unrealized_pl:+.2f}"
        )


# ----------------------------------------------------------------------
# Reconciliation result.
# ----------------------------------------------------------------------
@dataclass
class ReconciliationResult:
    """Diff between local Portfolio and OANDA snapshot.

    `is_clean` is True when the two views agree on:
        * count of open positions
        * direction + side per position
        * lot size (within `lot_tolerance`)

    `is_clean=False` means trading should NOT begin until the operator
    resolves the drift — the system can not assume which side is right.
    """
    is_clean: bool
    local_positions: int
    oanda_trades: int
    only_in_local: list[dict] = field(default_factory=list)   # broker_order_ids
    only_in_oanda: list[dict] = field(default_factory=list)
    mismatched: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ----------------------------------------------------------------------
# Public functions.
# ----------------------------------------------------------------------
def fetch_account_snapshot(client: OandaClient) -> Optional[OandaAccountSnapshot]:
    """Fetch a complete OandaAccountSnapshot. Returns None on failure.

    The /summary endpoint gives equity & margin; /openTrades gives
    individual trade rows. We do both calls; the summary is cheap and
    always returned, the trades call is the one that can be empty.
    """
    summary = client.get(client.account_path("/summary"))
    trades_resp = client.get(client.account_path("/openTrades"))
    now = datetime.now(timezone.utc)

    if not summary.ok or not isinstance(summary.data, dict):
        return None
    acc = summary.data.get("account", {})

    open_trades: list[OandaTrade] = []
    if trades_resp.ok and isinstance(trades_resp.data, dict):
        for t in trades_resp.data.get("trades", []) or []:
            ot = _parse_trade(t)
            if ot is not None:
                open_trades.append(ot)

    return OandaAccountSnapshot(
        fetched_at=now,
        account_id=client.account_id,
        environment=client.environment,
        currency=str(acc.get("currency", "")),
        balance=_f(acc.get("balance")),
        nav=_f(acc.get("NAV")),
        unrealized_pl=_f(acc.get("unrealizedPL")),
        margin_used=_f(acc.get("marginUsed")),
        margin_available=_f(acc.get("marginAvailable")),
        open_trade_count=int(acc.get("openTradeCount") or 0),
        open_position_count=int(acc.get("openPositionCount") or 0),
        pending_order_count=int(acc.get("pendingOrderCount") or 0),
        open_trades=open_trades,
    )


def reconcile_with_local_portfolio(*,
                                   snapshot: OandaAccountSnapshot,
                                   local_portfolio: Any,
                                   pair: str,
                                   units_per_lot: int = 100_000,
                                   lot_tolerance: float = 0.01,
                                   ) -> ReconciliationResult:
    """Compare the OANDA snapshot to the local GateMind Portfolio.

    `local_portfolio` is a GateMind.Portfolio instance; we call
    `open_for_pair(pair)` and translate to a comparable shape.

    Strategy:
        * Build a dict keyed by broker_order_id (which we wrote into
          each Position when GateMind opened it). OANDA's tradeID
          maps to that field on our side via the receipt.
        * For each side, mark only_in_local / only_in_oanda /
          mismatched (different lot or direction).
        * `is_clean` is True only when all three lists are empty.

    Note: we cannot always match by id (OANDA assigns a different
    "tradeID" than the "orderID" we recorded). When ids do not match,
    we fall back to matching by (direction, lot) on the same pair.
    The notes list explains how matches were made.
    """
    notes: list[str] = []

    try:
        locals_ = local_portfolio.open_for_pair(pair)
    except Exception as e:
        notes.append(f"local_portfolio.open_for_pair raised: {e}")
        locals_ = []

    oanda_target = to_oanda_pair(pair)
    oanda_trades = [t for t in snapshot.open_trades
                    if to_oanda_pair(t.pair) == oanda_target]

    # First try id-based match.
    local_by_id: dict[str, Any] = {}
    for p in locals_:
        boid = getattr(p, "broker_order_id", "") or ""
        if boid:
            local_by_id[boid] = p

    matched_local: set[int] = set()
    matched_oanda: set[int] = set()

    # Pass 1: id match (rare but unambiguous).
    for io, t in enumerate(oanda_trades):
        for il, p in enumerate(locals_):
            if il in matched_local:
                continue
            if t.client_order_id and t.client_order_id == getattr(p, "broker_order_id", ""):
                matched_local.add(il)
                matched_oanda.add(io)
                notes.append(
                    f"matched local[{il}] <-> oanda[{io}] by client_order_id"
                )
                break

    # Pass 2: shape match (direction + lot within tolerance).
    for io, t in enumerate(oanda_trades):
        if io in matched_oanda:
            continue
        for il, p in enumerate(locals_):
            if il in matched_local:
                continue
            local_lot = float(getattr(p, "lot", 0.0))
            oanda_lot = abs(t.units) / units_per_lot
            if (p.direction == t.direction
                    and abs(local_lot - oanda_lot) <= lot_tolerance):
                matched_local.add(il)
                matched_oanda.add(io)
                notes.append(
                    f"matched local[{il}] <-> oanda[{io}] by shape "
                    f"(direction+lot)"
                )
                break

    only_in_local = [
        {"broker_order_id": getattr(p, "broker_order_id", ""),
         "direction": p.direction, "lot": float(getattr(p, "lot", 0.0))}
        for il, p in enumerate(locals_) if il not in matched_local
    ]
    only_in_oanda = [
        {"trade_id": t.trade_id, "direction": t.direction,
         "units": t.units, "lot": abs(t.units) / units_per_lot}
        for io, t in enumerate(oanda_trades) if io not in matched_oanda
    ]

    is_clean = (not only_in_local and not only_in_oanda)
    return ReconciliationResult(
        is_clean=is_clean,
        local_positions=len(locals_),
        oanda_trades=len(oanda_trades),
        only_in_local=only_in_local,
        only_in_oanda=only_in_oanda,
        mismatched=[],   # reserved for future use (e.g. stop drift)
        notes=notes,
    )


# ----------------------------------------------------------------------
# Internals.
# ----------------------------------------------------------------------
def _parse_trade(t: dict) -> Optional[OandaTrade]:
    try:
        units = int(t.get("currentUnits") or t.get("initialUnits") or 0)
        if units == 0:
            return None
        direction = "long" if units > 0 else "short"
        instrument = str(t.get("instrument", ""))
        open_price = _f(t.get("price"))
        open_time = _parse_oanda_time(t.get("openTime", ""))
        unrealized = _f(t.get("unrealizedPL"))
        margin_used = _f(t.get("marginUsed"))

        sl_obj = t.get("stopLossOrder") or {}
        tp_obj = t.get("takeProfitOrder") or {}
        sl = _f(sl_obj.get("price")) if sl_obj else None
        tp = _f(tp_obj.get("price")) if tp_obj else None
        if sl is not None and sl == 0.0:
            sl = None
        if tp is not None and tp == 0.0:
            tp = None

        client_id = ""
        ce = t.get("clientExtensions") or {}
        if isinstance(ce, dict):
            client_id = str(ce.get("id", ""))

        return OandaTrade(
            trade_id=str(t.get("id", "")),
            pair=from_oanda_pair(instrument),
            direction=direction,
            units=units,
            open_price=open_price,
            open_time=open_time,
            stop_loss_price=sl,
            take_profit_price=tp,
            unrealized_pl=unrealized,
            margin_used=margin_used,
            client_order_id=client_id,
        )
    except (TypeError, ValueError):
        return None


def _f(x: Any) -> float:
    try:
        return float(x) if x not in (None, "", "NA") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_oanda_time(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    if "." in s:
        head, _, rest = s.partition(".")
        frac = ""
        suffix = ""
        for i, ch in enumerate(rest):
            if ch.isdigit() and len(frac) < 6:
                frac += ch
            else:
                suffix = rest[i:]
                break
        s = f"{head}.{frac}{suffix or 'Z'}"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
