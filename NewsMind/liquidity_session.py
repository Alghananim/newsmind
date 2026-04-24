# -*- coding: utf-8 -*-
"""Liquidity Session classifier (BIS doctrine)."""
from __future__ import annotations
from datetime import datetime, timezone


_SESSION_DISCOUNTS = {
    "london_ny":     1.00,
    "london_proper": 0.90,
    "tokyo_london":  0.85,
    "ny_pm":         0.70,
    "asia_only":     0.60,
    "weekend_accum": 0.50,
}


def session_from_utc(ts: datetime) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    wd = ts.weekday()
    hour = ts.hour
    if wd == 5:
        return "weekend_accum"
    if wd == 4 and hour >= 21:
        return "weekend_accum"
    if wd == 6 and hour < 21:
        return "weekend_accum"
    if 13 <= hour < 17:
        return "london_ny"
    if 9 <= hour < 13:
        return "london_proper"
    if 6 <= hour < 9:
        return "tokyo_london"
    if 17 <= hour < 21:
        return "ny_pm"
    return "asia_only"


def liquidity_discount(session: str) -> float:
    return _SESSION_DISCOUNTS.get(session, 0.75)
