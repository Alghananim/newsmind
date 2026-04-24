# -*- coding: utf-8 -*-
"""Integration hooks: MarketMind -> ChartMind."""
from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from MarketMind.MarketMind import MarketContext


def make_market_factor(ctx: "MarketContext"):
    try:
        from ChartMind import ConfluenceFactor  # type: ignore
    except ImportError:
        @dataclass
        class _F:
            name: str
            direction: str
            raw_strength: float
            weight: float
            contribution: float
            def to_dict(self) -> dict:
                return {"name": self.name, "direction": self.direction,
                        "raw_strength": self.raw_strength,
                        "weight": self.weight, "contribution": self.contribution}
        ConfluenceFactor = _F  # type: ignore
    sign = 1.0 if ctx.net_bias == "long" else (-1.0 if ctx.net_bias == "short" else 0.0)
    return ConfluenceFactor(
        name="MarketMind", direction=ctx.net_bias,
        raw_strength=ctx.bias_strength,
        weight=0.15,
        contribution=sign * ctx.bias_strength * 0.15,
    )


def make_market_conflict(ctx: "MarketContext", chart_analysis=None):
    try:
        from ChartMind import Conflict  # type: ignore
    except ImportError:
        @dataclass
        class _C:
            kind: str
            severity: float
            detail: str
            def to_dict(self) -> dict:
                return {"kind": self.kind, "severity": self.severity,
                        "detail": self.detail}
        Conflict = _C  # type: ignore
    if ctx.net_bias == "neutral":
        return None
    chart_bias = None
    if chart_analysis is not None:
        chart_bias = getattr(chart_analysis, "net_direction", None) or \
                     getattr(chart_analysis, "final_decision", None)
    if chart_bias not in ("long", "short") or chart_bias == ctx.net_bias:
        return None
    severity = min(1.0, ctx.bias_strength * 0.85)
    return Conflict(
        kind="market_vs_chart", severity=severity,
        detail=f"MarketMind {ctx.net_bias} opposes ChartMind {chart_bias}.",
    )


def make_market_challenge(ctx: "MarketContext"):
    try:
        from ChartMind.devils_advocate import Challenge  # type: ignore
    except ImportError:
        @dataclass
        class _Ch:
            challenge_id: str
            title: str
            body: str
            severity: float
            def to_dict(self) -> dict:
                return {"challenge_id": self.challenge_id, "title": self.title,
                        "body": self.body, "severity": self.severity}
        Challenge = _Ch  # type: ignore
    if ctx.bias_strength < 0.5:
        return None
    body = (f"MarketMind bias {ctx.net_bias} with strength {ctx.bias_strength:.2f}. "
            f"Supporting: {', '.join(ctx.supporting_factors[:3])}. "
            f"Consider whether macro is already priced in.")
    title = f"Macro conviction: {ctx.net_bias}"
    try:
        import dataclasses as _dc
        flds = {f.name for f in _dc.fields(Challenge)}
    except Exception:
        flds = set()
    if {"challenge_id", "title", "body"}.issubset(flds):
        return Challenge(challenge_id=f"market_{ctx.net_bias}",
                          title=title, body=body, severity=ctx.bias_strength)
    if {"name", "title_ar", "reasoning"}.issubset(flds):
        return Challenge(name=f"market_{ctx.net_bias}",
                          severity=ctx.bias_strength,
                          title_ar=title, reasoning=body)
    try:
        return Challenge(f"market_{ctx.net_bias}", ctx.bias_strength, title, body)
    except Exception:
        return None
