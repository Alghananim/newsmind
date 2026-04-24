# -*- coding: utf-8 -*-
"""MarketMind v1.0 - cross-asset macro brain for EUR/USD.

Reads the world AROUND EUR/USD: synthetic DXY, EUR basket, RORO risk,
correlations, yield context, and cross-asset divergences. Compresses
it all into a MarketContext that ChartMind and NewsMind can consume.

Sources (cite-worthy):
    John Murphy - Intermarket Analysis
    Ashraf Laidi - Currency Trading and Intermarket Analysis
    Marc Chandler - Making Sense of the Dollar
    Martin Pring - Active Asset Allocation
    Lopez de Prado - ML for Asset Managers
    BIS Quarterly Reviews on USD dynamics

All code original Python. No copyrighted material reproduced.
"""
__version__ = "1.0.0"

from MarketMind.MarketMind import (   # noqa: F401
    MarketMind, MarketContext,
)
from MarketMind.market_data import (   # noqa: F401
    MarketDataBundle, ALL_SYMBOLS, CORE_FX, CORE_NON_FX, OPTIONAL,
    bundle_from_dict, returns_matrix,
)
from MarketMind.composites import (   # noqa: F401
    DXYSnapshot, EURStrength, RORO, USDStrength,
    synthetic_dxy, eur_strength_index, roro_index, usd_strength_index,
)
from MarketMind.integration import (   # noqa: F401
    make_market_factor, make_market_conflict, make_market_challenge,
)
