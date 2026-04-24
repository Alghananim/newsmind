# -*- coding: utf-8 -*-
"""Event Calendar. Loads scheduled events + live times from FF calendar."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from NewsMind.config_loader import EventsConfig, EventDef, load_events_config


@dataclass
class ScheduledEvent:
    definition: EventDef
    schedule_time_utc: datetime
    consensus: Optional[float] = None
    previous: Optional[float] = None
    actual: Optional[float] = None
    consensus_stdev: Optional[float] = None
    live_impact: Optional[str] = None

    @property
    def event_id(self) -> str:
        return self.definition.id

    @property
    def tier(self) -> int:
        return self.definition.tier


class EventCalendar:
    def __init__(self, events_config: EventsConfig):
        self.events_config = events_config
        self._scheduled: List[ScheduledEvent] = []

    @classmethod
    def from_yaml(cls, path: Path) -> "EventCalendar":
        return cls(load_events_config(path))

    def clear_scheduled(self) -> None:
        self._scheduled = []

    def add_live_entry(self, entry: dict) -> Optional[ScheduledEvent]:
        title = str(entry.get("title") or entry.get("event") or "").strip()
        country = str(entry.get("country") or "").strip()
        if not title:
            return None
        defn = self._match_definition(title, country)
        if defn is None:
            return None
        ts = _parse_datetime(str(entry.get("date") or ""))
        if ts is None:
            return None
        se = ScheduledEvent(
            definition=defn, schedule_time_utc=ts,
            consensus=_parse_number(entry.get("forecast")),
            previous=_parse_number(entry.get("previous")),
            actual=_parse_number(entry.get("actual")),
            live_impact=entry.get("impact"),
        )
        self._scheduled.append(se)
        return se

    def bulk_load_live(self, entries: List[dict]) -> int:
        if not entries and self._scheduled:
            return 0
        new: List[ScheduledEvent] = []
        n = 0
        for e in entries:
            if not isinstance(e, dict):
                continue
            saved = list(self._scheduled)
            self._scheduled = new
            try:
                if self.add_live_entry(e) is not None:
                    n += 1
            finally:
                new = self._scheduled
                self._scheduled = saved
        if n == 0 and self._scheduled:
            return 0
        self._scheduled = new
        self._scheduled.sort(key=lambda s: s.schedule_time_utc)
        return n

    def next_event(self, now_utc: datetime,
                    min_tier: int = 1) -> Optional[ScheduledEvent]:
        now = _to_utc(now_utc)
        filtered = [s for s in self._scheduled
                    if s.schedule_time_utc > now and s.tier <= min_tier]
        return filtered[0] if filtered else None

    def prev_event(self, now_utc: datetime,
                    min_tier: int = 1) -> Optional[ScheduledEvent]:
        now = _to_utc(now_utc)
        filtered = [s for s in self._scheduled
                    if s.schedule_time_utc <= now and s.tier <= min_tier]
        return filtered[-1] if filtered else None

    def events_in_window(self, start_utc: datetime,
                           end_utc: datetime) -> List[ScheduledEvent]:
        start = _to_utc(start_utc)
        end = _to_utc(end_utc)
        return [s for s in self._scheduled
                if start <= s.schedule_time_utc <= end]

    def tier_filter(self, events, min_tier: int) -> List[ScheduledEvent]:
        return [e for e in events if e.tier <= min_tier]

    def all_scheduled(self) -> List[ScheduledEvent]:
        return list(self._scheduled)

    def match_definition(self, title: str, country: str) -> Optional[EventDef]:
        return self._match_definition(title, country)

    def _match_definition(self, title: str, country: str) -> Optional[EventDef]:
        lt = title.lower()
        lc_raw = country.upper() if country else ""
        _CCY = {"USD": "US", "EUR": "EU", "GBP": "UK", "JPY": "JP",
                "CHF": "CH", "CAD": "CA", "AUD": "AU", "NZD": "NZ", "CNY": "CN"}
        lc = _CCY.get(lc_raw, lc_raw)
        for d in self.events_config.scheduled:
            if d.label.lower() == lt and (not lc or d.country == lc):
                return d
        keywords = [
            ("non-farm", "us.nfp"),
            ("nonfarm", "us.nfp"),
            ("consumer price", "us.cpi" if lc == "US" else "eu.cpi_flash"),
            ("core pce", "us.core_pce"),
            ("fomc", "us.fomc_decision"),
            ("retail sales", "us.retail_sales" if lc == "US" else "eu.retail_sales"),
            ("ism manufacturing", "us.ism_mfg"),
            ("ism services", "us.ism_svc"),
            ("gdp", "us.gdp_advance" if lc == "US" else "eu.gdp_flash"),
            ("unemployment", "us.jobless_claims" if "claims" in lt else "eu.unemployment"),
            ("initial jobless", "us.jobless_claims"),
            ("ppi", "us.ppi"),
            ("trade balance", "us.trade_balance"),
            ("ecb", "eu.ecb_decision"),
            ("press conference", "us.fomc_press" if lc == "US" else "eu.ecb_press"),
            ("meeting accounts", "eu.ecb_minutes"),
            ("fomc minutes", "us.fomc_minutes"),
            ("ifo", "eu.ifo_de"),
            ("zew", "eu.zew_de"),
            ("pmi manufacturing", "eu.pmi_mfg_flash" if lc != "US" else "us.ism_mfg"),
            ("pmi services", "eu.pmi_svc_flash" if lc != "US" else "us.ism_svc"),
            ("pmi composite", "eu.pmi_comp_flash"),
            ("boe", "uk.boe_decision"),
            ("boj", "jp.boj_decision"),
            ("snb", "ch.snb_decision"),
            ("rba", "au.rba_decision"),
            ("boc", "ca.boc_decision"),
        ]
        for kw, eid in keywords:
            if kw in lt:
                return self.events_config.by_id(eid)
        return None


def _to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _parse_datetime(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _parse_number(v) -> Optional[float]:
    if v is None or v == "" or v == "-":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    mult = 1.0
    if s.endswith("K"):
        mult = 1_000.0
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1_000_000.0
        s = s[:-1]
    elif s.endswith("B"):
        mult = 1_000_000_000.0
        s = s[:-1]
    s = s.rstrip("%").replace(",", "").strip()
    try:
        return float(s) * mult
    except ValueError:
        return None
