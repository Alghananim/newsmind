# -*- coding: utf-8 -*-
"""Per-bar-series memoization for ATR/ADX/swings/levels.

Fingerprint: (id(bars), len, last.close, last.timestamp).
Why id(): bar lists are immutable in our flow (we append new bars, not mutate
existing ones). If a new bar arrives, len changes → new fingerprint. If
ChartMind is called twice in a row with the same bars list, the cache hits.
"""
from __future__ import annotations
from typing import Any, Callable, Tuple


_CACHE: dict = {}
_HITS = 0
_MISSES = 0


def _fingerprint(bars) -> Tuple:
    if not bars: return ("empty",)
    last = bars[-1]
    ts = last.timestamp.isoformat() if hasattr(last, "timestamp") else "?"
    return (id(bars), len(bars), round(getattr(last, "close", 0.0), 6), ts)


def memoize(name: str, bars, compute: Callable[[], Any]) -> Any:
    global _HITS, _MISSES
    key = (name, _fingerprint(bars))
    if key in _CACHE:
        _HITS += 1
        return _CACHE[key]
    _MISSES += 1
    val = compute()
    _CACHE[key] = val
    if len(_CACHE) > 1024:
        for _ in range(128):
            _CACHE.pop(next(iter(_CACHE)))
    return val


def stats() -> dict:
    total = _HITS + _MISSES
    rate = _HITS / total if total > 0 else 0
    return {"hits": _HITS, "misses": _MISSES, "hit_rate": round(rate, 3),
            "size": len(_CACHE)}


def clear():
    global _HITS, _MISSES
    _CACHE.clear()
    _HITS = 0
    _MISSES = 0
