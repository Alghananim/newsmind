#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""walk_forward.py — rolling out-of-sample validation across the
2-year OANDA window.

Method
------
Split the 2-year OANDA bar stream into N consecutive 90-day windows
(quarters). For each window:
    1. Run the variant on this quarter's bars.
    2. Record return %, expectancy, halt count, by-grade.

Then the aggregator decides if the variant survived REGIME shifts:
    - Profitable in >= 60% of quarters
    - Mean expectancy positive across quarters
    - Worst quarter return better than -15% (recoverable)
    - Variant is robust if ALL three pass.

Inputs (env vars)
-----------------
    VARIANT_NAME     — name from Backtest.variants.VARIANTS
    PAIR             — "EUR/USD" | "USD/JPY" | "GBP/USD"
    OANDA_API_TOKEN  — OANDA practice token
    OANDA_ACCOUNT_ID — practice account
    OUTPUT_DIR       — where to write results
    QUARTER_DAYS     — window length (default 90)

Outputs
-------
    <OUTPUT_DIR>/wf__<variant>__<pair>.json   — per-quarter results
"""
from __future__ import annotations

import json, os, sys, time, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Backtest import (BacktestConfig, BacktestData, BacktestRunner,
                      BacktestAnalyzer)
from Backtest.variants import get_variant
from SmartNoteBook import SmartNoteBook, SmartNoteBookConfig
from ChartMind import ChartMind


PAIR_PARAMS = {
    "EUR/USD": dict(pair_pip=0.0001, pip_value_per_lot=10.0,
                    fallback_spread_pips=0.5),
    "USD/JPY": dict(pair_pip=0.01,   pip_value_per_lot=6.66,
                    fallback_spread_pips=0.8),
    "GBP/USD": dict(pair_pip=0.0001, pip_value_per_lot=10.0,
                    fallback_spread_pips=0.9),
}


def slug(s): return s.replace("/", "_").lower()
def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}", flush=True)


def main() -> int:
    variant_name = os.environ.get("VARIANT_NAME", "baseline").strip()
    pair = os.environ.get("PAIR", "EUR/USD").strip()
    quarter_days = int(os.environ.get("QUARTER_DAYS", "90"))
    output_dir = Path(os.environ.get("OUTPUT_DIR", "wf_output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    spec = PAIR_PARAMS[pair]
    variant = get_variant(variant_name)
    log(f"walk-forward: variant={variant.name} pair={pair} window={quarter_days}d")

    end_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start_utc = end_utc - timedelta(days=730)

    pair_dir = output_dir / f"wf__{variant.name}__{slug(pair)}"
    pair_dir.mkdir(parents=True, exist_ok=True)

    # ---- fetch full 2 years once
    from OandaAdapter import OandaClient
    client = OandaClient()
    cache = pair_dir / "m15_cache.jsonl"
    data = BacktestData(client=client, cache_path=str(cache),
                        pair=pair, granularity="M15")
    all_bars = data.load(start=start_utc, end=end_utc)
    log(f"loaded {len(all_bars)} bars [{all_bars[0].time.date()} .. "
        f"{all_bars[-1].time.date()}]")

    # ---- iterate quarter windows
    quarters = []
    cur = start_utc
    q = 0
    while cur + timedelta(days=quarter_days) <= end_utc:
        q += 1
        wend = cur + timedelta(days=quarter_days)
        win_bars = [b for b in all_bars if cur <= b.time <= wend]
        if len(win_bars) < 100:
            log(f"Q{q}: insufficient bars ({len(win_bars)}); skip")
            cur = wend
            continue

        cfg = BacktestConfig(
            pair=pair, starting_equity=10_000.0,
            risk_per_trade_pct=0.5,
            pair_pip=spec["pair_pip"],
            pip_value_per_lot=spec["pip_value_per_lot"],
            fallback_spread_pips=spec["fallback_spread_pips"],
            output_dir=str(pair_dir / f"q{q}"),
        )
        snb = SmartNoteBook(SmartNoteBookConfig(
            state_dir=str(pair_dir / f"q{q}" / "snb"), pair=pair))
        # Pick ChartMind v1 vs v2 based on variant flag
    if getattr(variant, "use_chartmind_v2", False):
        from ChartMindV2 import ChartMindV2
        cm = ChartMindV2(
            pair_pip=spec["pair_pip"],
            min_grade=variant.v2_min_grade,
            min_confluence=variant.v2_min_confluence,
        )
    else:
        cm = ChartMind()
        runner = BacktestRunner(config=cfg, chartmind=cm, snb=snb,
                                variant_filter=variant)
        log(f"Q{q} [{cur.date()} .. {wend.date()}]: {len(win_bars)} bars")
        t0 = time.time()
        result = runner.run(win_bars)
        elapsed = time.time() - t0
        report = BacktestAnalyzer(result, oos_pct=30.0).analyze()
        ret_pct = (result.final_equity / result.starting_equity - 1) * 100
        quarters.append({
            "quarter": q,
            "start": cur.date().isoformat(),
            "end": wend.date().isoformat(),
            "bars": len(win_bars),
            "n_trades": report.n_trades,
            "win_rate": report.win_rate,
            "expectancy_r": report.expectancy_r,
            "profit_factor": report.profit_factor,
            "sqn": report.sqn,
            "max_dd_pct": report.max_dd_pct,
            "total_return_pct": ret_pct,
            "halted_early": result.halted_early,
            "halt_count": runner.halt_count,
            "rej_atr_surge": runner.rej_atr_surge,
            "by_grade": report.by_grade,
        })
        log(f"Q{q} done in {elapsed:.1f}s — n={report.n_trades} "
            f"E={report.expectancy_r:+.3f}R ret={ret_pct:+.2f}% "
            f"halts={runner.halt_count}")
        cur = wend

    # ---- summary
    profitable = sum(1 for q in quarters if q["total_return_pct"] > 0)
    mean_e = (sum(q["expectancy_r"] for q in quarters) / len(quarters)
              if quarters else 0)
    worst = min((q["total_return_pct"] for q in quarters), default=0)
    robust = (profitable / len(quarters) >= 0.6
              and mean_e > 0 and worst > -15.0) if quarters else False

    summary = {
        "variant": variant.name,
        "pair": pair,
        "n_quarters": len(quarters),
        "profitable_quarters": profitable,
        "mean_expectancy_r": mean_e,
        "worst_quarter_return_pct": worst,
        "robust": robust,
        "quarters": quarters,
    }
    out_file = output_dir / f"wf__{variant.name}__{slug(pair)}.json"
    out_file.write_text(json.dumps(summary, indent=2, default=str))

    log(f"\n  RESULT: {variant.name} on {pair}")
    log(f"  profitable quarters: {profitable}/{len(quarters)}")
    log(f"  mean expectancy:     {mean_e:+.3f}R")
    log(f"  worst quarter:       {worst:+.2f}%")
    log(f"  ROBUST: {'YES' if robust else 'NO'}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
