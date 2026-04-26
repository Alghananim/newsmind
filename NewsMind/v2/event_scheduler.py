# -*- coding: utf-8 -*-
"""EventScheduler — pre-known economic event windows.

Wraps the existing Backtest.calendar.HistoricalCalendar (which knows
NFP/CPI/FOMC/ECB/BoJ/BoE/UK CPI dates) and adds:
   * pre/during/post window state per UTC moment
   * "minutes until next major event" for the engine to pre-empt
   * actual vs forecast tracking (set externally when event fires)
   * currency → pair mapping

The scheduler is the AUTHORITATIVE source for "is now a news blackout?"
in production. NewsMind v2 consults it on every cycle.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from .models import EventSchedule, ImpactLevel


CURRENCY_TO_PAIRS = {
    "USD": ("EUR/USD", "USD/JPY", "GBP/USD"),
    "EUR": ("EUR/USD",),
    "JPY": ("USD/JPY",),
    "GBP": ("GBP/USD",),
}

# Mapping from HistoricalCalendar event names -> (impact, currency)
EVENT_META = {
    "NFP":                    ("high",   ("USD",)),
    "CPI (US)":               ("high",   ("USD",)),
    "PPI (US)":               ("medium", ("USD",)),
    "Jobless Claims":         ("low",    ("USD",)),
    "Retail Sales (US)":      ("medium", ("USD",)),
    "GDP (US, advance)":      ("high",   ("USD",)),
    "FOMC Rate Decision":     ("high",   ("USD",)),
    "ECB Rate Decision":      ("high",   ("EUR",)),
    "BoJ Rate Decision":      ("high",   ("JPY",)),
    "BoE Rate Decision":      ("high",   ("GBP",)),
    "UK CPI Release":         ("high",   ("GBP",)),
}


class EventScheduler:
    """Per-pair scheduler. Construct once, query per cycle."""

    def __init__(self, calendar=None,
                 pre_window_min: int = 30,
                 during_window_min: int = 5,
                 post_window_min: int = 60):
        self.calendar = calendar
        self.pre_window_min = pre_window_min
        self.during_window_min = during_window_min
        self.post_window_min = post_window_min
        # Cache the resolved EventSchedule list lazily.
        self._events_cache: Optional[list[EventSchedule]] = None

    def _ensure_loaded(self) -> list[EventSchedule]:
        if self._events_cache is not None:
            return self._events_cache
        if self.calendar is None:
            self._events_cache = []
            return self._events_cache
        try:
            raw_events = self.calendar.events()
        except Exception:
            self._events_cache = []
            return self._events_cache
        out = []
        for e in raw_events:
            impact, ccys = EVENT_META.get(e.name, ("medium", ()))
            pairs = tuple(p for ccy in ccys for p in CURRENCY_TO_PAIRS.get(ccy, ()))
            out.append(EventSchedule(
                event_id=f"{e.name}_{e.when_utc.isoformat()}",
                name=e.name,
                when_utc=e.when_utc,
                impact_level=impact,
                affected_currencies=ccys,
                affected_pairs=pairs,
                source=getattr(e, "source", "calendar"),
                pre_window_min=self.pre_window_min,
                during_window_min=self.during_window_min,
                post_window_min=self.post_window_min,
            ))
        self._events_cache = out
        return out

    # ------------------------------------------------------------------
    # Public queries.
    # ------------------------------------------------------------------
    def windows_for(self, *, now_utc: datetime, pair: str
                    ) -> tuple[bool, bool, bool, Optional[EventSchedule]]:
        """Return (in_pre, in_during, in_post, nearest_event) for `pair` at `now_utc`."""
        events = self._ensure_loaded()
        if not events:
            return False, False, False, None

        nearest: Optional[EventSchedule] = None
        nearest_dist_sec = float("inf")
        in_pre = in_during = in_post = False

        for e in events:
            if e.affected_pairs and pair not in e.affected_pairs:
                continue
            dist_sec = (e.when_utc - now_utc).total_seconds()

            # In any of the three windows?
            if -e.during_window_min*60 <= dist_sec <= 0:
                in_during = True
                nearest = e
                nearest_dist_sec = 0
                continue
            if 0 < dist_sec <= e.pre_window_min * 60:
                in_pre = True
                if dist_sec < nearest_dist_sec:
                    nearest = e
                    nearest_dist_sec = dist_sec
                continue
            if -e.post_window_min*60 <= dist_sec < -e.during_window_min*60:
                in_post = True
                if abs(dist_sec) < nearest_dist_sec:
                    nearest = e
                    nearest_dist_sec = abs(dist_sec)
                continue

            # Track absolute nearest for "minutes_to_next_event"
            if abs(dist_sec) < nearest_dist_sec:
                nearest = e
                nearest_dist_sec = abs(dist_sec)

        return in_pre, in_during, in_post, nearest

    def is_blackout(self, *, now_utc: datetime, pair: str,
                    impact: ImpactLevel = "high") -> tuple[bool, Optional[EventSchedule]]:
        """High-level: is `pair` currently in ANY blocking window
        for an event of at least `impact` level?
        """
        in_pre, in_during, in_post, ev = self.windows_for(
            now_utc=now_utc, pair=pair)
        if not (in_pre or in_during or in_post):
            return False, None
        if ev is None:
            return False, None
        # Order severity
        order = {"low": 0, "medium": 1, "high": 2, "unknown": 1}
        if order.get(ev.impact_level, 1) >= order.get(impact, 2):
            return True, ev
        return False, ev

    def minutes_to_next_event(self, *, now_utc: datetime, pair: str
                              ) -> tuple[Optional[float], Optional[EventSchedule]]:
        """Return (minutes_until_next_event, event)."""
        events = self._ensure_loaded()
        future = [e for e in events
                  if e.when_utc > now_utc
                  and (not e.affected_pairs or pair in e.affected_pairs)]
        if not future:
            return None, None
        nearest = min(future, key=lambda e: e.when_utc)
        delta_min = (nearest.when_utc - now_utc).total_seconds() / 60.0
        return delta_min, nearest
