# -*- coding: utf-8 -*-
"""BacktestSession — strict NY-time trading window filter.

What this module does
---------------------
Given a UTC timestamp, answer: is this moment inside a permitted
trading window? The system only trades:

    * Monday through Friday (NY local calendar)
    * 03:00-05:00 NY local time      (London early session)
    * 08:00-12:00 NY local time      (London/NY overlap)

DST handling
------------
NY observes DST: the UTC offset is -5 (EST) in winter and -4 (EDT) in
summer. Crucially, the *NY local* hours stay constant — 03:00 NY is
03:00 NY whether it is 08:00 UTC (EST) or 07:00 UTC (EDT). We use
`zoneinfo.ZoneInfo("America/New_York")` so the conversion is correct
across all DST transitions automatically.

Why these specific windows
--------------------------
EUR/USD has three liquidity peaks in the 24-hour cycle:

    * 03:00-05:00 NY = 08:00-10:00 GMT/BST = London open. Spreads
      tighten dramatically; macro flows hit on European data.
    * 08:00-12:00 NY = London/NY overlap. The single highest-volume
      window of the day. Most US economic releases land at 08:30 NY.
    * 12:00-16:00 NY = NY afternoon. Volume drops, choppy.

The system trades the first two and skips the third — a standard
"trade only when the elephants are moving" doctrine (Linda Raschke,
*Street Smarts*; Schwager interviews).

Reasoning canon
---------------
    * Brett Steenbarger — *The Daily Trading Coach*: "trading the
      wrong session is the silent killer of edge — it does not show
      up as a single losing day, it shows up as a ground-down equity
      curve over months."
    * Andrew Lo — *Adaptive Markets*: liquidity windows are regime
      indicators in their own right. Trading inside them is not a
      preference, it is a risk-management decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Iterable, Optional

# Use stdlib zoneinfo (Python 3.9+); no extra dependency.
try:
    from zoneinfo import ZoneInfo
except ImportError:        # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None        # type: ignore


# ----------------------------------------------------------------------
# The session filter.
# ----------------------------------------------------------------------
@dataclass
class BacktestSession:
    """Strict trading-window filter, configurable at construction.

    Construct once at backtest start; share across the runner +
    monitor. Methods are pure and thread-safe.
    """
    tz_name: str = "America/New_York"
    windows: tuple = (
        ("03:00", "05:00"),
        ("08:00", "12:00"),
    )
    # Days the system trades, as ISO weekday numbers (Monday=1, Sunday=7).
    trading_days: tuple = (1, 2, 3, 4, 5)   # Mon-Fri

    # Internals (computed in __post_init__).
    _tz: object = field(init=False, default=None)
    _parsed_windows: list = field(init=False, default_factory=list)

    def __post_init__(self):
        if ZoneInfo is None:
            raise RuntimeError(
                "zoneinfo is unavailable. Use Python 3.9+; the system "
                "depends on stdlib zoneinfo for correct NY DST handling."
            )
        self._tz = ZoneInfo(self.tz_name)
        self._parsed_windows = [
            (_parse_hhmm(start), _parse_hhmm(end))
            for start, end in self.windows
        ]
        # Sanity: every window must have start < end (no overnight
        # spans — those would need extra logic).
        for s, e in self._parsed_windows:
            if not (s < e):
                raise ValueError(
                    f"window {s}-{e} must have start < end "
                    "(overnight windows are not supported)"
                )

    # ==================================================================
    # Public API.
    # ==================================================================
    def is_trading_now(self, ts_utc: datetime) -> bool:
        """Return True iff `ts_utc` (UTC) falls inside any allowed
        window on a trading day in NY local time.

        Behaviour at boundaries:
            * Window start time: INCLUDED (>=)
            * Window end time:   EXCLUDED (<)
        So a window 03:00-05:00 means [03:00, 05:00) — bars stamped
        exactly 05:00 NY are *outside*.
        """
        local = self._to_local(ts_utc)
        if local.isoweekday() not in self.trading_days:
            return False
        t = local.time()
        for start, end in self._parsed_windows:
            if start <= t < end:
                return True
        return False

    def next_window_start(self, ts_utc: datetime) -> Optional[datetime]:
        """Return the UTC datetime of the next session window opening
        on or after `ts_utc`. None if no window can be found within
        the next 14 days (sanity guard).
        """
        from datetime import timedelta
        cur = ts_utc
        for _ in range(14 * 24 * 4):          # at most 14 days at 15-min granularity
            local = self._to_local(cur)
            if local.isoweekday() in self.trading_days:
                t = local.time()
                for start, end in self._parsed_windows:
                    if t < start:
                        # Same day, before this window
                        target_local = local.replace(
                            hour=start.hour, minute=start.minute,
                            second=0, microsecond=0,
                        )
                        return target_local.astimezone(timezone.utc)
            cur = cur + timedelta(minutes=15)
        return None

    def windows_in_local_form(self) -> list[tuple[str, str]]:
        """Human-readable list, e.g. [('03:00','05:00'), ('08:00','12:00')]."""
        return list(self.windows)

    # ==================================================================
    # Internals.
    # ==================================================================
    def _to_local(self, ts_utc: datetime) -> datetime:
        """Convert a UTC-aware datetime to the configured local zone.

        Naive datetimes are assumed UTC (a defensive default — backtest
        bars from OANDA always come UTC-aware so this is rarely hit).
        """
        if ts_utc.tzinfo is None:
            ts_utc = ts_utc.replace(tzinfo=timezone.utc)
        return ts_utc.astimezone(self._tz)


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _parse_hhmm(s: str) -> time:
    """Parse 'HH:MM' into a `time` object."""
    h, _, m = s.partition(":")
    return time(int(h), int(m))


# ----------------------------------------------------------------------
# Convenience function for callers that don't need a dataclass instance.
# ----------------------------------------------------------------------
def is_in_ny_trading_hours(ts_utc: datetime,
                           windows: Iterable[tuple[str, str]] = (
                               ("03:00", "05:00"),
                               ("08:00", "12:00"),
                           ),
                           trading_days: Iterable[int] = (1, 2, 3, 4, 5),
                           ) -> bool:
    """One-shot check without constructing a BacktestSession.

    Useful in tests and one-off scripts. For the backtest hot path,
    construct a BacktestSession once and reuse it.
    """
    sess = BacktestSession(
        windows=tuple(windows),
        trading_days=tuple(trading_days),
    )
    return sess.is_trading_now(ts_utc)
