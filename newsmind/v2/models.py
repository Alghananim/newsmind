# -*- coding: utf-8 -*-
"""NewsMind v2 — structured data models for news intelligence.

Three core dataclasses that flow through the v2 pipeline:
    NewsItem        — one observed news event from any source
    EventSchedule   — one pre-known scheduled economic event (NFP, FOMC...)
    NewsVerdict     — final permission decision for the trading engine

Every field is JSON-serialisable so SmartNoteBook can journal verdicts
and the operator can audit decisions retroactively.
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Literal


# Permission verdicts
TradePermission = Literal["allow", "wait", "block"]

# Freshness tiers
FreshnessStatus = Literal["fresh", "recent", "stale", "recycled", "unknown"]

# Impact magnitude
ImpactLevel = Literal["high", "medium", "low", "unknown"]

# Source classifications
SourceType = Literal["official", "tier1_wire", "financial_media", "social", "calendar"]


# ----------------------------------------------------------------------
# 1) NewsItem — one ingested news observation.
# ----------------------------------------------------------------------
@dataclass
class NewsItem:
    """One news event as ingested from any source.

    Time fields:
        published_at        — original publish time at the SOURCE
        received_at         — when our system received it
        source_timezone     — the publisher's local timezone (e.g. "Europe/London")
        normalized_utc_time — published_at converted to UTC (single source of truth)
    """
    headline: str
    body: str = ""
    source_name: str = "unknown"
    source_type: SourceType = "calendar"

    # Time tracking
    published_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    source_timezone: str = "UTC"
    normalized_utc_time: Optional[datetime] = None

    # Asset linkage
    affected_currencies: tuple = ()           # ("USD", "EUR")
    affected_pairs: tuple = ()                # ("EUR/USD", "USD/JPY")
    affected_assets: tuple = ()               # ("DXY", "gold", "yields")

    # Source confirmation
    confirmation_count: int = 1               # how many distinct sources reported this
    conflicting_sources: tuple = ()           # sources that contradicted

    # Linkage to scheduled event (if any)
    scheduled_event_id: Optional[str] = None
    is_scheduled_event: bool = False

    # Raw payload
    raw: dict = field(default_factory=dict)
    item_id: str = ""                         # stable identifier (hash of headline+source+time)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("published_at", "received_at", "normalized_utc_time"):
            if d.get(k) and isinstance(d[k], datetime):
                d[k] = d[k].isoformat()
        return d


# ----------------------------------------------------------------------
# 2) EventSchedule — pre-known events.
# ----------------------------------------------------------------------
@dataclass
class EventSchedule:
    """A scheduled economic event known IN ADVANCE.

    Loaded from official calendars at startup and refreshed daily/weekly.
    """
    event_id: str
    name: str                                 # "NFP", "FOMC Rate Decision"
    when_utc: datetime
    impact_level: ImpactLevel = "medium"
    affected_currencies: tuple = ()           # ("USD",) for NFP
    affected_pairs: tuple = ()                # ("EUR/USD", "USD/JPY")

    # Source of the schedule
    source: str = "calendar"

    # Pre/during/post-event windows in MINUTES
    pre_window_min: int = 30                  # block trading 30 min before
    during_window_min: int = 5                # blackout exactly at event time
    post_window_min: int = 60                 # cool-down after event

    # When event fires, fill in actual numbers vs forecast
    forecast_value: Optional[float] = None
    actual_value: Optional[float] = None
    previous_value: Optional[float] = None
    surprise_score: Optional[float] = None    # (actual - forecast) / forecast volatility

    def to_dict(self) -> dict:
        d = asdict(self)
        if d.get("when_utc") and isinstance(d["when_utc"], datetime):
            d["when_utc"] = d["when_utc"].isoformat()
        return d


# ----------------------------------------------------------------------
# 3) NewsVerdict — the trading decision output.
# ----------------------------------------------------------------------
@dataclass
class NewsVerdict:
    """Final NewsMind v2 verdict for the engine.

    GateMind treats trade_permission as authoritative — if "block",
    no trade may fire regardless of ChartMind / MarketMind.
    """
    headline: str
    source_name: str = ""
    source_type: SourceType = "calendar"

    # Time tracking (snapshot of NewsItem)
    published_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    normalized_utc_time: Optional[datetime] = None
    news_age_seconds: float = 0.0

    # Freshness
    freshness_status: FreshnessStatus = "unknown"

    # Verification
    verified: bool = False
    confirmation_count: int = 0
    conflicting_sources: tuple = ()

    # Impact / direction
    impact_level: ImpactLevel = "unknown"
    affected_assets: tuple = ()
    market_bias: str = "neutral"               # "bullish" | "bearish" | "neutral" | "unclear"
    risk_mode: str = "unclear"                 # "risk_on" | "risk_off" | "unclear"

    # Grade (matches Engine grade scale)
    grade: str = "C"
    confidence: float = 0.0                    # 0..1

    # The decision (the only thing GateMind needs to honour)
    trade_permission: TradePermission = "block"
    reason: str = ""

    # Audit trail
    sources_checked: tuple = ()
    event_id: Optional[str] = None
    is_scheduled_event: bool = False
    pre_event_window: bool = False             # currently inside pre-window?
    post_event_window: bool = False            # currently inside post-window?

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("published_at", "received_at", "normalized_utc_time"):
            if d.get(k) and isinstance(d[k], datetime):
                d[k] = d[k].isoformat()
        return d
