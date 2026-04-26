# -*- coding: utf-8 -*-
"""Per-currency relative strength.

For EUR/USD: dollar bias from synthetic DXY, EUR bias from EUR-cross strength
(if EUR/JPY available; else inferred as -dollar bias).

For USD/JPY: dollar bias from DXY, JPY bias inferred from risk_mode + JPY-crosses.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from .models import Bar
from .synthetic_dxy import compute as compute_dxy, SyntheticDxyResult


@dataclass
class StrengthSnapshot:
    dollar: str = "unclear"
    counter: str = "unclear"
    dollar_strength: float = 0.5
    counter_strength: float = 0.5
    notes: tuple = ()


def _bias_label_full(strength: float) -> str:
    """Coarser bands for the operator-facing label."""
    if strength >= 0.65: return "strong"
    if strength <= 0.35: return "weak"
    return "neutral"


def assess_for_pair(*, pair: str, baskets: dict[str, List[Bar]],
                    risk_mode: str = "unclear",
                    window: int = 20) -> StrengthSnapshot:
    notes = []
    dxy = compute_dxy(baskets=baskets, window=window)
    notes.append(f"dxy_dir:{dxy.direction}_str:{dxy.strength}_cov:{dxy.coverage}")

    dollar_label = _bias_label_full(dxy.strength)
    snap = StrengthSnapshot(
        dollar=dollar_label,
        dollar_strength=dxy.strength,
        notes=tuple(notes),
    )

    if pair == "EUR/USD":
        # EUR strength is the inverse of dollar — but we can refine using
        # EUR/JPY if available.
        if "EUR/JPY" in baskets and len(baskets["EUR/JPY"]) >= window + 1:
            bars = baskets["EUR/JPY"]
            pct = (bars[-1].close - bars[-window-1].close) / bars[-window-1].close
            eur_strength = 0.5 + pct / 0.02
            eur_strength = max(0.0, min(1.0, eur_strength))
            snap.counter_strength = round(eur_strength, 3)
            snap.counter = _bias_label_full(eur_strength)
            notes = list(snap.notes) + [f"eur_jpy_inferred:{eur_strength:.3f}"]
            snap.notes = tuple(notes)
        else:
            # Infer as inverse of dollar
            snap.counter_strength = round(1 - dxy.strength, 3)
            snap.counter = _bias_label_full(snap.counter_strength)

    elif pair == "USD/JPY":
        # JPY strength heavily influenced by risk_mode (haven flow)
        # If risk_off, JPY is strong regardless of USD direction.
        if risk_mode == "risk_off":
            snap.counter = "strong"
            snap.counter_strength = 0.75
            snap.notes = tuple(list(snap.notes) + ["jpy_risk_off_haven"])
        elif risk_mode == "risk_on":
            snap.counter = "weak"
            snap.counter_strength = 0.25
            snap.notes = tuple(list(snap.notes) + ["jpy_risk_on_offload"])
        else:
            # Infer from USD/JPY bars themselves — if USD/JPY rising fast
            # and DXY flat, JPY weakening; if USD/JPY falling and DXY rising,
            # JPY strengthening (haven flow).
            if "USD/JPY" in baskets and len(baskets["USD/JPY"]) >= window + 1:
                bars = baskets["USD/JPY"]
                pct = (bars[-1].close - bars[-window-1].close) / bars[-window-1].close
                # USD/JPY up = JPY down (in absence of other info)
                jpy_strength = 0.5 - pct / 0.02
                jpy_strength = max(0.0, min(1.0, jpy_strength))
                snap.counter_strength = round(jpy_strength, 3)
                snap.counter = _bias_label_full(jpy_strength)

    return snap
