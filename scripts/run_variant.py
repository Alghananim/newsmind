#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_variant.py — run ONE variant on ONE pair, write a summary JSON.

Designed for GitHub Actions matrix execution: one job per
(variant, pair) pair = parallelism. Each job writes its own
small JSON; a final aggregator step in the workflow combines them.

Inputs (env vars)
-----------------
    VARIANT_NAME       — key into Backtest.variants.VARIANTS
    PAIR               — "EUR/USD" | "USD/JPY" | "GBP/USD"
    DAYS               — backtest window in days (default 730)
    USE_SYNTHETIC      — true to skip OANDA (offline test)
    OANDA_API_TOKEN    — required if USE_SYNTHETIC != true
    OANDA_ACCOUNT_ID   — required if USE_SYNTHETIC != true
    OANDA_ENVIRONMENT  — practice|live (default practice)
    OUTPUT_DIR         — where to write the per-job JSON

Output
------
    <OUTPUT_DIR>/<variant>__<pair_slug>.json
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Backtest import (BacktestConfig, BacktestData,
                      BacktestRunner, BacktestAnalyzer)
from Backtest.variants import get_variant
from SmartNoteBook import SmartNoteBook, SmartNoteBookConfig
from ChartMind import ChartMind


PAIR_PARAMS = {
    "EUR/USD": dict(pair_pip=0.0001, pip_value_per_lot=10.0,  fallback_spread_pips=0.5,
                    synth_start=1.0850, synth_vol=0.07),
    "USD/JPY": dict(pair_pip=0.01,   pip_value_per_lot=6.66,  fallback_spread_pips=0.8,
                    synth_start=150.50, synth_vol=0.08),
    "GBP/USD": dict(pair_pip=0.0001, pip_value_per_lot=10.0,  fallback_spread_pips=0.9,
                    synth_start=1.2650, synth_vol=0.09),
}


def slug(s: str) -> str:
    return s.replace("/", "_").lower()


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}",
          flush=True)


def main() -> int:
    variant_name = os.environ.get("VARIANT_NAME", "baseline").strip()
    pair = os.environ.get("PAIR", "EUR/USD").strip()
    days = int(os.environ.get("DAYS", "730"))
    use_synthetic = os.environ.get("USE_SYNTHETIC", "").lower() in ("1", "true", "yes")
    output_dir = Path(os.environ.get("OUTPUT_DIR", "variant_output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    if pair not in PAIR_PARAMS:
        log(f"ERROR: unknown pair {pair}")
        return 2

    spec = PAIR_PARAMS[pair]
    variant = get_variant(variant_name)
    log(f"variant={variant.name} pair={pair} days={days} synthetic={use_synthetic}")
    log(f"variant detail: hours_allow={variant.allowed_hours_utc} "
        f"hours_block={variant.blocked_hours_utc} "
        f"setups_allow={variant.allowed_setups} "
        f"setups_block={variant.blocked_setups} "
        f"min_conf={variant.min_confidence} min_rr={variant.min_rr} "
        f"halt_off={variant.disable_max_dd_halt}")

    end_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start_utc = end_utc - timedelta(days=days)

    pair_dir = output_dir / f"{variant.name}__{slug(pair)}"
    pair_dir.mkdir(parents=True, exist_ok=True)

    config = BacktestConfig(
        pair=pair,
        starting_equity=10_000.0,
        risk_per_trade_pct=0.5,
        pair_pip=spec["pair_pip"],
        pip_value_per_lot=spec["pip_value_per_lot"],
        fallback_spread_pips=spec["fallback_spread_pips"],
        output_dir=str(pair_dir),
    )

    # ---- bars
    if use_synthetic:
        log("generating synthetic bars...")
        bars = BacktestData.synthesize(
            start=start_utc, end=end_utc, pair=pair, seed=42,
            start_price=spec["synth_start"],
            annualised_volatility=spec["synth_vol"],
            spread_pips_mean=spec["fallback_spread_pips"],
        )
    else:
        from OandaAdapter import OandaClient
        client = OandaClient()
        cache = pair_dir / "m15_cache.jsonl"
        data = BacktestData(client=client, cache_path=str(cache),
                            pair=pair, granularity="M15")
        bars = data.load(start=start_utc, end=end_utc)

    log(f"bars: {len(bars)}")
    if len(bars) < 100:
        log("insufficient bars — abort")
        return 3

    # ---- snb (ephemeral)
    snb = SmartNoteBook(SmartNoteBookConfig(
        state_dir=str(pair_dir / "notebook"), pair=pair,
    ))
    cm = ChartMind()

    runner = BacktestRunner(
        config=config, chartmind=cm, snb=snb,
        variant_filter=variant,
    )

    log("running...")
    t0 = time.time()
    result = runner.run(bars)
    elapsed = time.time() - t0
    log(f"done in {elapsed:.1f}s — {result.summary()}")
    log(f"variant rejections: {runner.rej_variant}  "
        f"signals: {result.signals_generated}  "
        f"entries: {result.entries_filled}")

    analyzer = BacktestAnalyzer(result, oos_pct=30.0)
    report = analyzer.analyze()

    summary = {
        "variant": variant.name,
        "pair": pair,
        "n_trades": report.n_trades,
        "win_rate": report.win_rate,
        "expectancy_r": report.expectancy_r,
        "profit_factor": report.profit_factor,
        "sqn": report.sqn,
        "max_dd_pct": report.max_dd_pct,
        "total_return_pct": report.total_return_pct,
        "annualised_return_pct": report.annualised_return_pct,
        "halted_early": result.halted_early,
        "halt_reason": result.halt_reason,
        "is_expectancy": report.in_sample.get("expectancy_r", 0),
        "oos_expectancy": report.out_of_sample.get("expectancy_r", 0),
        "is_n": report.in_sample.get("n_trades", 0),
        "oos_n": report.out_of_sample.get("n_trades", 0),
        "rej_variant": runner.rej_variant,
        "signals_generated": result.signals_generated,
        "entries_filled": result.entries_filled,
        "rej_session": result.entries_rejected_by_session,
        "rej_calendar": result.entries_rejected_by_calendar,
        "rej_risk": result.entries_rejected_by_risk,
        "monthly": report.monthly,
        "by_setup": report.by_setup,
        "by_hour": report.by_hour,
    }

    # Write the per-job summary
    out_file = output_dir / f"{variant.name}__{slug(pair)}.json"
    out_file.write_text(json.dumps(summary, indent=2, default=str))
    log(f"wrote {out_file}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
