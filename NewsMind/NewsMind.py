# -*- coding: utf-8 -*-
"""NewsMind orchestrator - top-level news and narrative brain."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from NewsMind.config_loader import (
    EventsConfig, KeywordsConfig, NarrativesConfig, SourcesConfig,
    load_events_config, load_sources_config,
    load_narratives_config, load_keywords_config,
)
from NewsMind.news_data import (
    RawItem, SourceAdapter, load_adapters_from_yaml, ingest_all,
)
from NewsMind.event_calendar import EventCalendar, ScheduledEvent
from NewsMind.event_classifier import EventRecord, classify_raw_item
from NewsMind.surprise_engine import (
    compute_surprise_z, direction_from_z, apply_asymmetry,
)
from NewsMind.headline_scanner import HeadlineScanner
from NewsMind.event_windows import EventWindowState, compute_window_state
from NewsMind.channel_router import ChannelImpact, route_event_to_channel
from NewsMind.conviction import compute_conviction, COTSnapshot
from NewsMind.liquidity_session import session_from_utc, liquidity_discount
from NewsMind.news_regime import NewsRegimeState, classify_regime
from NewsMind.narrative_tracker import NarrativeTracker, NarrativeState
from NewsMind.precedent_engine import PrecedentEngine
from NewsMind.news_narrative import build_narrative, one_liner
from NewsMind.integration import HaltSignal  # noqa: F401


@dataclass
class NewsSignal:
    event_id: str
    bias_direction: str
    bias_pip_expected: float
    conviction: str
    decay_remaining_h: float
    channel: str
    narrative_tags: List[str] = field(default_factory=list)
    reasoning_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class NewsContext:
    timestamp: datetime
    last_event: Optional[EventRecord] = None
    next_event: Optional[EventRecord] = None
    window_state: EventWindowState = field(
        default_factory=lambda: EventWindowState(
            event_id=None, in_pre_window=False, in_post_window=False,
            t_to_event_min=None, t_since_event_min=None,
            trading_halted=False, widen_stops_multiplier=1.0,
            window_reason="",
        )
    )
    regime: Optional[NewsRegimeState] = None
    active_narratives: List[NarrativeState] = field(default_factory=list)
    signals_24h: List[NewsSignal] = field(default_factory=list)
    net_bias: str = "neutral"
    bias_strength: float = 0.0
    confidence: float = 0.0
    conviction: str = "low"
    do_not_trade: bool = False
    do_not_trade_reason: str = ""
    narrative: str = ""
    summary_one_liner: str = ""
    liquidity_session: str = ""
    raw_factors_debug: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            "last_event": self.last_event.to_dict() if self.last_event else None,
            "next_event": self.next_event.to_dict() if self.next_event else None,
            "window_state": asdict(self.window_state),
            "regime": self.regime.to_dict() if self.regime else None,
            "active_narratives": [n.to_dict() for n in self.active_narratives],
            "signals_24h": [s.to_dict() for s in self.signals_24h],
            "net_bias": self.net_bias,
            "bias_strength": self.bias_strength,
            "confidence": self.confidence,
            "conviction": self.conviction,
            "do_not_trade": self.do_not_trade,
            "do_not_trade_reason": self.do_not_trade_reason,
            "narrative": self.narrative,
            "summary_one_liner": self.summary_one_liner,
            "liquidity_session": self.liquidity_session,
        }


class NewsMind:
    _CONFIDENCE_BY_PHASE = {
        "shock": 0.2, "denial": 0.4,
        "acceptance": 0.8, "integration": 0.9, "none": 0.3,
    }

    def __init__(self,
                  config_dir: Optional[Path] = None,
                  events_config: Optional[EventsConfig] = None,
                  sources_config: Optional[SourcesConfig] = None,
                  narratives_config: Optional[NarrativesConfig] = None,
                  keywords_config: Optional[KeywordsConfig] = None,
                  precedent_path: Optional[Path] = None,
                  persist_narrative_state: bool = True):
        if config_dir is None:
            config_dir = Path(__file__).parent / "config"
        self.config_dir = Path(config_dir)
        self.events_config = events_config or load_events_config(
            self.config_dir / "events.yaml")
        self.sources_config = sources_config or load_sources_config(
            self.config_dir / "sources.yaml")
        self.narratives_config = narratives_config or load_narratives_config(
            self.config_dir / "narratives.yaml")
        self.keywords_config = keywords_config or load_keywords_config(
            self.config_dir / "keywords.yaml")
        self.calendar = EventCalendar(self.events_config)
        self.scanner = HeadlineScanner(self.events_config, self.keywords_config)
        state_dir = Path(__file__).parent / "state"
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        narrative_state_path = (
            state_dir / "narrative_stage.json"
            if persist_narrative_state else None
        )
        self.narrative_tracker = NarrativeTracker(
            self.narratives_config, state_path=narrative_state_path)
        if precedent_path is None:
            precedent_path = state_dir / "event_history.jsonl"
        self.precedent_engine = PrecedentEngine(precedent_path)
        self._adapters: List[SourceAdapter] = []
        self._snapshot: Optional[NewsContext] = None
        self._recent_events: List[EventRecord] = []
        self._cot: Optional[COTSnapshot] = None

    def build_adapters(self, http_fn=None) -> None:
        self._adapters = load_adapters_from_yaml(
            self.config_dir / "sources.yaml", http_fn=http_fn)

    def poll_once(self, now_utc: Optional[datetime] = None) -> List[RawItem]:
        if not self._adapters:
            return []
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        return ingest_all(self._adapters, now_utc)

    def ingest_items(self, items: List[RawItem],
                        now_utc: Optional[datetime] = None
                        ) -> List[EventRecord]:
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        events: List[EventRecord] = []
        for item in items:
            self.narrative_tracker.update_on_headline(item, now_utc)
        ff_entries = []
        for item in items:
            if item.source_id == "ff_calendar" and item.raw_payload:
                ff_entries.append(item.raw_payload)
        if ff_entries:
            self.calendar.bulk_load_live(ff_entries)
        new_unscheduled = self.scanner.ingest(items)
        events.extend(new_unscheduled)
        seen_ids = {e.event_id for e in events}
        for item in items:
            rec = classify_raw_item(item, self.calendar,
                                     self.events_config, self.keywords_config)
            if rec is None or rec.event_id in seen_ids:
                continue
            seen_ids.add(rec.event_id)
            if rec.consensus is not None and rec.actual is not None:
                z = compute_surprise_z(rec)
                rec.surprise_z = z
                self.precedent_engine.record(rec, None, None,
                                              session=session_from_utc(now_utc))
            events.append(rec)
        self._recent_events.extend(events)
        cutoff = now_utc - timedelta(hours=24)
        self._recent_events = [e for e in self._recent_events
                                if (e.observed_time_utc or now_utc) >= cutoff]
        self.narrative_tracker.decay(now_utc)
        return events

    def set_cot(self, cot: COTSnapshot) -> None:
        self._cot = cot

    def save_state(self) -> None:
        try:
            self.narrative_tracker.save_state()
        except OSError:
            pass

    def close(self) -> None:
        self.save_state()

    def context_now(self, now_utc: Optional[datetime] = None) -> NewsContext:
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        return self._build_context(now_utc)

    def context_at(self, ts: datetime) -> NewsContext:
        return self._build_context(ts)

    def _build_context(self, now_utc: datetime) -> NewsContext:
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=timezone.utc)
        ctx = NewsContext(timestamp=now_utc)
        prev_sched = self.calendar.prev_event(now_utc, min_tier=3)
        next_sched = self.calendar.next_event(now_utc, min_tier=3)
        active_unsch = self.scanner.active_alerts(now_utc)
        ws = compute_window_state(prev_sched, next_sched, active_unsch, now_utc)
        ctx.window_state = ws
        regime = classify_regime(self.calendar, self.scanner, now_utc)
        ctx.regime = regime
        signals: List[NewsSignal] = []
        channels_total = ChannelImpact()
        cutoff = now_utc - timedelta(hours=24)
        by_id: Dict[str, EventRecord] = {}
        for e in self._recent_events:
            if (e.observed_time_utc or now_utc) >= cutoff:
                by_id[e.event_id] = e
        for rec in active_unsch:
            by_id[rec.event_id] = rec
        recent = list(by_id.values())
        for rec in recent:
            sig, ci = self._signal_for_event(rec, now_utc)
            if sig is None:
                continue
            signals.append(sig)
            channels_total.rates += ci.rates
            channels_total.growth += ci.growth
            channels_total.safe_haven += ci.safe_haven
            channels_total.flows += ci.flows
        if prev_sched is not None:
            ctx.last_event = self._scheduled_to_record(prev_sched)
        if next_sched is not None:
            ctx.next_event = self._scheduled_to_record(next_sched)
        ctx.active_narratives = self.narrative_tracker.states()
        narrative_bias = self.narrative_tracker.bias_sum()
        if narrative_bias != 0:
            channels_total.rates += narrative_bias * 0.15
        net = channels_total.net()
        if net > 0.10:
            bias = "long"
        elif net < -0.10:
            bias = "short"
        else:
            bias = "neutral"
        strength = min(1.0, abs(net))
        ctx.net_bias = bias
        ctx.bias_strength = round(strength, 3)
        ctx.conviction = compute_conviction(channels_total, self._cot, now_utc)
        ctx.liquidity_session = session_from_utc(now_utc)
        ctx.confidence = self._confidence_from_phase(prev_sched, now_utc, active_unsch)
        ctx.do_not_trade = (
            ws.trading_halted or
            (regime is not None and regime.black_swan_suspected))
        if ctx.do_not_trade:
            ctx.do_not_trade_reason = (
                "black-swan speed detector" if regime and regime.black_swan_suspected
                else ws.window_reason)
            ctx.bias_strength = min(ctx.bias_strength, 0.3)
        ctx.signals_24h = signals
        ctx.raw_factors_debug = {
            "channels": asdict(channels_total),
            "narrative_bias_sum": narrative_bias,
            "recent_event_count": len(recent),
        }
        ctx.narrative = build_narrative(ctx)
        ctx.summary_one_liner = one_liner(ctx)
        self._snapshot = ctx
        return ctx

    def _signal_for_event(self, rec: EventRecord,
                             now_utc: datetime) -> tuple:
        z = rec.surprise_z
        direction = direction_from_z(z, rec.direction_rule, rec.text_tone)
        if direction == "neutral":
            return None, ChannelImpact()
        mag = 0.0
        if z is not None:
            mag = min(1.0, abs(apply_asymmetry(z)) / 3.0)
        elif rec.is_unscheduled:
            mag = 0.6
        if mag < 0.05:
            return None, ChannelImpact()
        sess = session_from_utc(rec.observed_time_utc or now_utc)
        mag *= liquidity_discount(sess)
        observed = rec.observed_time_utc or now_utc
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        else:
            observed = observed.astimezone(timezone.utc)
        age_h = max(0.0, (now_utc - observed).total_seconds() / 3600.0)
        defn = self.events_config.by_id(rec.event_id)
        half_life = defn.decay_hours if defn else 4.0
        decay_factor = 0.5 ** (age_h / max(0.1, half_life))
        mag *= decay_factor
        sign = 1.0 if direction == "long" else -1.0
        channels = route_event_to_channel(rec, sign, mag)
        if defn is not None and z is not None:
            pip_exp = sign * defn.pip_per_sigma * abs(apply_asymmetry(z))
        elif defn is not None:
            pip_exp = sign * defn.pip_per_sigma
        else:
            udef = self.events_config.unscheduled_by_id(rec.event_id)
            pip_exp = sign * (udef.default_pip if udef else 50.0)
        pip_exp *= liquidity_discount(sess) * decay_factor
        reasoning = self._reasoning(rec, z, sess, age_h)
        sig = NewsSignal(
            event_id=rec.event_id, bias_direction=direction,
            bias_pip_expected=round(pip_exp, 2),
            conviction="medium",
            decay_remaining_h=round(max(0.0, half_life - age_h), 2),
            channel=rec.channel, narrative_tags=[],
            reasoning_text=reasoning,
        )
        return sig, channels

    def _reasoning(self, rec: EventRecord, z: Optional[float],
                    session: str, age_h: float) -> str:
        if z is None:
            return (f"{rec.label} unscheduled alert; channel={rec.channel}; "
                    f"session={session}; age={age_h:.1f}h.")
        sign_word = "beat" if z > 0 else "missed"
        return (f"{rec.label} {sign_word} consensus at z={z:+.2f}; "
                f"channel={rec.channel}; session={session}; age={age_h:.1f}h.")

    def _scheduled_to_record(self, se: ScheduledEvent) -> EventRecord:
        return EventRecord(
            event_id=se.event_id, label=se.definition.label,
            country=se.definition.country, currency=se.definition.currency,
            tier=se.definition.tier, channel=se.definition.channel,
            schedule_time_utc=se.schedule_time_utc,
            observed_time_utc=se.schedule_time_utc,
            actual=se.actual, consensus=se.consensus, previous=se.previous,
            direction_rule=se.definition.direction_rule,
            is_unscheduled=False,
        )

    def _confidence_from_phase(self, prev: Optional[ScheduledEvent],
                                   now_utc: datetime,
                                   actives: List[EventRecord]) -> float:
        most_recent_ts = None
        if prev is not None:
            most_recent_ts = prev.schedule_time_utc
        for a in actives:
            ts = a.observed_time_utc
            if ts is None:
                continue
            if most_recent_ts is None or ts > most_recent_ts:
                most_recent_ts = ts
        if most_recent_ts is None:
            return self._CONFIDENCE_BY_PHASE["none"]
        if most_recent_ts.tzinfo is None:
            most_recent_ts = most_recent_ts.replace(tzinfo=timezone.utc)
        age_min = (now_utc - most_recent_ts).total_seconds() / 60.0
        if age_min < 5:
            return self._CONFIDENCE_BY_PHASE["shock"]
        if age_min < 30:
            return self._CONFIDENCE_BY_PHASE["denial"]
        if age_min < 240:
            return self._CONFIDENCE_BY_PHASE["acceptance"]
        return self._CONFIDENCE_BY_PHASE["integration"]
