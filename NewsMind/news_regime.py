# -*- coding: utf-8 -*-
"""News Regime - quiet/busy/crisis classifier."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional


@dataclass
class NewsRegimeState:
    regime: str
    event_density_24h: int
    tier1_count_24h: int
    unscheduled_alerts_active: int
    black_swan_suspected: bool
    elevated_volatility_until: Optional[datetime]

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "event_density_24h": self.event_density_24h,
            "tier1_count_24h": self.tier1_count_24h,
            "unscheduled_alerts_active": self.unscheduled_alerts_active,
            "black_swan_suspected": self.black_swan_suspected,
            "elevated_volatility_until": (self.elevated_volatility_until.isoformat()
                                           if self.elevated_volatility_until else None),
        }


def classify_regime(calendar, scanner, now_utc: datetime) -> NewsRegimeState:
    now = _to_utc(now_utc)
    past = now - timedelta(hours=24)
    future = now + timedelta(hours=24)
    scheduled_24h = calendar.events_in_window(past, future)
    tier1_24h = [s for s in scheduled_24h if s.tier == 1]
    tier12_24h = [s for s in scheduled_24h if s.tier <= 2]
    actives = scanner.active_alerts(now)
    tier1_unsch = [a for a in actives if a.tier == 1]
    black_swan = scanner.black_swan_suspected(now)
    tier1_total = len(tier1_24h) + len(tier1_unsch)
    event_density = len(tier12_24h) + len(actives)
    if black_swan or tier1_total >= 3:
        regime = "crisis"
    elif event_density >= 5 or (tier1_total >= 1 and len(actives) >= 1):
        regime = "busy"
    else:
        regime = "quiet"
    if regime == "crisis":
        until = now + timedelta(hours=72)
    elif regime == "busy":
        until = now + timedelta(hours=24)
    else:
        until = None
    return NewsRegimeState(
        regime=regime, event_density_24h=event_density,
        tier1_count_24h=tier1_total,
        unscheduled_alerts_active=len(actives),
        black_swan_suspected=black_swan,
        elevated_volatility_until=until,
    )


def _to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
