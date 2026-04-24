# -*- coding: utf-8 -*-
"""MarketMind main class - compresses intermarket state into MarketContext."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from MarketMind.market_data import MarketDataBundle, bundle_from_dict
from MarketMind.composites import (
    DXYSnapshot, EURStrength, RORO, USDStrength,
    synthetic_dxy, eur_strength_index, roro_index, usd_strength_index,
)


@dataclass
class MarketContext:
    timestamp: "datetime"
    usd_strength: Optional[USDStrength] = None
    eur_strength: Optional[EURStrength] = None
    dxy_synthetic: Optional[DXYSnapshot] = None
    roro: Optional[RORO] = None
    net_bias: str = "neutral"
    bias_strength: float = 0.0
    supporting_factors: List[str] = field(default_factory=list)
    opposing_factors: List[str] = field(default_factory=list)
    narrative: str = ""
    summary_one_liner: str = ""
    halt_trading: bool = False
    halt_reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if hasattr(self.timestamp, "isoformat"):
            d["timestamp"] = self.timestamp.isoformat()
        return d


class MarketMind:
    """Stateless: every analyze() is independent."""

    @staticmethod
    def bundle_from_frames(frames: Dict) -> MarketDataBundle:
        return bundle_from_dict(frames)

    def analyze(self, bundle: MarketDataBundle) -> MarketContext:
        timestamp = self._pick_ts(bundle)
        dxy = synthetic_dxy(bundle)
        eur = eur_strength_index(bundle)
        roro = roro_index(bundle)
        usd = usd_strength_index(dxy)
        ctx = MarketContext(
            timestamp=timestamp, usd_strength=usd, eur_strength=eur,
            dxy_synthetic=dxy, roro=roro,
        )
        bias, strength, supporting, opposing = _consolidate_bias(
            usd=usd, eur=eur, roro=roro)
        ctx.net_bias = bias
        ctx.bias_strength = strength
        ctx.supporting_factors = supporting
        ctx.opposing_factors = opposing
        ctx.narrative = _narrate(ctx)
        ctx.summary_one_liner = _one_liner(ctx)
        return ctx

    def _pick_ts(self, bundle: MarketDataBundle):
        if bundle.common_index is not None and len(bundle.common_index) > 0:
            last = bundle.common_index[-1]
            return last
        for df in bundle.frames.values():
            if len(df) > 0:
                return df.index[-1]
        return datetime.now(timezone.utc)


def _consolidate_bias(usd: Optional[USDStrength],
                        eur: Optional[EURStrength],
                        roro: Optional[RORO]):
    weighted = 0.0
    total_w = 0.0
    supporting: List[str] = []
    opposing: List[str] = []
    if usd is not None:
        c = -usd.value * 0.45
        weighted += c
        total_w += 0.45
        if c > 0.02:
            supporting.append(f"USD weakness ({usd.value:+.2f})")
        elif c < -0.02:
            opposing.append(f"USD strength ({usd.value:+.2f})")
    if eur is not None:
        c = eur.value * 0.35
        weighted += c
        total_w += 0.35
        if c > 0.02:
            supporting.append(f"EUR basket strong ({eur.value:+.2f})")
        elif c < -0.02:
            opposing.append(f"EUR basket weak ({eur.value:+.2f})")
    if roro is not None:
        c = roro.value * 0.20
        weighted += c
        total_w += 0.20
        if roro.detail == "risk_on":
            supporting.append(f"Risk-on ({roro.value:+.2f})")
        elif roro.detail == "risk_off":
            opposing.append(f"Risk-off ({roro.value:+.2f})")
    raw = weighted / total_w if total_w > 0 else 0.0
    raw = max(-1.0, min(1.0, raw))
    if raw >= 0.10:
        bias = "long"
    elif raw <= -0.10:
        bias = "short"
    else:
        bias = "neutral"
    return bias, round(abs(raw), 3), supporting, opposing


def _narrate(ctx: MarketContext) -> str:
    parts = []
    if ctx.usd_strength:
        parts.append(f"USD is {ctx.usd_strength.detail} ({ctx.usd_strength.value:+.2f}).")
    if ctx.eur_strength:
        parts.append(f"EUR basket {ctx.eur_strength.value:+.2f}.")
    if ctx.roro:
        parts.append(f"Risk tone: {ctx.roro.detail}.")
    parts.append(f"Net bias {ctx.net_bias} strength {ctx.bias_strength:.2f}.")
    return " ".join(parts)


def _one_liner(ctx: MarketContext) -> str:
    return f"MM: {ctx.net_bias} {ctx.bias_strength:.2f} | supp={len(ctx.supporting_factors)} opp={len(ctx.opposing_factors)}"
