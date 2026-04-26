# -*- coding: utf-8 -*-
"""Per-call dummy cache (placeholder for parity with other brains).
GateMind decisions are stateless and very fast — no real caching needed.
This module exists so the orchestrator API matches MarketMind/ChartMind."""
def clear(): pass
