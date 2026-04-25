# -*- coding: utf-8 -*-
"""OandaInstruments — candle and price fetches from the v20 instruments API.

What this module gives the system
---------------------------------
    * `OandaInstruments.candles(...)` — historical and recent OHLC bars
      at any granularity (M1, M5, M15, H1, H4, D), with optional
      bid/mid/ask prices. Returns a list of `Candle` dataclasses.

    * `OandaInstruments.latest_completed(...)` — convenience wrapper
      that returns *only* the most recent COMPLETED candle (excludes
      the in-progress one), with deduplication on time so a poll loop
      can call this every second without producing duplicates.

    * `OandaInstruments.current_price(...)` — bid/ask/mid for the
      pair, used by GateMind for spread checks at order time.

We deliberately do not stream pricing here. OANDA offers a streaming
API but it is overkill for an M15 system: a polling client that asks
"any new candle since X?" every 5–15 seconds is robust, cheap, and
matches the bar-clock the rest of the system already runs on.

Design notes
------------
    * Time discipline: every Candle carries a UTC datetime (parsed
      from OANDA's RFC3339 strings). Internal code never touches
      timezones again.
    * Pricing: by default we request `M` (mid) prices. ChartMind's
      analysis is based on mid; spread comes separately from
      `current_price`.
    * Pair format: external code uses "EUR/USD" (with slash) but
      OANDA expects "EUR_USD" (with underscore). We translate at the
      boundary so callers never deal with the OANDA-specific form.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from .client import OandaClient, OandaResponse


# ----------------------------------------------------------------------
# Output type.
# ----------------------------------------------------------------------
@dataclass
class Candle:
    """One OHLC candle from OANDA's v20 candles endpoint.

    Pricing fields hold mid prices by default; if `price='B'` or 'A'
    was requested, the bid/ask are filled instead and the mid fields
    are zero.
    """
    time: datetime               # UTC, RFC3339 parsed
    open: float
    high: float
    low: float
    close: float
    volume: int                  # tick volume (not size-weighted)
    complete: bool               # True if the candle is closed
    granularity: str             # e.g. "M15"

    @property
    def hl2(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def ohlc4(self) -> float:
        return (self.open + self.high + self.low + self.close) / 4.0

    def to_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "open": self.open, "high": self.high,
            "low": self.low, "close": self.close,
            "volume": self.volume, "complete": self.complete,
            "granularity": self.granularity,
        }


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def to_oanda_pair(pair: str) -> str:
    """Translate 'EUR/USD' → 'EUR_USD'. Idempotent for already-OANDA form."""
    return pair.replace("/", "_").replace("-", "_").upper()


def from_oanda_pair(pair: str) -> str:
    """Translate 'EUR_USD' → 'EUR/USD'. Used when surfacing names back."""
    return pair.replace("_", "/")


# ----------------------------------------------------------------------
# Public class.
# ----------------------------------------------------------------------
class OandaInstruments:
    """Convenience wrapper around the v20 instruments + pricing endpoints.

    Construct with an OandaClient; share the client across instruments
    + broker + account modules so HTTP connection pooling is reused.
    """

    def __init__(self, client: OandaClient):
        self._c = client

    # ==================================================================
    # Candles.
    # ==================================================================
    def candles(self, *,
                pair: str,
                granularity: str = "M15",
                count: Optional[int] = 100,
                from_time: Optional[datetime] = None,
                to_time: Optional[datetime] = None,
                price: str = "M",
                ) -> list[Candle]:
        """Fetch OHLC candles for `pair`.

        One of (`count`) or (`from_time`, `to_time`) drives the window.
        OANDA's API accepts `count` (most recent N closed candles) OR
        a time range. When both are given the time range wins; we keep
        `count` as a small default (100) so a fresh caller gets a
        useful default page.

        Returns an empty list on error (errors surfaced via the client
        but not raised — the live loop should keep going).
        """
        path = f"/v3/instruments/{to_oanda_pair(pair)}/candles"
        params: dict[str, Any] = {
            "granularity": granularity,
            "price": price,
        }
        if from_time is not None and to_time is not None:
            params["from"] = _to_rfc3339(from_time)
            params["to"] = _to_rfc3339(to_time)
        else:
            params["count"] = int(count) if count is not None else 100

        resp = self._c.get(path, params=params)
        return _parse_candles(resp, granularity=granularity, price=price)

    def latest_completed(self, *,
                         pair: str,
                         granularity: str = "M15",
                         since: Optional[datetime] = None,
                         ) -> Optional[Candle]:
        """Return the most-recent COMPLETED candle, optionally only if
        it is newer than `since`.

        OANDA's response usually includes the in-progress candle as
        `complete=False`; we filter it out so the live loop never
        sees a half-formed bar.

        `since` is the dedup gate: pass the time of the last candle
        you saw, and this returns None if the latest closed candle is
        the same. Useful for the polling loop in OandaBarFeed.
        """
        # Ask for the last 2 candles so we always have at least one
        # completed even when the in-progress one is fresh.
        cs = self.candles(pair=pair, granularity=granularity, count=2)
        completed = [c for c in cs if c.complete]
        if not completed:
            return None
        last = completed[-1]
        if since is not None and last.time <= since:
            return None
        return last

    def candles_since(self, *,
                      pair: str,
                      granularity: str = "M15",
                      since: datetime,
                      max_count: int = 500,
                      ) -> list[Candle]:
        """Fetch all completed candles strictly newer than `since`.

        Used to backfill after a process restart: start with the
        last journaled bar's time, then ask for everything newer.
        """
        cs = self.candles(
            pair=pair, granularity=granularity,
            from_time=since, to_time=datetime.now(timezone.utc),
        )
        out = [c for c in cs if c.complete and c.time > since]
        return out[:max_count]

    # ==================================================================
    # Pricing (bid / ask / spread).
    # ==================================================================
    def current_price(self, *, pair: str) -> Optional[dict]:
        """Return {bid, ask, mid, spread_pips, time} for `pair`.

        Used by GateMind's pre-trade check ("is the spread acceptable
        to take this trade?") and by SmartNoteBook to record
        spread_pips_at_entry.

        Returns None on error.
        """
        path = self._c.account_path(
            f"/pricing?instruments={to_oanda_pair(pair)}"
        )
        resp = self._c.get(path)
        if not resp.ok or not isinstance(resp.data, dict):
            return None
        prices = resp.data.get("prices", [])
        if not prices:
            return None
        p = prices[0]
        try:
            bids = p.get("bids", [{}])
            asks = p.get("asks", [{}])
            bid = float(bids[0].get("price", 0.0))
            ask = float(asks[0].get("price", 0.0))
            if bid <= 0 or ask <= 0:
                return None
            mid = (bid + ask) / 2.0
            spread_pips = (ask - bid) / 0.0001  # EUR/USD pip
            t = p.get("time", "")
            ts = _parse_time(t) if t else datetime.now(timezone.utc)
            return {
                "bid": bid, "ask": ask, "mid": mid,
                "spread_pips": spread_pips,
                "time": ts,
            }
        except (ValueError, KeyError, IndexError, TypeError):
            return None


# ----------------------------------------------------------------------
# Parsing helpers.
# ----------------------------------------------------------------------
def _parse_candles(resp: OandaResponse, *,
                   granularity: str, price: str) -> list[Candle]:
    if not resp.ok or not isinstance(resp.data, dict):
        return []
    raw = resp.data.get("candles", [])
    out: list[Candle] = []
    for c in raw:
        try:
            t = _parse_time(c.get("time", ""))
            if t is None:
                continue
            mid_block = c.get("mid") or c.get("bid") or c.get("ask") or {}
            o = float(mid_block.get("o", 0.0))
            h = float(mid_block.get("h", 0.0))
            lo = float(mid_block.get("l", 0.0))
            cl = float(mid_block.get("c", 0.0))
            vol = int(c.get("volume", 0) or 0)
            complete = bool(c.get("complete", False))
            out.append(Candle(
                time=t, open=o, high=h, low=lo, close=cl,
                volume=vol, complete=complete,
                granularity=granularity,
            ))
        except (TypeError, ValueError):
            continue
    return out


def _parse_time(s: str) -> Optional[datetime]:
    """Parse OANDA RFC3339 timestamp into a tz-aware UTC datetime.

    Handles both '2026-04-25T12:00:00.000000000Z' and
    '2026-04-25T12:00:00Z' forms.
    """
    if not s:
        return None
    s = s.strip()
    # Truncate fractional seconds beyond microseconds.
    if "." in s:
        head, _, rest = s.partition(".")
        # Keep up to 6 digits of fractional seconds; trim 'Z' afterwards.
        frac = ""
        for ch in rest:
            if ch.isdigit() and len(frac) < 6:
                frac += ch
            else:
                break
        # Re-attach the trailing 'Z' or offset
        suffix = ""
        for ch in rest:
            if not ch.isdigit():
                suffix = rest[rest.index(ch):]
                break
        s = f"{head}.{frac}{suffix or 'Z'}"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_rfc3339(dt: datetime) -> str:
    """Format a datetime as RFC3339 UTC for the OANDA API."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
