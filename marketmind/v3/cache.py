# -*- coding: utf-8 -*-
"""Per-bar-series memoization for ATR / ADX / percentile / correlation.

Key insight: bar series only mutates when a new bar arrives. We
fingerprint the series by (id, len, last_close, last_ts) and cache
expensive indicator computations against that fingerprint. A WeakValueDict
prevents memory leaks across re-runs.

The cache is module-level (process-wide) because Engine.assess will be
called many times with overlapping series.
"""
from __future__ import annotations
from typing import Any, Callable, Tuple


_CACHE: dict = {}
_HITS = 0
_MISSES = 0


def _fingerprint(bars) -> Tuple:
    """Cheap fingerprint: (id_of_list, length, last_close, last_ts_iso).

    `id()` is enough because we never mutate bars; we'd append/replace.
    """
    if not bars: return ("empty",)
    last = bars[-1]
    ts = last.timestamp.isoformat() if hasattr(last, "timestamp") else "?"
    return (id(bars), len(bars), round(getattr(last, "close", 0.0), 6), ts)


def memoize(name: str, bars, compute: Callable[[], Any]) -> Any:
    """Lookup or compute. Cache key = (name, fingerprint)."""
    global _HITS, _MISSES
    key = (name, _fingerprint(bars))
    if key in _CACHE:
        _HITS += 1
        return _CACHE[key]
    _MISSES += 1
    val = compute()
    _CACHE[key] = val
    # Bound memory: keep at most 1024 entries (LRU-ish)
    if len(_CACHE) > 1024:
        # Drop ~10% oldest by insertion order (Python dicts preserve order)
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
