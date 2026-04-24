# -*- coding: utf-8 -*-
"""Headline Scanner - Tier-X detection + black-swan speed detector."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple

from NewsMind.config_loader import EventsConfig, KeywordsConfig
from NewsMind.event_classifier import EventRecord, classify_unscheduled
from NewsMind.news_data import RawItem


@dataclass
class KeywordConfig:
    categories: dict
    exclude: dict


@dataclass
class _ActiveAlert:
    event_id: str
    first_seen_utc: datetime
    last_seen_utc: datetime
    tier: int
    channel: str
    source_ids: List[str] = field(default_factory=list)
    headlines: List[str] = field(default_factory=list)
    default_pip: float = 100.0
    default_direction: str = "usd_bullish_risk_off"
    expiry_utc: Optional[datetime] = None


class HeadlineScanner:
    _DECAY_TIER1_MIN = 120
    _DECAY_TIER2_MIN = 60
    _DECAY_TIER3_MIN = 20
    _BLACK_SWAN_WINDOW_MIN = 5

    def __init__(self, events_config: EventsConfig,
                  keywords_config: Optional[KeywordsConfig] = None):
        self.events_config = events_config
        self.keywords_config = keywords_config
        self._alerts: dict = {}
        self._recent_tier1: List[Tuple[datetime, str]] = []

    def ingest(self, items: List[RawItem]) -> List[EventRecord]:
        newly: List[EventRecord] = []
        for item in items:
            rec = classify_unscheduled(item, self.events_config,
                                        self.keywords_config)
            if rec is None:
                continue
            ts = item.fetched_utc or item.published_utc or datetime.now(timezone.utc)
            alert = self._alerts.get(rec.event_id)
            if alert is None:
                udef = self.events_config.unscheduled_by_id(rec.event_id)
                alert = _ActiveAlert(
                    event_id=rec.event_id,
                    first_seen_utc=ts, last_seen_utc=ts,
                    tier=rec.tier, channel=rec.channel,
                    source_ids=[item.source_id],
                    headlines=[item.title],
                    default_pip=udef.default_pip if udef else 100.0,
                    default_direction=udef.default_direction if udef else "usd_bullish_risk_off",
                    expiry_utc=ts + timedelta(minutes=self._expiry_for_tier(rec.tier)),
                )
                self._alerts[rec.event_id] = alert
                newly.append(rec)
                if rec.tier == 1:
                    self._recent_tier1.append((ts, rec.event_id))
            else:
                alert.last_seen_utc = ts
                if item.source_id not in alert.source_ids:
                    alert.source_ids.append(item.source_id)
                if len(alert.headlines) < 10:
                    alert.headlines.append(item.title)
                udef = self.events_config.unscheduled_by_id(rec.event_id)
                if udef and udef.tier == 1 and alert.tier == 2:
                    if item.source_tier in ("S", "A"):
                        alert.tier = 1
                        self._recent_tier1.append((ts, rec.event_id))
                alert.expiry_utc = ts + timedelta(
                    minutes=self._expiry_for_tier(alert.tier))
        if self._recent_tier1:
            anchor = self._recent_tier1[-1][0]
            cutoff = anchor - timedelta(minutes=15)
            self._recent_tier1 = [(t, eid) for (t, eid) in self._recent_tier1
                                   if t >= cutoff]
        return newly

    def active_alerts(self, now_utc: datetime) -> List[EventRecord]:
        self.clear_expired(now_utc)
        return [self._to_record(a) for a in self._alerts.values()]

    def black_swan_suspected(self, now_utc: datetime) -> bool:
        cutoff = now_utc - timedelta(minutes=self._BLACK_SWAN_WINDOW_MIN)
        recent = [(t, eid) for (t, eid) in self._recent_tier1 if t >= cutoff]
        return len({eid for (_, eid) in recent}) >= 3

    def clear_expired(self, now_utc: datetime) -> None:
        expired = [eid for eid, a in self._alerts.items()
                   if a.expiry_utc is not None and a.expiry_utc < now_utc]
        for eid in expired:
            self._alerts.pop(eid, None)

    def active_count_by_tier(self, now_utc: datetime) -> dict:
        self.clear_expired(now_utc)
        out = {1: 0, 2: 0, 3: 0}
        for a in self._alerts.values():
            out[a.tier] = out.get(a.tier, 0) + 1
        return out

    def _expiry_for_tier(self, tier: int) -> int:
        if tier == 1:
            return self._DECAY_TIER1_MIN
        if tier == 2:
            return self._DECAY_TIER2_MIN
        return self._DECAY_TIER3_MIN

    def _to_record(self, a: _ActiveAlert) -> EventRecord:
        udef = self.events_config.unscheduled_by_id(a.event_id)
        label = udef.label if udef else a.event_id
        return EventRecord(
            event_id=a.event_id, label=label,
            country="", currency="",
            tier=a.tier, channel=a.channel,
            observed_time_utc=a.last_seen_utc,
            direction_rule=a.default_direction,
            is_unscheduled=True,
            headline_source=",".join(a.source_ids),
            source_ids=list(a.source_ids),
            raw_title=a.headlines[0] if a.headlines else "",
        )
