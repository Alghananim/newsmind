# -*- coding: utf-8 -*-
"""OandaAdapter — live data + broker for the EUR/USD trading system.

This package wires the trading system to OANDA's v20 REST API so the
brains receive real M15 candles, GateMind routes real orders, and the
SmartNoteBook journal accumulates real fills.

What the adapter gives the system
---------------------------------
    * **OandaClient** — a thin, defensive HTTP client. Reads
      OANDA_API_TOKEN, OANDA_ACCOUNT_ID, and OANDA_ENVIRONMENT from
      env vars at construction; never accepts credentials in source.
      Retries 429/5xx with exponential backoff. Surfaces every call's
      status + latency for monitoring.

    * **OandaInstruments** — fetch historical and recent candles
      (M1/M5/M15/H1/H4/D), plus current bid/ask/mid for spread
      calculations.

    * **OandaBarFeed** — a polling loop that the live runner can plug
      into Engine.step(): "give me the latest M15 closed candle since
      I last asked". Includes deduplication so a partial open candle
      never leaks into ChartMind.

    * **OandaBroker** — implements GateMind's `Broker` interface:
      submit_order, modify_stop, close_position, list_open_trades.
      Translates between GateMind's OrderSpec/ExecutionReceipt and
      OANDA's order request shapes.

    * **OandaAccountSnapshot** — equity, NAV, margin, unrealised
      P&L, list of open trades. Used at boot for reconciliation
      (compare local Portfolio JSON against OANDA's authoritative
      view) and for SmartNoteBook briefings.

Environment variables
---------------------
    OANDA_API_TOKEN      required (the personal token from OANDA)
    OANDA_ACCOUNT_ID     required ("101-001-..." for practice,
                                   "001-001-..." for live)
    OANDA_ENVIRONMENT    "practice" (default) or "live"
    OANDA_TIMEOUT_SEC    optional, default 15

Security posture
----------------
The adapter NEVER logs the token. It NEVER stores the token outside
the in-memory client object. It NEVER accepts the token as a
constructor argument from chat-driven code paths — only from
os.environ at process start.

Reasoning canon (why we wrap rather than use any community library)
-------------------------------------------------------------------
    * Robert Carver — *Systematic Trading*, ch.3: control your I/O.
      A live trading system that depends on an unpinned, abandoned
      community wrapper is a system that breaks the day the wrapper
      breaks. We write the minimum we need, in idiomatic style that
      matches the rest of the codebase, with no surprises.
    * The OANDA v20 API surface we need is small (~6 endpoints).
      A focused wrapper is shorter than configuring a generic one.
"""
from __future__ import annotations

from .client import (
    OandaClient,
    OandaConfig,
    OandaError,
    OandaResponse,
)
from .instruments import (
    OandaInstruments,
    Candle,
)
from .feed import (
    OandaBarFeed,
    Bar,
)
from .broker import OandaBroker
from .account import (
    OandaAccountSnapshot,
    fetch_account_snapshot,
    reconcile_with_local_portfolio,
)

__all__ = [
    "OandaClient", "OandaConfig", "OandaError", "OandaResponse",
    "OandaInstruments", "Candle",
    "OandaBarFeed", "Bar",
    "OandaBroker",
    "OandaAccountSnapshot",
    "fetch_account_snapshot", "reconcile_with_local_portfolio",
]

__version__ = "1.0.0"
