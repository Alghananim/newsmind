# -*- coding: utf-8 -*-
"""OandaBarFeed — polling bar producer for the live loop.

Engine.step() expects a `bar` argument that ChartMind can analyse.
This module is the bridge between OANDA's candles endpoint and that
expectation: it polls every cycle, returns the latest *closed* M15
candle if there is a fresh one, and translates it into a `Bar` shape
ChartMind already accepts (a pandas-Series-like duck-typed object).

Why polling, not streaming
--------------------------
For an M15 system the longest acceptable bar arrival lag is ~5–10s.
Polling once a second beats that comfortably and is far simpler than
maintaining a streaming socket; for a system that already has a 60s
poll loop in main.py, polling fits the existing rhythm.

Deduplication
-------------
The feed remembers the last bar's `time` it returned. Subsequent
calls only return a new bar if the latest closed candle is strictly
newer. The live loop can therefore call `latest_new_bar()` every
cycle without producing duplicates.

`Bar` duck-type
---------------
ChartMind.analyze() expects an object with attribute access on
`open`, `high`, `low`, `close` and indexable by the same names. We
provide both via dataclass + `__getitem__`. ChartMind uses pandas
Series in production but our Bar is interchangeable for the fields
ChartMind reads.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .client import OandaClient
from .instruments import Candle, OandaInstruments


# ----------------------------------------------------------------------
# ChartMind-compatible bar shape.
# ----------------------------------------------------------------------
@dataclass
class Bar:
    """Minimal duck-typed bar object accepted by ChartMind.analyze().

    Fields:
        time:        UTC datetime of the bar (close time of the candle)
        open / high / low / close:  the four prices (mid by default)
        volume:      tick volume from OANDA
        granularity: e.g. "M15"
        pair:        e.g. "EUR/USD"
    """
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    granularity: str
    pair: str

    # pandas-like access — ChartMind sometimes does bar['close'] too
    def __getitem__(self, key):
        return getattr(self, key)

    def __contains__(self, key):
        return hasattr(self, key)

    def to_dict(self) -> dict:
        return {
            "time": self.time.isoformat(),
            "open": self.open, "high": self.high,
            "low": self.low, "close": self.close,
            "volume": self.volume,
            "granularity": self.granularity, "pair": self.pair,
        }


# ----------------------------------------------------------------------
# The feed.
# ----------------------------------------------------------------------
class OandaBarFeed:
    """Stateful poller that yields fresh closed bars one at a time.

    Construct once at process start (shares the client). On each tick
    of the live loop, call `latest_new_bar()`; it returns a `Bar` if a
    new closed candle has appeared since the last call, else None.

    A separate `backfill_since(...)` lets the system catch up after a
    restart by replaying every closed bar between the journal's last
    bar time and now.
    """

    def __init__(self, *,
                 client: OandaClient,
                 pair: str = "EUR/USD",
                 granularity: str = "M15"):
        self._instruments = OandaInstruments(client)
        self._pair = pair
        self._granularity = granularity
        self._last_bar_time: Optional[datetime] = None

    # ==================================================================
    # Public properties.
    # ==================================================================
    @property
    def pair(self) -> str:
        return self._pair

    @property
    def granularity(self) -> str:
        return self._granularity

    @property
    def last_bar_time(self) -> Optional[datetime]:
        """The time of the last bar this feed returned (UTC)."""
        return self._last_bar_time

    # ==================================================================
    # Public methods.
    # ==================================================================
    def latest_new_bar(self) -> Optional[Bar]:
        """Return a fresh closed bar, or None if nothing new since
        the last call.

        The first call after construction returns the most recent
        closed bar (so the system has something to chew on
        immediately); subsequent calls only return strictly newer
        bars.
        """
        candle = self._instruments.latest_completed(
            pair=self._pair,
            granularity=self._granularity,
            since=self._last_bar_time,
        )
        if candle is None:
            return None
        bar = self._candle_to_bar(candle)
        self._last_bar_time = candle.time
        return bar

    def backfill_since(self, since: datetime,
                       max_count: int = 500) -> list[Bar]:
        """Return every closed bar strictly newer than `since`.

        Used at process boot when the journal's last bar is older
        than `now - granularity`. Keeps SmartNoteBook continuous
        across restarts.
        """
        candles = self._instruments.candles_since(
            pair=self._pair,
            granularity=self._granularity,
            since=since,
            max_count=max_count,
        )
        out = [self._candle_to_bar(c) for c in candles]
        if out:
            self._last_bar_time = out[-1].time
        return out

    def reset(self) -> None:
        """Forget the last-bar-time gate; next call to latest_new_bar
        will return whatever is most recent. Use sparingly — usually
        only in tests.
        """
        self._last_bar_time = None

    def current_price(self) -> Optional[dict]:
        """Convenience passthrough to OandaInstruments.current_price."""
        return self._instruments.current_price(pair=self._pair)

    # ==================================================================
    # Internals.
    # ==================================================================
    def _candle_to_bar(self, c: Candle) -> Bar:
        return Bar(
            time=c.time,
            open=c.open, high=c.high, low=c.low, close=c.close,
            volume=c.volume,
            granularity=c.granularity,
            pair=self._pair,
        )
