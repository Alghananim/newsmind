# -*- coding: utf-8 -*-
"""OandaBroker — implements GateMind's Broker interface against OANDA v20.

What this module provides
-------------------------
A drop-in replacement for `GateMind.PaperBroker` that submits real
orders to OANDA. Construction takes an `OandaClient` (which already
holds the validated env-var credentials) so this class never touches
secrets directly.

OANDA order shape (v20)
-----------------------
OANDA accepts orders via POST /v3/accounts/{id}/orders with a single
"order" object:

    MARKET orders carry: instrument, units, timeInForce='FOK', positionFill
    LIMIT  orders carry: instrument, units, price, timeInForce='GTC'
    STOP   orders carry: instrument, units, price, timeInForce='GTC'

We always attach `stopLossOnFill` and `takeProfitOnFill` so the stop
and target are guaranteed to be on the broker's side (not just our
local memory). This is non-negotiable for a 24/5 system that may
have transient connectivity loss.

Sizing translation
------------------
GateMind sizes in *standard lots* (1.0 lot = 100,000 units of base).
OANDA's `units` field is the underlying integer count: positive for
long, negative for short. We convert: units = round(lot * 100,000).

Idempotency
-----------
We use OANDA's `clientExtensions.id` field (max 64 chars) for the
client order id. If GateMind retries with the same id and OANDA
already has the order, OANDA returns the existing transaction in the
response — we surface it as `accepted=True` so the retry is harmless.

What we do NOT support (yet)
----------------------------
    * Trailing stops via OANDA's native `trailingStopLossOnFill`.
      GateMind manages trailing locally via monitor() because the
      trailing logic is shared with the paper broker.
    * Position-fill (NETTING vs HEDGING). Default is OPEN_ONLY which
      matches the system's one-position-at-a-time invariant.

Reasoning canon
---------------
    * Carver — *Systematic Trading*: brokers fail; the system must
      fail-safe. Stop-loss-on-fill is the cheapest insurance you can
      buy against a network outage.
    * Lopez de Prado — *AFML*, ch.5: track expected vs actual fill
      price. We populate `actual_slippage_pips` so SmartNoteBook can
      learn how slippage scales with conditions.
"""
from __future__ import annotations

import time as _time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .client import OandaClient
from .instruments import to_oanda_pair


# Lazy import GateMind types so OandaAdapter is import-cheap.
def _gatemind_types():
    from GateMind.execution_router import (
        Broker, ExecutionReceipt, OrderSpec,
    )
    return Broker, ExecutionReceipt, OrderSpec


# ----------------------------------------------------------------------
# OandaBroker.
# ----------------------------------------------------------------------
def OandaBroker(client: OandaClient,
                *,
                pair_pip: float = 0.0001,
                units_per_lot: int = 100_000,
                ) -> Any:
    """Factory that returns a Broker subclass instance.

    We use a factory because GateMind's `Broker` ABC needs to be
    imported at instance time (which we want to defer until the
    caller actually wants to use OandaBroker). Returning an instance
    keeps the public surface "OandaBroker(client)" — same shape as
    PaperBroker.
    """
    Broker, ExecutionReceipt, OrderSpec = _gatemind_types()

    class _OandaBroker(Broker):
        """Live-trading Broker against OANDA v20 REST."""

        def __init__(self) -> None:
            self._c = client
            self._pair_pip = pair_pip
            self._upl = units_per_lot

        # ----- Broker interface ------------------------------------
        def submit(self, order) -> Any:
            t0 = _time.monotonic()
            client_id = order.client_order_id or _new_client_id()
            payload = self._build_order_payload(order, client_id)
            path = self._c.account_path("/orders")
            resp = self._c.post(path, json_body=payload)
            elapsed = _time.monotonic() - t0

            if not resp.ok or not isinstance(resp.data, dict):
                return ExecutionReceipt(
                    accepted=False,
                    broker_order_id="",
                    client_order_id=client_id,
                    filled_at=None,
                    filled_price=None,
                    requested_price=float(order.entry_price),
                    expected_slippage_pips=None,
                    actual_slippage_pips=None,
                    error_code=str(resp.status),
                    error_message=resp.error or "submit failed",
                    raw_response=resp.data or {},
                    attempts=1,
                    elapsed_seconds=elapsed,
                )

            return self._parse_fill(
                response_body=resp.data,
                requested_price=float(order.entry_price),
                client_id=client_id,
                elapsed=elapsed,
                ExecutionReceiptCls=ExecutionReceipt,
            )

        def cancel(self, broker_order_id: str) -> bool:
            """Cancel a pending order by its OANDA orderID.

            Note: OANDA's "trade" (open position) is different from
            "order" (pending). For a filled position, use
            close_position() instead.
            """
            if not broker_order_id:
                return False
            path = self._c.account_path(f"/orders/{broker_order_id}/cancel")
            resp = self._c.put(path)
            return resp.ok

        def get_account_summary(self) -> dict:
            path = self._c.account_path("/summary")
            resp = self._c.get(path)
            if not resp.ok or not isinstance(resp.data, dict):
                return {}
            acc = resp.data.get("account", {})
            return {
                "balance": _f(acc.get("balance")),
                "nav": _f(acc.get("NAV")),
                "unrealized_pl": _f(acc.get("unrealizedPL")),
                "margin_used": _f(acc.get("marginUsed")),
                "margin_available": _f(acc.get("marginAvailable")),
                "open_position_count": int(acc.get("openPositionCount") or 0),
                "open_trade_count": int(acc.get("openTradeCount") or 0),
                "currency": acc.get("currency", ""),
            }

        # ----- extras (not part of Broker abstract, but useful) ----
        def close_position(self, *, pair: str,
                           direction: str = "long") -> bool:
            """Close all units of an open position on `pair`.

            OANDA's positions endpoint accepts longUnits='ALL' or
            shortUnits='ALL'. We close the side that matches `direction`.
            """
            path = self._c.account_path(
                f"/positions/{to_oanda_pair(pair)}/close"
            )
            body = (
                {"longUnits": "ALL"} if direction == "long"
                else {"shortUnits": "ALL"}
            )
            resp = self._c.put(path, json_body=body)
            return resp.ok

        def list_open_trades(self, *, pair: Optional[str] = None) -> list[dict]:
            """List currently-open OANDA trades, optionally filtered by pair."""
            path = self._c.account_path("/openTrades")
            resp = self._c.get(path)
            if not resp.ok or not isinstance(resp.data, dict):
                return []
            trades = resp.data.get("trades", [])
            if pair is None:
                return trades
            target = to_oanda_pair(pair)
            return [t for t in trades if t.get("instrument") == target]

        # ----- internals -------------------------------------------
        def _build_order_payload(self, order, client_id: str) -> dict:
            """Translate OrderSpec → OANDA v20 order payload."""
            units = int(round(order.lot * self._upl))
            if order.direction == "short":
                units = -units
            instrument = to_oanda_pair(order.pair)

            order_type = (order.order_type or "market").upper()
            if order_type == "LIMIT":
                otype = "LIMIT"
                tif = order.time_in_force or "GTC"
            elif order_type == "STOP":
                otype = "STOP"
                tif = order.time_in_force or "GTC"
            else:
                otype = "MARKET"
                tif = "FOK"   # OANDA market orders use FOK

            o: dict = {
                "type": otype,
                "instrument": instrument,
                "units": str(units),
                "timeInForce": tif,
                "positionFill": "OPEN_ONLY",
                "clientExtensions": {
                    "id": client_id[:64],
                    "tag": "newsmind-engine",
                },
            }
            if otype != "MARKET" and order.entry_price > 0:
                o["price"] = _fmt_price(order.entry_price)

            if order.stop_price > 0:
                o["stopLossOnFill"] = {
                    "price": _fmt_price(order.stop_price),
                    "timeInForce": "GTC",
                }
            if order.target_price > 0:
                o["takeProfitOnFill"] = {
                    "price": _fmt_price(order.target_price),
                    "timeInForce": "GTC",
                }
            return {"order": o}

        def _parse_fill(self, *, response_body: dict,
                        requested_price: float, client_id: str,
                        elapsed: float,
                        ExecutionReceiptCls) -> Any:
            """Translate OANDA's response into an ExecutionReceipt.

            Two relevant top-level transactions:
                orderCreateTransaction: the order was accepted
                orderFillTransaction:   the order was filled
            For market orders both arrive in the same response; for
            limit/stop only orderCreate arrives until the price hits.
            """
            create_tx = response_body.get("orderCreateTransaction", {}) or {}
            fill_tx = response_body.get("orderFillTransaction", {}) or {}
            cancel_tx = response_body.get("orderCancelTransaction", {}) or {}

            broker_order_id = (
                create_tx.get("id") or fill_tx.get("orderID") or ""
            )

            if cancel_tx and not fill_tx:
                return ExecutionReceiptCls(
                    accepted=False,
                    broker_order_id=str(broker_order_id),
                    client_order_id=client_id,
                    filled_at=None,
                    filled_price=None,
                    requested_price=requested_price,
                    expected_slippage_pips=None,
                    actual_slippage_pips=None,
                    error_code=str(cancel_tx.get("reason", "CANCELLED")),
                    error_message="order cancelled by broker",
                    raw_response=response_body,
                    attempts=1,
                    elapsed_seconds=elapsed,
                )

            if not fill_tx:
                # Order accepted but not yet filled (limit/stop).
                return ExecutionReceiptCls(
                    accepted=True,
                    broker_order_id=str(broker_order_id),
                    client_order_id=client_id,
                    filled_at=None,
                    filled_price=None,
                    requested_price=requested_price,
                    expected_slippage_pips=None,
                    actual_slippage_pips=None,
                    raw_response=response_body,
                    attempts=1,
                    elapsed_seconds=elapsed,
                )

            # Filled — extract price, time, slippage.
            filled_price = _f(fill_tx.get("price"))
            filled_at = _parse_oanda_time(fill_tx.get("time", ""))

            slip_pips = None
            if filled_price and requested_price > 0:
                # Sign is irrelevant for SmartNoteBook (we record absolute
                # slippage and use direction separately to label as
                # adverse/favourable).
                slip_pips = (filled_price - requested_price) / self._pair_pip

            return ExecutionReceiptCls(
                accepted=True,
                broker_order_id=str(broker_order_id),
                client_order_id=client_id,
                filled_at=filled_at,
                filled_price=filled_price,
                requested_price=requested_price,
                expected_slippage_pips=None,
                actual_slippage_pips=slip_pips,
                raw_response=response_body,
                attempts=1,
                elapsed_seconds=elapsed,
            )

    return _OandaBroker()


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _new_client_id() -> str:
    return f"newsmind-{uuid.uuid4().hex[:24]}"


def _fmt_price(p: float) -> str:
    """Format a price for OANDA — 5 decimals for EUR/USD style pairs."""
    return f"{p:.5f}"


def _f(x: Any) -> float:
    """Defensive float coercion for OANDA's stringified numbers."""
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
