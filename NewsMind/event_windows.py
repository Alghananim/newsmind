# -*- coding: utf-8 -*-
"""Event Windows - halt timers + widen-stops multiplier (Carver doctrine)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from NewsMind.event_classifier import EventRecord
from NewsMind.event_calendar import ScheduledEvent


@dataclass
class EventWindowState:
    event_id: Optional[str]
    in_pre_window: bool
    in_post_window: bool
    t_to_event_min: Optional[float]
    t_since_event_min: Optional[float]
    trading_halted: bool
    widen_stops_multiplier: float
    window_reason: str


def compute_window_state(prev_event: Optional[ScheduledEvent],
                           next_event: Optional[ScheduledEvent],
                           active_unscheduled: List[EventRecord],
                           now_utc: datetime) -> EventWindowState:
    now = _to_utc(now_utc)
    candidates: List[EventWindowState] = []
    if next_event is not None:
        candidates.append(_upcoming(next_event, now))
    if prev_event is not None:
        candidates.append(_past(prev_event, now))
    for e in active_unscheduled:
        candidates.append(_unscheduled(e, now))
    actives = [c for c in candidates if c.trading_halted]
    if actives:
        return max(actives, key=lambda c: c.widen_stops_multiplier)
    non_halt = [c for c in candidates if c is not None]
    if non_halt:
        future = [c for c in non_halt if c.t_to_event_min is not None]
        if future:
            return min(future, key=lambda c: c.t_to_event_min)
        return non_halt[0]
    return EventWindowState(
        event_id=None, in_pre_window=False, in_post_window=False,
        t_to_event_min=None, t_since_event_min=None,
        trading_halted=False, widen_stops_multiplier=1.0,
        window_reason="no active event",
    )


def _upcoming(ev: ScheduledEvent, now_utc: datetime) -> EventWindowState:
    t = (ev.schedule_time_utc - now_utc).total_seconds() / 60.0
    in_pre = 0 <= t <= ev.definition.halt_minus_min
    if in_pre:
        return EventWindowState(
            event_id=ev.event_id, in_pre_window=True, in_post_window=False,
            t_to_event_min=t, t_since_event_min=None,
            trading_halted=True,
            widen_stops_multiplier=ev.definition.widen_stops_multiplier,
            window_reason=f"pre-event halt for {ev.event_id} (T-{t:.1f} min)",
        )
    return EventWindowState(
        event_id=ev.event_id, in_pre_window=False, in_post_window=False,
        t_to_event_min=t, t_since_event_min=None,
        trading_halted=False, widen_stops_multiplier=1.0,
        window_reason=f"upcoming {ev.event_id} in {t:.0f} min",
    )


def _past(ev: ScheduledEvent, now_utc: datetime) -> EventWindowState:
    t = (now_utc - ev.schedule_time_utc).total_seconds() / 60.0
    in_post = 0 <= t <= ev.definition.halt_plus_min
    if in_post:
        return EventWindowState(
            event_id=ev.event_id, in_pre_window=False, in_post_window=True,
            t_to_event_min=None, t_since_event_min=t,
            trading_halted=True,
            widen_stops_multiplier=ev.definition.widen_stops_multiplier,
            window_reason=f"post-event halt for {ev.event_id} (T+{t:.1f} min)",
        )
    return EventWindowState(
        event_id=ev.event_id, in_pre_window=False, in_post_window=False,
        t_to_event_min=None, t_since_event_min=t,
        trading_halted=False, widen_stops_multiplier=1.0,
        window_reason=f"past {ev.event_id} {t:.0f} min ago",
    )


def _unscheduled(rec: EventRecord, now_utc: datetime) -> EventWindowState:
    if rec.tier >= 3:
        return EventWindowState(
            event_id=rec.event_id, in_pre_window=False, in_post_window=False,
            t_to_event_min=None, t_since_event_min=None,
            trading_halted=False, widen_stops_multiplier=1.0,
            window_reason=f"unscheduled {rec.event_id} (tier 3)",
        )
    observed = _to_utc(rec.observed_time_utc or now_utc)
    t = (now_utc - observed).total_seconds() / 60.0
    halt_minutes = 60 if rec.tier == 1 else 20
    widen = 2.0 if rec.tier == 1 else 1.5
    halted = 0 <= t <= halt_minutes
    return EventWindowState(
        event_id=rec.event_id, in_pre_window=False,
        in_post_window=halted, t_to_event_min=None, t_since_event_min=t,
        trading_halted=halted,
        widen_stops_multiplier=widen if halted else 1.0,
        window_reason=(f"unscheduled tier-{rec.tier} alert {rec.event_id} "
                       f"active {t:.1f} min"),
    )


def _to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
