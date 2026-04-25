# -*- coding: utf-8 -*-
"""Parallel runner — fan out N brain calls concurrently.

The five brains do not depend on each other's outputs (Engine composes
them in `_build_brain_grades`), so we can call all five LLMs in
parallel and shave four bar-cycle latencies down to one. With a 60-
second poll interval and ~3 seconds per gpt-5 call, sequential is fine
but tight; parallel is comfortable.

We use a thread pool (not asyncio) for two reasons:

    1. The OpenAI sync client is the documented, stable surface; the
       async client is fine but gains us little here because each call
       is bound by network round-trip, not by CPU.
    2. Engine.step() is synchronous; mixing asyncio into a sync
       codebase is a source of bugs that doesn't pay back.

Public API
----------
    results = run_brains_parallel({
        "chartmind":   lambda: chart_wrapper.analyse(...),
        "newsmind":    lambda: news_wrapper.analyse(...),
        ...
    }, max_workers=5, timeout_seconds=45)

Each callable should return whatever the per-brain wrapper returns
(typically `LLMResponse` or a brain-specific dataclass). Failures are
captured into the result dict as exceptions; the caller decides
whether to fall back per brain.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable


def run_brains_parallel(callables: dict[str, Callable[[], Any]],
                        *,
                        max_workers: int = 5,
                        timeout_seconds: float = 45.0,
                        ) -> dict[str, Any]:
    """Run several brain callables concurrently and return their results.

    Returns a dict keyed by the same names as `callables` whose values
    are either the callable's return value or an Exception instance if
    it raised / timed out. Callers should `isinstance(v, Exception)`
    check before consuming.
    """
    if not callables:
        return {}

    out: dict[str, Any] = {name: None for name in callables}
    workers = min(max_workers, len(callables))
    with ThreadPoolExecutor(max_workers=workers,
                            thread_name_prefix="brain_llm") as pool:
        future_to_name = {
            pool.submit(_safe_call, fn): name
            for name, fn in callables.items()
        }
        for fut in as_completed(future_to_name, timeout=timeout_seconds):
            name = future_to_name[fut]
            try:
                out[name] = fut.result(timeout=0)
            except Exception as e:    # noqa: BLE001
                out[name] = e
    return out


def _safe_call(fn: Callable[[], Any]) -> Any:
    """Wrap the callable so its exceptions become return values that
    `as_completed` surfaces in `.result()`. Without this any raise
    inside one brain's worker thread would hide behind a
    BrokenThreadPool error.
    """
    try:
        return fn()
    except Exception as e:    # noqa: BLE001
        return e
