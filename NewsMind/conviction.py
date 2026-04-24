# -*- coding: utf-8 -*-
"""Conviction aggregator (Chandler Three-of-Four)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
from NewsMind.channel_router import ChannelImpact


@dataclass
class COTSnapshot:
    report_date_utc: datetime
    net_spec_z: float
    net_spec_sign: int
    pair: str = "EUR_USD"

    def is_stale(self, now_utc: datetime, max_age_days: int = 10) -> bool:
        return (now_utc - self.report_date_utc) > timedelta(days=max_age_days)


def compute_conviction(channels: ChannelImpact,
                         cot: Optional[COTSnapshot] = None,
                         now_utc: Optional[datetime] = None) -> str:
    aligned = channels.aligned_count()
    ceiling = "high"
    if cot is not None and now_utc is not None and cot.is_stale(now_utc):
        ceiling = "medium"
    if abs(channels.net()) < 0.05:
        return "low"
    effective = aligned
    if cot is not None:
        net_sign = 1.0 if channels.net() > 0 else -1.0
        if cot.net_spec_sign != 0 and (cot.net_spec_sign * net_sign) > 0:
            effective += 1
    if effective >= 3:
        out = "high"
    elif effective == 2:
        out = "medium"
    else:
        out = "low"
    if ceiling == "medium" and out == "high":
        out = "medium"
    return out
