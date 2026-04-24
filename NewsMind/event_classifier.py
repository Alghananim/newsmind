# -*- coding: utf-8 -*-
"""Event Classifier - map RawItems to EventRecords (scheduled + unscheduled)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from NewsMind.config_loader import EventsConfig, UnscheduledEventDef, KeywordsConfig
from NewsMind.news_data import RawItem


@dataclass
class EventRecord:
    event_id: str
    label: str
    country: str
    currency: str
    tier: int
    channel: str
    schedule_time_utc: Optional[datetime] = None
    observed_time_utc: Optional[datetime] = None
    actual: Optional[float] = None
    consensus: Optional[float] = None
    consensus_stdev: Optional[float] = None
    previous: Optional[float] = None
    surprise_z: Optional[float] = None
    direction_rule: str = ""
    is_unscheduled: bool = False
    headline_source: Optional[str] = None
    text_tone: Optional[str] = None
    source_ids: List[str] = field(default_factory=list)
    raw_title: str = ""

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id, "label": self.label,
            "country": self.country, "currency": self.currency,
            "tier": self.tier, "channel": self.channel,
            "schedule_time_utc": (self.schedule_time_utc.isoformat()
                                    if self.schedule_time_utc else None),
            "observed_time_utc": (self.observed_time_utc.isoformat()
                                    if self.observed_time_utc else None),
            "actual": self.actual, "consensus": self.consensus,
            "consensus_stdev": self.consensus_stdev, "previous": self.previous,
            "surprise_z": self.surprise_z,
            "direction_rule": self.direction_rule,
            "is_unscheduled": self.is_unscheduled,
            "headline_source": self.headline_source,
            "text_tone": self.text_tone,
            "source_ids": list(self.source_ids),
            "raw_title": self.raw_title,
        }


def classify_scheduled(item: RawItem, calendar) -> Optional[EventRecord]:
    from NewsMind.event_calendar import _parse_number
    title = item.title.strip()
    country = ""
    actual_val = consensus_val = previous_val = None
    if item.raw_payload and isinstance(item.raw_payload, dict):
        country = str(item.raw_payload.get("country", "") or "")
        actual_val = _parse_number(item.raw_payload.get("actual"))
        consensus_val = _parse_number(item.raw_payload.get("forecast"))
        previous_val = _parse_number(item.raw_payload.get("previous"))
    defn = calendar.match_definition(title, country)
    if defn is None and ":" in title:
        c, _, rest = title.partition(":")
        defn = calendar.match_definition(rest.strip(), c.strip())
    if defn is None:
        return None
    return EventRecord(
        event_id=defn.id, label=defn.label,
        country=defn.country, currency=defn.currency,
        tier=defn.tier, channel=defn.channel,
        schedule_time_utc=item.published_utc,
        observed_time_utc=item.fetched_utc or item.published_utc,
        actual=actual_val, consensus=consensus_val, previous=previous_val,
        direction_rule=defn.direction_rule,
        is_unscheduled=False,
        headline_source=item.source_id,
        source_ids=[item.source_id], raw_title=title,
    )


def classify_unscheduled(item: RawItem,
                           events_config: EventsConfig,
                           keywords: Optional[KeywordsConfig] = None
                           ) -> Optional[EventRecord]:
    text = f"{item.title.lower()} {(item.body or '').lower()}"
    best: Optional[UnscheduledEventDef] = None
    best_score = 0
    for udef in events_config.unscheduled:
        score = _score_keywords(text, udef.keywords, udef.exclude_keywords)
        for group in udef.and_keywords:
            if not group:
                continue
            if all(tok.lower() in text for tok in group):
                if udef.exclude_keywords and any(
                    ex.lower() in text for ex in udef.exclude_keywords
                ):
                    continue
                score += 1
        if score > best_score:
            best = udef
            best_score = score
    if best is None or best_score < 1:
        return None
    tier = best.tier
    if best.tier == 1 and item.source_tier not in ("S", "A"):
        tier = 2
    return EventRecord(
        event_id=best.id, label=best.label,
        country="", currency="",
        tier=tier, channel=best.channel,
        observed_time_utc=item.fetched_utc or item.published_utc,
        direction_rule=best.default_direction,
        is_unscheduled=True,
        headline_source=item.source_id,
        source_ids=[item.source_id], raw_title=item.title,
    )


def classify_raw_item(item: RawItem, calendar,
                        events_config: EventsConfig,
                        keywords: Optional[KeywordsConfig] = None
                        ) -> Optional[EventRecord]:
    rec = classify_scheduled(item, calendar)
    if rec is not None:
        return rec
    return classify_unscheduled(item, events_config, keywords)


def _score_keywords(text: str, keywords: List[str],
                     exclude: Optional[List[str]] = None) -> int:
    if exclude:
        for ex in exclude:
            if ex.lower() in text:
                return 0
    return sum(1 for kw in keywords if kw.lower() in text)
