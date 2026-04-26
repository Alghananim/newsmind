# -*- coding: utf-8 -*-
"""NY trading session enforcement: 03:00-05:00 + 08:00-12:00 NY local."""
from __future__ import annotations
from datetime import datetime, timezone


# NY local time windows (24h)
NY_WINDOWS_LOCAL = [(3, 5), (8, 12)]


def check(now_utc: datetime) -> dict:
    if now_utc is None:
        return {"status": "outside", "details": "no_timestamp"}
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    # Try zoneinfo for proper DST handling
    try:
        from zoneinfo import ZoneInfo
        ny = now_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        # Conservative fallback: be safe — return dst_unknown so GateMind blocks
        return {"status": "dst_unknown",
                "details": "zoneinfo_unavailable_fallback_to_block"}

    h = ny.hour
    minute = ny.minute
    for start, end in NY_WINDOWS_LOCAL:
        if start <= h < end:
            return {"status": "in_window",
                    "details": f"ny_time={ny.strftime('%H:%M')} window={start}-{end}"}
    return {"status": "outside",
            "details": f"ny_time={ny.strftime('%H:%M')} not_in_{NY_WINDOWS_LOCAL}"}
