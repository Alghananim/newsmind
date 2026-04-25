# -*- coding: utf-8 -*-
"""BacktestData — historical bar loader for the EUR/USD backtest.

Responsibilities
----------------
    1. Fetch 2 years of M15 EUR/USD bars from OANDA (with bid + ask
       so the cost model can compute real spreads).
    2. Cache to disk as JSON so the second run is instant and the
       backtest is reproducible offline.
    3. Handle OANDA's 5000-candle pagination limit transparently —
       2 years of M15 ≈ 50,000 bars ≈ 10 API calls.
    4. Filter out duplicate/empty bars and weekend gaps.
    5. Provide a clean iterable for the runner: bars in chronological
       order, each one carrying the fields the system needs.

OANDA's bid/ask request
-----------------------
The candles endpoint accepts `price=BAM` to return Bid + Ask + Mid in
one call. We use that everywhere — mid for ChartMind's analysis, bid
& ask for the cost model's spread computation.

Cache format
------------
JSONL on disk: one line per bar with fields
    {time, mid_o, mid_h, mid_l, mid_c, bid_o, bid_c, ask_o, ask_c,
     spread_pips, volume, complete}

We don't bother gzipping — 50,000 bars of JSONL is ~10 MB, trivial.

Reasoning canon
---------------
    * Marcos Lopez de Prado — *AFML* ch.4: cached data is the only
      data that lets you re-run an experiment. Network-dependent
      backtests are not backtests.
    * Robert Carver — *Systematic Trading*: the cost of bad data is
      worse than the cost of no data, because no data forces caution
      while bad data invites overconfidence.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional


# ----------------------------------------------------------------------
# Bar dataclass for the backtest.
# ----------------------------------------------------------------------
@dataclass
class BacktestBar:
    """One M15 bar with bid + mid + ask, ready for the runner.

    Duck-types as a ChartMind input via __getitem__.
    """
    time: datetime           # UTC, bar CLOSE time
    open: float              # mid open
    high: float              # mid high
    low: float               # mid low
    close: float             # mid close
    bid_open: float
    bid_close: float
    ask_open: float
    ask_close: float
    spread_pips: float       # at close
    volume: int

    pair: str = "EUR/USD"
    granularity: str = "M15"

    def __getitem__(self, key):
        return getattr(self, key)

    def __contains__(self, key):
        return hasattr(self, key)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["time"] = self.time.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BacktestBar":
        d2 = dict(d)
        if isinstance(d2.get("time"), str):
            d2["time"] = datetime.fromisoformat(d2["time"])
        return cls(**d2)


# ----------------------------------------------------------------------
# The data loader.
# ----------------------------------------------------------------------
class BacktestData:
    """Fetch + cache + iterate historical bars.

    Construction:
        data = BacktestData(
            client=oanda_client,        # OandaClient or None
            cache_path="state/backtest/eurusd_m15.jsonl",
            pair="EUR/USD",
            granularity="M15",
        )
        bars = data.load(start=date_from, end=date_to)
        for bar in bars:
            ...
    """

    OANDA_PAGE_SIZE = 5000   # OANDA's hard cap

    def __init__(self, *,
                 client: Optional[object] = None,
                 cache_path: str = "/app/NewsMind/state/backtest/eurusd_m15.jsonl",
                 pair: str = "EUR/USD",
                 granularity: str = "M15"):
        self._client = client
        self._cache_path = Path(cache_path)
        self._pair = pair
        self._granularity = granularity
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)

    # ==================================================================
    # Public API.
    # ==================================================================
    def load(self,
             *,
             start: datetime,
             end: datetime,
             force_refresh: bool = False,
             ) -> list[BacktestBar]:
        """Return the bars in [start, end] (UTC), inclusive.

        Strategy:
            1. If cache exists and covers the range and `force_refresh
               is False`, read from cache.
            2. Otherwise fetch from OANDA in pages, append to cache,
               return.

        If `client is None` (no OANDA), returns whatever the cache has.
        """
        cached = self._read_cache()
        cached_range = self._range_covered(cached)
        need_fetch = (
            force_refresh
            or not cached
            or (cached_range[0] is None or cached_range[0] > start)
            or (cached_range[1] is None or cached_range[1] < end)
        )

        if need_fetch and self._client is not None:
            self._fetch_into_cache(start=start, end=end, existing=cached)
            cached = self._read_cache()

        # Filter to requested range
        out = [b for b in cached if start <= b.time <= end]
        return out

    def cache_summary(self) -> dict:
        """Return a small summary of what's on disk. Useful for the
        boot log so the operator can see at a glance what they have.
        """
        cached = self._read_cache()
        if not cached:
            return {"path": str(self._cache_path), "bars": 0}
        return {
            "path": str(self._cache_path),
            "bars": len(cached),
            "first_bar": cached[0].time.isoformat(),
            "last_bar": cached[-1].time.isoformat(),
            "size_bytes": (
                self._cache_path.stat().st_size
                if self._cache_path.exists() else 0
            ),
        }

    def iter_bars(self, *,
                  start: Optional[datetime] = None,
                  end: Optional[datetime] = None,
                  ) -> Iterator[BacktestBar]:
        """Stream bars without loading the whole list into memory.

        Reads the cache line-by-line; useful when the cache grows past
        a few hundred MB.
        """
        if not self._cache_path.exists():
            return
        with open(self._cache_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    bar = BacktestBar.from_dict(json.loads(line))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                if start is not None and bar.time < start:
                    continue
                if end is not None and bar.time > end:
                    continue
                yield bar

    # ==================================================================
    # Fetch via OANDA.
    # ==================================================================
    def _fetch_into_cache(self, *,
                          start: datetime,
                          end: datetime,
                          existing: list[BacktestBar],
                          ) -> None:
        """Fetch every bar in [start, end] from OANDA and merge into
        the on-disk cache. Idempotent: re-fetching does not duplicate.
        """
        from OandaAdapter.instruments import OandaInstruments, to_oanda_pair
        instruments = OandaInstruments(self._client)

        existing_times = {b.time for b in existing}
        merged: list[BacktestBar] = list(existing)

        # Page through 5000-bar windows
        cursor = start
        page = 0
        while cursor < end:
            page += 1
            # Each M15 page covers 5000 * 15 minutes = ~52 days
            window_end = min(cursor + timedelta(minutes=15 * self.OANDA_PAGE_SIZE), end)
            # Fetch BAM (bid + ask + mid)
            mid_candles = instruments.candles(
                pair=self._pair, granularity=self._granularity,
                from_time=cursor, to_time=window_end, price="M",
            )
            bid_candles = instruments.candles(
                pair=self._pair, granularity=self._granularity,
                from_time=cursor, to_time=window_end, price="B",
            )
            ask_candles = instruments.candles(
                pair=self._pair, granularity=self._granularity,
                from_time=cursor, to_time=window_end, price="A",
            )

            # Index by time for safe zip
            bid_by_t = {c.time: c for c in bid_candles}
            ask_by_t = {c.time: c for c in ask_candles}

            page_bars = 0
            for c in mid_candles:
                if not c.complete:
                    continue
                if c.time in existing_times:
                    continue
                bid = bid_by_t.get(c.time)
                ask = ask_by_t.get(c.time)
                if bid is None or ask is None:
                    continue
                # Pair-aware pip definition (0.01 for JPY pairs).
                pip_for_pair = 0.01 if "JPY" in self._pair.upper() else 0.0001
                spread_pips = (ask.close - bid.close) / pip_for_pair if (
                    ask.close > bid.close > 0
                ) else 0.5
                bar = BacktestBar(
                    time=c.time,
                    open=c.open, high=c.high, low=c.low, close=c.close,
                    bid_open=bid.open, bid_close=bid.close,
                    ask_open=ask.open, ask_close=ask.close,
                    spread_pips=spread_pips,
                    volume=c.volume,
                    pair=self._pair, granularity=self._granularity,
                )
                merged.append(bar)
                existing_times.add(bar.time)
                page_bars += 1

            if page_bars == 0:
                # Empty page = end of available history
                break
            cursor = window_end + timedelta(minutes=1)

        # Sort + dedupe + persist
        merged.sort(key=lambda b: b.time)
        deduped: list[BacktestBar] = []
        last_t = None
        for b in merged:
            if b.time != last_t:
                deduped.append(b)
                last_t = b.time
        self._write_cache(deduped)

    # ==================================================================
    # Cache I/O.
    # ==================================================================
    def _read_cache(self) -> list[BacktestBar]:
        if not self._cache_path.exists():
            return []
        out: list[BacktestBar] = []
        with open(self._cache_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(BacktestBar.from_dict(json.loads(line)))
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
        out.sort(key=lambda b: b.time)
        return out

    def _write_cache(self, bars: list[BacktestBar]) -> None:
        tmp = self._cache_path.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for b in bars:
                f.write(json.dumps(b.to_dict(), ensure_ascii=False,
                                   separators=(",", ":")) + "\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, self._cache_path)

    @staticmethod
    def _range_covered(bars: list[BacktestBar]) -> tuple[Optional[datetime], Optional[datetime]]:
        if not bars:
            return None, None
        return bars[0].time, bars[-1].time

    # ==================================================================
    # Synthetic data generator (for offline development / unit tests).
    # ==================================================================
    @staticmethod
    def synthesize(*,
                   start: datetime,
                   end: datetime,
                   pair: str = "EUR/USD",
                   seed: int = 42,
                   start_price: float = 1.0850,
                   annualised_volatility: float = 0.07,
                   spread_pips_mean: float = 0.5,
                   spread_pips_jitter: float = 0.3,
                   ) -> list[BacktestBar]:
        """Generate a synthetic but realistic M15 EUR/USD series.

        Used for unit tests of the runner when we don't want to hit
        OANDA. The series:
            * geometric Brownian motion (Bachelier-Black-Scholes),
            * scaled to ~7% annualised vol (typical EUR/USD recent),
            * spreads jittered around 0.5 pips,
            * weekends + 17:00-22:00 UTC Friday-to-Sunday gap omitted.

        Lopez de Prado (AFML ch.4): synthetic data is fine for unit
        testing the runner; it is NOT a substitute for real data when
        evaluating the strategy.
        """
        import math
        import random
        rng = random.Random(seed)

        # 15-minute step in years (252 trading days * 24 * 4 = 24,192 steps/year)
        dt_years = (15.0 / 60.0) / (24.0 * 252.0)
        sigma = annualised_volatility
        drift = 0.0           # zero-drift random walk (most realistic for FX)

        bars = []
        price = start_price
        cur = start
        while cur < end:
            # Skip weekend (Sat 00:00 UTC -> Sun 22:00 UTC inclusive)
            if cur.weekday() == 5:
                cur += timedelta(days=2)
                continue
            if cur.weekday() == 6 and cur.hour < 22:
                cur += timedelta(hours=1)
                continue

            # Random walk step
            z = rng.gauss(0, 1)
            log_ret = (drift - 0.5 * sigma * sigma) * dt_years + sigma * math.sqrt(dt_years) * z
            new_price = price * math.exp(log_ret)
            o = price
            c = new_price
            high_z = abs(rng.gauss(0, 0.5))
            low_z = abs(rng.gauss(0, 0.5))
            h = max(o, c) + high_z * sigma * math.sqrt(dt_years) * o
            l = min(o, c) - low_z * sigma * math.sqrt(dt_years) * o

            spread_pips = max(0.1, rng.gauss(spread_pips_mean, spread_pips_jitter))
            half_spread = (spread_pips * 0.0001) / 2.0

            bars.append(BacktestBar(
                time=cur + timedelta(minutes=15),   # close time
                open=o, high=h, low=l, close=c,
                bid_open=o - half_spread, bid_close=c - half_spread,
                ask_open=o + half_spread, ask_close=c + half_spread,
                spread_pips=spread_pips,
                volume=int(rng.uniform(50, 250)),
                pair=pair, granularity="M15",
            ))
            price = c
            cur += timedelta(minutes=15)
        return bars
