# -*- coding: utf-8 -*-
"""MarketData bundle - OHLCV frames for cross-asset analysis."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional

try:
    import pandas as pd
except ImportError:
    pd = None   # type: ignore


CORE_FX = ["EUR_USD", "USD_JPY", "GBP_USD", "AUD_USD", "USD_CAD", "USD_CHF"]
CORE_NON_FX = ["XAU_USD", "SPX500_USD"]
OPTIONAL = ["USB10Y_USD", "DE30_EUR", "WTICO_USD"]
ALL_SYMBOLS = CORE_FX + CORE_NON_FX + OPTIONAL


@dataclass
class MarketDataBundle:
    """Holds aligned OHLCV frames keyed by symbol."""
    frames: Dict[str, "pd.DataFrame"] = field(default_factory=dict)
    common_index: Optional["pd.DatetimeIndex"] = None
    timeframe: str = "M15"

    def has(self, symbol: str) -> bool:
        return symbol in self.frames and len(self.frames[symbol]) > 0

    def symbols(self) -> List[str]:
        return list(self.frames.keys())


def bundle_from_dict(frames: Dict[str, "pd.DataFrame"],
                       timeframe: str = "M15") -> MarketDataBundle:
    """Build a bundle from a dict of symbol -> DataFrame."""
    if pd is None:
        raise RuntimeError("pandas not available")
    b = MarketDataBundle(frames=dict(frames), timeframe=timeframe)
    if frames:
        common = None
        for df in frames.values():
            if df is None or df.empty:
                continue
            idx = df.index
            common = idx if common is None else common.intersection(idx)
        b.common_index = common
    return b


def returns_matrix(bundle: MarketDataBundle) -> "pd.DataFrame":
    """Return log-returns matrix for all symbols in bundle."""
    if pd is None:
        raise RuntimeError("pandas not available")
    import numpy as np
    cols = {}
    for sym, df in bundle.frames.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        close = df["Close"].dropna()
        if len(close) < 2:
            continue
        ret = np.log(close).diff().dropna()
        cols[sym] = ret
    return pd.concat(cols, axis=1) if cols else pd.DataFrame()
