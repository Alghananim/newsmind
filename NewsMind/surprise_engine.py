# -*- coding: utf-8 -*-
"""Surprise Engine. z = (actual - consensus)/stdev, asymmetry 1.5x on neg."""
from __future__ import annotations
from typing import Optional
from NewsMind.event_classifier import EventRecord

_ASYMMETRY_NEG_MULT = 1.5


def compute_surprise_z(event: EventRecord,
                         default_stdev_factor: float = 0.5) -> Optional[float]:
    if event.actual is None or event.consensus is None:
        return None
    stdev = event.consensus_stdev
    if stdev is None or stdev <= 0:
        base = abs(event.consensus) if event.consensus else 1.0
        stdev = max(base * default_stdev_factor, 0.1)
    z = (event.actual - event.consensus) / stdev
    if z > 5.0:
        z = 5.0
    elif z < -5.0:
        z = -5.0
    return z


def apply_asymmetry(z: float) -> float:
    """Kahneman-Tversky loss aversion; Andersen 2003 asymmetry."""
    if z < 0:
        return z * _ASYMMETRY_NEG_MULT
    return z


def direction_from_z(z: Optional[float],
                       direction_rule: str,
                       text_tone: Optional[str] = None) -> str:
    if direction_rule in ("risk_off_usd_bullish", "usd_bullish_risk_off"):
        return "short"
    if direction_rule == "risk_on_usd_bearish":
        return "long"
    if direction_rule == "eur_bearish":
        return "short"
    if direction_rule in ("hawkish_press_usd_positive", "hawkish_text_usd_positive"):
        if text_tone == "hawkish":
            return "short"
        if text_tone == "dovish":
            return "long"
        return "neutral"
    if direction_rule in ("hawkish_press_eur_positive", "hawkish_text_eur_positive"):
        if text_tone == "hawkish":
            return "long"
        if text_tone == "dovish":
            return "short"
        return "neutral"
    if z is None:
        return "neutral"
    if direction_rule == "usd_positive_on_beat":
        return "short" if z > 0 else ("long" if z < 0 else "neutral")
    if direction_rule == "usd_negative_on_beat":
        return "long" if z > 0 else ("short" if z < 0 else "neutral")
    if direction_rule == "eur_positive_on_beat":
        return "long" if z > 0 else ("short" if z < 0 else "neutral")
    if direction_rule == "eur_negative_on_beat":
        return "short" if z > 0 else ("long" if z < 0 else "neutral")
    if direction_rule == "eur_positive_on_hike":
        return "long" if z > 0 else ("short" if z < 0 else "neutral")
    if direction_rule.startswith(("gbp_", "jpy_", "chf_", "aud_", "cad_")):
        return "neutral"
    return "neutral"
