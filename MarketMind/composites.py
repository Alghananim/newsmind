# -*- coding: utf-8 -*-
"""Composites: synthetic DXY, EUR strength, RORO, USD strength."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from MarketMind.market_data import MarketDataBundle


# ICE DXY weights (historical basis).
_DXY_WEIGHTS_FULL = {
    "EUR_USD": -0.576,
    "USD_JPY": +0.136,
    "GBP_USD": -0.119,
    "USD_CAD": +0.091,
    "USD_CHF": +0.036,
}
_DXY_BASE = 50.14348112

_RORO_WEIGHTS = {
    "SPX500_USD": +0.35,
    "XAU_USD":    -0.25,
    "AUD_USD":    +0.20,
    "USD_JPY":    +0.20,
}


@dataclass
class DXYSnapshot:
    value: float
    change_pct: float
    n_components: int


@dataclass
class EURStrength:
    value: float     # -1..+1
    detail: str


@dataclass
class RORO:
    value: float     # -1 risk_off .. +1 risk_on
    detail: str


@dataclass
class USDStrength:
    value: float     # -1..+1
    detail: str


def synthetic_dxy(bundle: MarketDataBundle) -> Optional[DXYSnapshot]:
    """Best-effort synthetic DXY. Requires at least EUR_USD + one other."""
    try:
        import numpy as np
    except ImportError:
        return None
    values = {}
    for sym in _DXY_WEIGHTS_FULL:
        if not bundle.has(sym):
            continue
        df = bundle.frames[sym]
        if "Close" not in df.columns or len(df) < 2:
            continue
        values[sym] = df["Close"].iloc[-1]
    if "EUR_USD" not in values or len(values) < 2:
        return None
    log_sum = 0.0
    w_sum = 0.0
    for sym, v in values.items():
        w = _DXY_WEIGHTS_FULL[sym]
        log_sum += w * float(np.log(v) if v > 0 else 0)
        w_sum += abs(w)
    dxy = _DXY_BASE * float(np.exp(log_sum))
    # Change vs prior bar.
    prev_log_sum = 0.0
    for sym, v in values.items():
        df = bundle.frames[sym]
        prev_v = float(df["Close"].iloc[-2])
        w = _DXY_WEIGHTS_FULL[sym]
        prev_log_sum += w * float(np.log(prev_v) if prev_v > 0 else 0)
    prev_dxy = _DXY_BASE * float(np.exp(prev_log_sum))
    change_pct = ((dxy - prev_dxy) / prev_dxy * 100.0) if prev_dxy else 0.0
    return DXYSnapshot(value=dxy, change_pct=change_pct,
                        n_components=len(values))


def eur_strength_index(bundle: MarketDataBundle) -> Optional[EURStrength]:
    """Derive EUR/GBP, EUR/JPY from EUR_USD legs; score EUR in basket."""
    if not bundle.has("EUR_USD"):
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    eur_usd_last = float(bundle.frames["EUR_USD"]["Close"].iloc[-1])
    eur_usd_prev = float(bundle.frames["EUR_USD"]["Close"].iloc[-2])
    scores = []
    # vs USD
    scores.append((eur_usd_last - eur_usd_prev) / eur_usd_prev)
    # vs GBP
    if bundle.has("GBP_USD"):
        gbp_last = float(bundle.frames["GBP_USD"]["Close"].iloc[-1])
        gbp_prev = float(bundle.frames["GBP_USD"]["Close"].iloc[-2])
        eur_gbp_last = eur_usd_last / gbp_last if gbp_last else 0
        eur_gbp_prev = eur_usd_prev / gbp_prev if gbp_prev else 0
        if eur_gbp_prev:
            scores.append((eur_gbp_last - eur_gbp_prev) / eur_gbp_prev)
    # vs JPY
    if bundle.has("USD_JPY"):
        jpy_last = float(bundle.frames["USD_JPY"]["Close"].iloc[-1])
        jpy_prev = float(bundle.frames["USD_JPY"]["Close"].iloc[-2])
        eur_jpy_last = eur_usd_last * jpy_last
        eur_jpy_prev = eur_usd_prev * jpy_prev
        if eur_jpy_prev:
            scores.append((eur_jpy_last - eur_jpy_prev) / eur_jpy_prev)
    if not scores:
        return None
    avg = sum(scores) / len(scores)
    value = max(-1.0, min(1.0, avg * 500))
    return EURStrength(
        value=round(value, 3),
        detail=f"EUR basket across {len(scores)} pairs")


def roro_index(bundle: MarketDataBundle) -> Optional[RORO]:
    """Risk-on / Risk-off composite using SPX, Gold, AUD, JPY."""
    try:
        import numpy as np
    except ImportError:
        return None
    contribs = []
    for sym, w in _RORO_WEIGHTS.items():
        if not bundle.has(sym):
            continue
        df = bundle.frames[sym]
        if "Close" not in df.columns or len(df) < 2:
            continue
        last = float(df["Close"].iloc[-1])
        prev = float(df["Close"].iloc[-2])
        if prev == 0:
            continue
        ret = (last - prev) / prev
        contribs.append(w * ret)
    if not contribs:
        return None
    raw = sum(contribs)
    value = max(-1.0, min(1.0, raw * 100))
    detail = ("risk_on" if value > 0.1 else
              "risk_off" if value < -0.1 else "neutral")
    return RORO(value=round(value, 3), detail=detail)


def usd_strength_index(dxy: Optional[DXYSnapshot]) -> Optional[USDStrength]:
    """USD strength derived from DXY direction/change."""
    if dxy is None:
        return None
    value = max(-1.0, min(1.0, dxy.change_pct / 2.0))
    detail = ("strong" if value > 0.2 else "weak" if value < -0.2 else "mixed")
    return USDStrength(value=round(value, 3), detail=detail)
