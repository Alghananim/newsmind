# -*- coding: utf-8 -*-
"""Integration hooks: NewsMind -> ChartMind + MarketMind. Lazy imports."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from NewsMind.NewsMind import NewsContext


@dataclass
class HaltSignal:
    active: bool
    reason: str
    expires_at: Optional[datetime]
    source: str = "news"

    def to_dict(self) -> dict:
        return {
            "active": self.active, "reason": self.reason,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "source": self.source,
        }


def make_news_factor(ctx: "NewsContext", market_ctx=None):
    try:
        from ChartMind import ConfluenceFactor  # type: ignore
    except ImportError:
        @dataclass
        class _Factor:
            name: str
            direction: str
            raw_strength: float
            weight: float
            contribution: float

            def to_dict(self) -> dict:
                return {"name": self.name, "direction": self.direction,
                        "raw_strength": self.raw_strength,
                        "weight": self.weight,
                        "contribution": self.contribution}
        ConfluenceFactor = _Factor  # type: ignore
    weight = 0.10
    if ctx.window_state and ctx.window_state.event_id:
        weight += 0.10
    if ctx.conviction == "high":
        weight += 0.05
    if market_ctx is not None and hasattr(market_ctx, "net_bias"):
        if market_ctx.net_bias == ctx.net_bias and ctx.net_bias != "neutral":
            weight += 0.05
    weight = min(0.30, weight)
    sign = 1.0 if ctx.net_bias == "long" else (-1.0 if ctx.net_bias == "short" else 0.0)
    raw_strength = ctx.bias_strength
    contribution = sign * raw_strength * weight
    return ConfluenceFactor(
        name="NewsMind", direction=ctx.net_bias,
        raw_strength=raw_strength, weight=weight,
        contribution=contribution,
    )


def make_news_conflict(ctx: "NewsContext", chart_analysis=None, market_ctx=None):
    try:
        from ChartMind import Conflict  # type: ignore
    except ImportError:
        @dataclass
        class _Conflict:
            kind: str
            severity: float
            detail: str

            def to_dict(self) -> dict:
                return {"kind": self.kind, "severity": self.severity,
                        "detail": self.detail}
        Conflict = _Conflict  # type: ignore
    if ctx.net_bias == "neutral":
        return None
    chart_bias = None
    market_bias = None
    if chart_analysis is not None:
        chart_bias = getattr(chart_analysis, "net_direction", None) or \
                     getattr(chart_analysis, "final_decision", None)
    if market_ctx is not None:
        market_bias = getattr(market_ctx, "net_bias", None)
    target = target_label = None
    if chart_bias in ("long", "short") and chart_bias != ctx.net_bias:
        target = chart_bias
        target_label = "ChartMind"
    elif market_bias in ("long", "short") and market_bias != ctx.net_bias:
        target = market_bias
        target_label = "MarketMind"
    if target is None:
        return None
    severity = min(1.0, ctx.bias_strength * 0.9)
    detail = (f"NewsMind bias {ctx.net_bias.upper()} opposes "
              f"{target_label} bias {target.upper()}. "
              f"Conviction {ctx.conviction}; confidence {ctx.confidence:.2f}.")
    return Conflict(
        kind=f"news_vs_{target_label.lower()}",
        severity=severity, detail=detail,
    )


def make_news_challenge(ctx: "NewsContext"):
    try:
        from ChartMind.devils_advocate import Challenge  # type: ignore
    except ImportError:
        @dataclass
        class _Challenge:
            challenge_id: str
            title: str
            body: str
            severity: float

            def to_dict(self) -> dict:
                return {"challenge_id": self.challenge_id, "title": self.title,
                        "body": self.body, "severity": self.severity}
        Challenge = _Challenge  # type: ignore
    if not ctx.active_narratives:
        return None
    dom = max(ctx.active_narratives,
               key=lambda n: n.reflexivity_stage * n.conviction)
    if dom.reflexivity_stage < 6:
        return None
    severity = min(1.0, (dom.reflexivity_stage - 5) / 3.0)
    body = (f"Narrative {dom.label} is at Soros reflexivity stage "
            f"{dom.reflexivity_stage}/8. At late stages, narratives often "
            f"reverse as the success of the story undermines its premise.")
    title = f"Late-stage narrative: {dom.label}"
    try:
        import dataclasses as _dc
        flds = {f.name for f in _dc.fields(Challenge)}
    except Exception:
        flds = set()
    if {"challenge_id", "title", "body"}.issubset(flds):
        return Challenge(
            challenge_id=f"news_late_stage_{dom.narrative_id}",
            title=title, body=body, severity=severity,
        )
    if {"name", "title_ar", "reasoning"}.issubset(flds):
        return Challenge(
            name=f"news_late_stage_{dom.narrative_id}",
            severity=severity, title_ar=title, reasoning=body,
        )
    try:
        return Challenge(f"news_late_stage_{dom.narrative_id}",
                         severity, title, body)
    except Exception:
        return None


def make_news_halt(ctx: "NewsContext") -> Optional[HaltSignal]:
    if not (ctx.do_not_trade or ctx.window_state.trading_halted or
            (ctx.regime and ctx.regime.black_swan_suspected)):
        return None
    reason = ctx.do_not_trade_reason or ctx.window_state.window_reason
    if not reason:
        reason = "news-driven halt"
    return HaltSignal(active=True, reason=reason, expires_at=None, source="news")
