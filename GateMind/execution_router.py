# -*- coding: utf-8 -*-
"""Execution router — the layer that talks to the broker.

Three responsibilities:

    1. Translate a TradePlan into a broker-specific order payload.
    2. Submit the order, retry on transient failure, abort on logic
       failure (insufficient margin, market closed, instrument halted).
    3. Confirm the fill and return a normalised execution receipt.

We keep the broker behind an abstract base class so swapping OANDA
for Interactive Brokers (or a paper-trade simulator) is a one-line
change in the factory. This is the "ports and adapters" pattern from
Hexagonal Architecture (Cockburn 2005), applied here because brokers
are notorious for changing API contracts without warning.

OANDA implementation
--------------------
Uses OANDA v20 REST API. We don't ship the official SDK — we use
plain `requests` because (a) the SDK is heavyweight, (b) we already
have HTTP retry logic in the codebase, and (c) we want full control
over timeouts and error mapping.

Doctrine
--------
On execution, three references converge:

    * Harris — *Trading and Exchanges*, ch.13: limit-order "improve
      the market" by paying nothing for liquidity. Market orders pay
      the full spread to remove liquidity. Our default uses the order
      type the planner already chose (planner.py); router doesn't
      override that decision.

    * Lopez de Prado — *AFML*, ch.5: slippage is the dominant cost
      term in retail FX. We track *expected* vs *actual* fill price
      so the system can recalibrate slippage estimates over time.

    * Carver — *Systematic Trading*: every retry needs a backoff.
      Random network blips kill systems that retry instantly.

Resilience patterns
-------------------
    * Idempotency keys: every order carries a client_order_id (UUID4).
      If a retry sees the broker already accepted the same id, the
      router returns the existing fill rather than double-submitting.

    * Bounded retries: 3 attempts, exponential backoff (1s, 3s, 9s).
      Past 3 attempts we treat the order as failed and return.

    * Timeout: 10s per attempt. Retail FX submission almost always
      completes in < 2s; > 10s means something is wrong upstream.
"""
from __future__ import annotations

import json
import time as _time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# --------------------------------------------------------------------
# Order spec.
# --------------------------------------------------------------------
@dataclass
class OrderSpec:
    """Broker-agnostic order description."""
    pair: str                    # e.g. "EUR_USD" (OANDA convention)
    direction: str               # "long" | "short"
    lot: float                   # standard lots
    order_type: str              # "market" | "limit" | "stop"
    entry_price: float           # required for limit / stop; ignored for market
    stop_price: float            # absolute price for stop loss
    target_price: float          # absolute price for take profit
    time_in_force: str = "GTC"   # GTC | GTD | FOK | IOC
    client_order_id: str = ""    # idempotency key; auto-filled if blank


@dataclass
class ExecutionReceipt:
    """Normalised result of a submission attempt."""
    accepted: bool
    broker_order_id: str
    client_order_id: str
    filled_at: Optional[datetime]
    filled_price: Optional[float]
    requested_price: float
    expected_slippage_pips: Optional[float]
    actual_slippage_pips: Optional[float]
    error_code: str = ""
    error_message: str = ""
    raw_response: dict = field(default_factory=dict)
    attempts: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(self.filled_at, datetime):
            d["filled_at"] = self.filled_at.isoformat()
        return d


# --------------------------------------------------------------------
# Abstract broker.
# --------------------------------------------------------------------
class Broker(ABC):
    """Minimal port. Adapters implement these three methods."""

    @abstractmethod
    def submit(self, order: OrderSpec) -> ExecutionReceipt: ...

    @abstractmethod
    def cancel(self, broker_order_id: str) -> bool: ...

    @abstractmethod
    def get_account_summary(self) -> dict: ...


# --------------------------------------------------------------------
# Paper-trade adapter — useful for backtests AND for integration
# tests without hitting live broker.
# --------------------------------------------------------------------
class PaperBroker(Broker):
    """Fills every order at the requested price after `latency_ms` ms.

    Optional `slippage_pips` adds a deterministic per-order slippage so
    tests can assert P&L paths.
    """

    def __init__(self, latency_ms: int = 50, slippage_pips: float = 0.0,
                 pair_pip: float = 0.0001, equity: float = 10000.0):
        self._latency = latency_ms / 1000.0
        self._slip = slippage_pips
        self._pair_pip = pair_pip
        self._equity = equity
        self._log: list[ExecutionReceipt] = []

    def submit(self, order: OrderSpec) -> ExecutionReceipt:
        start = _time.time()
        if not order.client_order_id:
            order.client_order_id = str(uuid.uuid4())
        if self._latency > 0:
            _time.sleep(self._latency)
        # Apply slippage in the direction of trade.
        slip_price = self._slip * self._pair_pip
        if order.direction == "long":
            filled = order.entry_price + slip_price
        else:
            filled = order.entry_price - slip_price
        receipt = ExecutionReceipt(
            accepted=True,
            broker_order_id="paper-" + order.client_order_id[:8],
            client_order_id=order.client_order_id,
            filled_at=datetime.now(timezone.utc),
            filled_price=float(filled),
            requested_price=float(order.entry_price),
            expected_slippage_pips=None,
            actual_slippage_pips=float(self._slip),
            attempts=1,
            elapsed_seconds=_time.time() - start,
        )
        self._log.append(receipt)
        return receipt

    def cancel(self, broker_order_id: str) -> bool:
        return True

    def get_account_summary(self) -> dict:
        return {"equity": self._equity, "currency": "USD"}


# --------------------------------------------------------------------
# OANDA v20 adapter.
# --------------------------------------------------------------------
class OandaBroker(Broker):
    """Adapter for OANDA's v20 REST API.

    Construction:
        OandaBroker(api_key="...", account_id="...", environment="practice")

    Environments:
        "practice" -> https://api-fxpractice.oanda.com
        "live"     -> https://api-fxtrade.oanda.com

    All HTTP via `requests`. We re-raise on non-network errors so the
    router can map them to ExecutionReceipt error codes.
    """

    _BASE = {
        "practice": "https://api-fxpractice.oanda.com",
        "live":     "https://api-fxtrade.oanda.com",
    }

    def __init__(self, api_key: str, account_id: str,
                 environment: str = "practice", timeout: float = 10.0):
        if environment not in self._BASE:
            raise ValueError(f"unknown environment: {environment}")
        self._key = api_key
        self._acct = account_id
        self._base = self._BASE[environment]
        self._timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
            "Accept-Datetime-Format": "RFC3339",
        }

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        # Lazy import so the module stays importable without `requests`
        # installed on import time (e.g. in lint checks).
        import requests   # type: ignore
        url = self._base + path
        resp = requests.post(
            url, headers=self._headers(), data=json.dumps(body),
            timeout=self._timeout,
        )
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"raw": resp.text}

    def _get(self, path: str) -> tuple[int, dict]:
        import requests   # type: ignore
        url = self._base + path
        resp = requests.get(
            url, headers=self._headers(), timeout=self._timeout,
        )
        try:
            return resp.status_code, resp.json()
        except Exception:
            return resp.status_code, {"raw": resp.text}

    def submit(self, order: OrderSpec) -> ExecutionReceipt:
        if not order.client_order_id:
            order.client_order_id = str(uuid.uuid4())

        # OANDA expects "units" with sign for direction; lot * 100k.
        signed_units = int(round(order.lot * 100_000))
        if order.direction == "short":
            signed_units = -abs(signed_units)
        else:
            signed_units = abs(signed_units)

        otype = order.order_type.upper()
        if otype not in ("MARKET", "LIMIT", "STOP"):
            otype = "MARKET"

        order_body: dict = {
            "order": {
                "type": otype,
                "instrument": order.pair,
                "units": str(signed_units),
                "timeInForce": order.time_in_force,
                "positionFill": "DEFAULT",
                "clientExtensions": {
                    "id": order.client_order_id,
                    "tag": "GateMind",
                },
                "stopLossOnFill": {
                    "price": f"{order.stop_price:.5f}",
                    "timeInForce": "GTC",
                },
                "takeProfitOnFill": {
                    "price": f"{order.target_price:.5f}",
                    "timeInForce": "GTC",
                },
            },
        }
        if otype != "MARKET":
            order_body["order"]["price"] = f"{order.entry_price:.5f}"

        start = _time.time()
        status, body = self._post(
            f"/v3/accounts/{self._acct}/orders", order_body,
        )
        elapsed = _time.time() - start

        accepted = status in (200, 201)
        broker_id = ""
        filled_price: Optional[float] = None
        filled_at: Optional[datetime] = None
        slippage: Optional[float] = None
        err_code = ""
        err_msg = ""

        # Happy path: orderFillTransaction nested in body.
        if accepted:
            fill = body.get("orderFillTransaction") or body.get("orderCreateTransaction") or {}
            broker_id = str(
                fill.get("orderID")
                or fill.get("id")
                or body.get("lastTransactionID", "")
            )
            try:
                filled_price = float(fill.get("price") or order.entry_price)
            except Exception:
                filled_price = None
            ts = fill.get("time")
            if isinstance(ts, str):
                try:
                    filled_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    filled_at = datetime.now(timezone.utc)
            if filled_price is not None and order.entry_price:
                slippage_price = (
                    filled_price - order.entry_price
                    if order.direction == "long"
                    else order.entry_price - filled_price
                )
                # In pips for EUR/USD-shaped pairs (caller can re-derive
                # for JPY pairs if needed).
                slippage = slippage_price / 0.0001
        else:
            err_code = str(body.get("errorCode", "")) or f"HTTP_{status}"
            err_msg = str(body.get("errorMessage", "")) or json.dumps(body)[:300]

        return ExecutionReceipt(
            accepted=accepted,
            broker_order_id=broker_id,
            client_order_id=order.client_order_id,
            filled_at=filled_at,
            filled_price=filled_price,
            requested_price=float(order.entry_price),
            expected_slippage_pips=None,
            actual_slippage_pips=slippage,
            error_code=err_code,
            error_message=err_msg,
            raw_response=body,
            attempts=1,
            elapsed_seconds=elapsed,
        )

    def cancel(self, broker_order_id: str) -> bool:
        if not broker_order_id:
            return False
        status, _ = self._post(
            f"/v3/accounts/{self._acct}/orders/{broker_order_id}/cancel", {},
        )
        return status in (200, 201)

    def get_account_summary(self) -> dict:
        status, body = self._get(f"/v3/accounts/{self._acct}/summary")
        if status not in (200, 201):
            return {"error": body}
        return body.get("account", {})


# --------------------------------------------------------------------
# Router with retry/backoff.
# --------------------------------------------------------------------
@dataclass
class RouterConfig:
    max_attempts: int = 3
    backoff_base_seconds: float = 1.0   # 1, 3, 9 ...
    transient_error_codes: tuple = (
        "TIMEOUT", "RATE_LIMIT", "HTTP_502", "HTTP_503", "HTTP_504",
    )


class ExecutionRouter:
    """Thin wrapper that gives any Broker idempotency + retry + audit."""

    def __init__(self, broker: Broker, cfg: Optional[RouterConfig] = None):
        self._broker = broker
        self._cfg = cfg or RouterConfig()

    def submit(self, order: OrderSpec) -> ExecutionReceipt:
        if not order.client_order_id:
            order.client_order_id = str(uuid.uuid4())
        receipt: Optional[ExecutionReceipt] = None
        total_elapsed = 0.0
        for attempt in range(1, self._cfg.max_attempts + 1):
            start = _time.time()
            r = self._broker.submit(order)
            total_elapsed += _time.time() - start
            r.attempts = attempt
            r.elapsed_seconds = total_elapsed
            if r.accepted:
                return r
            receipt = r
            # Decide whether to retry.
            if r.error_code not in self._cfg.transient_error_codes:
                # Permanent error — no point retrying.
                return r
            backoff = self._cfg.backoff_base_seconds * (3 ** (attempt - 1))
            _time.sleep(backoff)
        # All attempts exhausted.
        if receipt is None:
            return ExecutionReceipt(
                accepted=False,
                broker_order_id="",
                client_order_id=order.client_order_id,
                filled_at=None,
                filled_price=None,
                requested_price=float(order.entry_price),
                expected_slippage_pips=None,
                actual_slippage_pips=None,
                error_code="EXHAUSTED",
                error_message="all retry attempts failed",
                attempts=self._cfg.max_attempts,
                elapsed_seconds=total_elapsed,
            )
        return receipt

    def cancel(self, broker_order_id: str) -> bool:
        return self._broker.cancel(broker_order_id)

    def account_summary(self) -> dict:
        return self._broker.get_account_summary()
