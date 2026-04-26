# -*- coding: utf-8 -*-
"""Hidden contradiction detector — catches subtle traps that pass simple checks.

Each detector returns (label, severity).
    "critical" — block
    "high"     — wait
    "medium"   — flag only

Detectors:
   1. Grade A/A+ but reason contains warning keyword (data_missing/etc)
   2. High confidence but data flagged stale
   3. All "allow" but spread is wide (passing minimum threshold)
   4. In-window but within 5 minutes of window end (retry risk)
   5. Stop/target exist but R/R below 1.0 (low edge)
   6. monitoring + live + live_enabled (already covered, sanity)
   7. allow from chart but block-mention in chart.reason
   8. Trying to enter same direction immediately after a stop-out (proxy via cooldown not set + recent_loss)
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional, Tuple
from .models import BrainSummary, SystemState


_WARN_KEYWORDS = ("warning", "stale", "missing", "uncertain", "incomplete",
                  "broken", "fake", "extreme", "violated")


@dataclass
class ContradictionResult:
    items: list = field(default_factory=list)
    @property
    def critical(self) -> bool: return any(s == "critical" for _, s in self.items)
    @property
    def high(self) -> bool: return any(s == "high" for _, s in self.items)
    @property
    def medium(self) -> bool: return any(s == "medium" for _, s in self.items)
    def labels(self) -> tuple: return tuple(label for label, _ in self.items)


def detect(*, news: Optional[BrainSummary], market: Optional[BrainSummary],
           chart: Optional[BrainSummary],
           state: Optional[SystemState],
           now_utc: datetime,
           rr: Optional[float] = None,
           session_status: str = "outside",
           ny_minute: int = -1) -> ContradictionResult:
    items = []

    # 1. Grade A/A+ but reason contains warning keyword
    for b in (news, market, chart):
        if b is None: continue
        if b.grade in ("A", "A+"):
            r = (b.reason or "").lower()
            if any(k in r for k in _WARN_KEYWORDS):
                items.append((f"{b.name}_high_grade_with_warning_keyword", "high"))
            # warnings tuple too
            for w in (b.warnings or ()):
                if any(k in w.lower() for k in _WARN_KEYWORDS):
                    items.append((f"{b.name}_warning_despite_high_grade:{w[:30]}", "high"))
                    break

    # 2. High confidence but data flagged stale
    if state and state.data_latency_ms > state.max_data_latency_ms:
        for b in (news, market, chart):
            if b and b.confidence >= 0.8:
                items.append((f"{b.name}_high_conf_but_data_stale", "high"))
                break

    # 3. Spread passing threshold but >70% of max
    if state and state.spread_pips is not None:
        if state.spread_pips > state.max_spread_pips * 0.7:
            items.append((f"spread_near_max:{state.spread_pips:.2f}>{state.max_spread_pips*0.7:.2f}", "medium"))

    # 4. In window but very close to window end (last 5 min)
    if session_status == "in_window" and ny_minute >= 55:
        items.append(("in_window_but_near_end_retry_risk", "medium"))

    # 5. Stop/target exist but R/R weak (1.0-1.2 = "marginal" already caught,
    #    but rr 1.0-1.5 is "ok" per risk_check yet weak edge)
    if rr is not None and 1.0 <= rr < 1.2:
        items.append((f"rr_weak_edge:{rr:.2f}", "medium"))

    # 6. Pair monitoring + live mode + live_enabled (defensive duplicate)
    if (state and state.pair_status == "monitoring"
        and state.broker_mode == "live" and state.live_enabled):
        items.append(("pair_monitoring_with_live_enabled", "critical"))

    # 7. Chart allow but reason mentions block-related keywords
    if chart and chart.permission == "allow":
        r = (chart.reason or "").lower()
        for k in ("fake_breakout", "liquidity_sweep", "chase", "mtf_conflict"):
            if k in r:
                items.append((f"chart_allow_but_reason_has_{k}", "high"))
                break

    # 8. Recent loss without cooldown set (consecutive_losses 1-2 + no cooldown,
    #    user is trying to re-enter immediately)
    if state and state.consecutive_losses in (1, 2) and state.cooldown_until_utc is None:
        items.append(("recent_loss_no_cooldown_set_retry_risk", "medium"))

    # 9. Direction inconsistency (news bias != market direction != chart direction)
    if news and market and chart:
        dirs = [news.direction, market.direction, chart.direction]
        clear = [d for d in dirs if d in ("bullish","bearish")]
        if len(clear) >= 2 and len(set(clear)) > 1:
            items.append((f"direction_inconsistency:{dirs}", "critical"))

    return ContradictionResult(items=items)


def severity_to_outcome(result: ContradictionResult) -> Tuple[str, str]:
    """Return (perm_override, grade_floor): block / wait / "" """
    if result.critical: return ("block", "C")
    if result.high:     return ("wait", "C")
    if result.medium:   return ("",     "B")
    return ("", "A+")
