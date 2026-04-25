#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_multi_pair_backtest.py — backtest the system on 3 major FX pairs.

Pairs tested
------------
    EUR/USD — most liquid pair, tightest spreads, base case
    USD/JPY — different pip definition (0.01), different volatility
              regime (often ranges with sharp BoJ-driven moves)
    GBP/USD — higher volatility, wider spreads, more news-sensitive

Why test multiple pairs
-----------------------
A robust trading system should perform reasonably on multiple major
pairs, not just the one it was implicitly tuned on. If EUR/USD shows
+150% but USD/JPY shows -30%, the system is overfit to EUR/USD
microstructure (Lopez de Prado, *AFML* ch.11 — the "single-asset
overfitting trap").

Per-pair pip definitions
------------------------
    EUR/USD: 1 pip = 0.0001    (4 decimal price)
    GBP/USD: 1 pip = 0.0001    (4 decimal price)
    USD/JPY: 1 pip = 0.01      (2 decimal price)

The backtest's CostModel and RiskManager are pip-aware via
BacktestConfig.pair_pip — we just supply the right value per pair.

Output
------
For each pair:
    /app/NewsMind/state/backtest/<pair_slug>/results.json
    /app/NewsMind/state/backtest/<pair_slug>/report.txt

Plus a comparison table printed to stdout at the end.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Make project importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ----------------------------------------------------------------------
# Per-pair config table.
# ----------------------------------------------------------------------
@dataclass
class PairSpec:
    """Per-pair constants the backtest needs."""
    pair: str                    # canonical name with slash
    pair_pip: float              # 0.0001 or 0.01
    pip_value_per_lot: float     # USD per pip per std lot
    fallback_spread_pips: float  # used when bid/ask unavailable
    synthetic_start_price: float # only for synthetic mode
    synthetic_vol: float         # annualised volatility for synthetic


PAIRS: list[PairSpec] = [
    PairSpec(
        pair="EUR/USD",
        pair_pip=0.0001,
        pip_value_per_lot=10.0,
        fallback_spread_pips=0.5,
        synthetic_start_price=1.0850,
        synthetic_vol=0.07,
    ),
    PairSpec(
        pair="USD/JPY",
        pair_pip=0.01,
        # Pip value depends on USD/JPY rate; ~$6.6 at 150.0
        # We use 6.66 as the standard at ~150 (1000/150 = 6.66).
        pip_value_per_lot=6.66,
        fallback_spread_pips=0.8,        # JPY pairs typically wider
        synthetic_start_price=150.50,
        synthetic_vol=0.08,
    ),
    PairSpec(
        pair="GBP/USD",
        pair_pip=0.0001,
        pip_value_per_lot=10.0,
        fallback_spread_pips=0.9,        # GBP/USD wider than EUR/USD
        synthetic_start_price=1.2650,
        synthetic_vol=0.09,              # higher vol than EUR/USD
    ),
]


# ----------------------------------------------------------------------
# Output paths.
# ----------------------------------------------------------------------
OUTPUT_ROOT = Path(os.environ.get(
    "BACKTEST_OUTPUT_DIR",
    str(Path.home() / "newsmind_backtest")
))
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------
def _slug(pair: str) -> str:
    return pair.replace("/", "_").lower()


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def banner(text: str) -> None:
    sep = "=" * 70
    print()
    print(sep)
    print(f" {text}")
    print(sep)


# ----------------------------------------------------------------------
# Per-pair backtest.
# ----------------------------------------------------------------------
def run_one_pair(spec: PairSpec, *,
                 start_utc: datetime, end_utc: datetime,
                 use_synthetic: bool = False,
                 oanda_client=None) -> Optional[dict]:
    """Run the backtest on one pair. Return summary dict or None."""
    from Backtest import (
        BacktestConfig, BacktestData, BacktestRunner, BacktestAnalyzer,
    )
    from SmartNoteBook import SmartNoteBook, SmartNoteBookConfig
    from ChartMind import ChartMind

    pair_dir = OUTPUT_ROOT / _slug(spec.pair)
    pair_dir.mkdir(parents=True, exist_ok=True)

    config = BacktestConfig(
        pair=spec.pair,
        starting_equity=10_000.0,
        risk_per_trade_pct=0.5,
        pair_pip=spec.pair_pip,
        pip_value_per_lot=spec.pip_value_per_lot,
        fallback_spread_pips=spec.fallback_spread_pips,
        output_dir=str(pair_dir),
    )

    # ---- bars -----------------------------------------------------
    if use_synthetic:
        log(f"[{spec.pair}] generating synthetic bars...")
        bars = BacktestData.synthesize(
            start=start_utc, end=end_utc,
            pair=spec.pair, seed=42,
            start_price=spec.synthetic_start_price,
            annualised_volatility=spec.synthetic_vol,
            spread_pips_mean=spec.fallback_spread_pips,
        )
    else:
        log(f"[{spec.pair}] loading OANDA bars...")
        cache_path = pair_dir / "m15_cache.jsonl"
        data = BacktestData(
            client=oanda_client, cache_path=str(cache_path),
            pair=spec.pair, granularity="M15",
        )
        bars = data.load(start=start_utc, end=end_utc)

    log(f"[{spec.pair}] bars: {len(bars)}")
    if len(bars) < 100:
        log(f"[{spec.pair}] insufficient bars — skipping")
        return None

    # ---- snb (fresh per pair) -------------------------------------
    import shutil
    snb_dir = pair_dir / "notebook"
    if snb_dir.exists():
        shutil.rmtree(snb_dir, ignore_errors=True)
    snb = SmartNoteBook(SmartNoteBookConfig(
        state_dir=str(snb_dir),
        pair=spec.pair,
    ))
    cm = ChartMind()

    # ---- run ------------------------------------------------------
    runner = BacktestRunner(config=config, chartmind=cm, snb=snb)
    log(f"[{spec.pair}] running backtest...")
    t0 = time.time()
    result = runner.run(bars)
    elapsed = time.time() - t0
    log(f"[{spec.pair}] done in {elapsed:.1f}s — {result.summary()}")

    # ---- analyze --------------------------------------------------
    analyzer = BacktestAnalyzer(result, oos_pct=30.0)
    report = analyzer.analyze()

    # ---- save -----------------------------------------------------
    with open(pair_dir / "results.json", "w") as f:
        json.dump({
            "pair": spec.pair,
            "config": result.config.to_dict(),
            "summary": {
                "n_trades": report.n_trades,
                "win_rate": report.win_rate,
                "expectancy_r": report.expectancy_r,
                "profit_factor": report.profit_factor,
                "sqn": report.sqn,
                "max_dd_pct": report.max_dd_pct,
                "total_return_pct": report.total_return_pct,
                "annualised_return_pct": report.annualised_return_pct,
                "starting_equity": report.starting_equity,
                "final_equity": report.final_equity,
                "halted_early": result.halted_early,
                "halt_reason": result.halt_reason,
            },
            "in_sample": report.in_sample,
            "out_of_sample": report.out_of_sample,
            "monthly": report.monthly,
            "by_setup": report.by_setup,
            "by_hour": report.by_hour,
        }, f, indent=2, ensure_ascii=False, default=str)

    with open(pair_dir / "report.txt", "w") as f:
        f.write(analyzer.text_report(report))

    return {
        "pair": spec.pair,
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
    }


# ----------------------------------------------------------------------
# Comparison table.
# ----------------------------------------------------------------------
def print_comparison(summaries: list[dict]) -> None:
    banner(" MULTI-PAIR COMPARISON ")
    print(f"{'Pair':10s} | {'N':>5s} | {'WR':>6s} | {'E (R)':>7s} | "
          f"{'PF':>5s} | {'SQN':>6s} | {'MaxDD':>7s} | "
          f"{'Return':>9s} | {'Annual':>9s} | OOS check")
    print("-" * 110)
    for s in summaries:
        if s is None:
            continue
        oos_warn = ""
        if s["is_expectancy"] > 0 > s["oos_expectancy"]:
            oos_warn = "  ⚠ OVERFIT"
        elif s["oos_expectancy"] > 0:
            oos_warn = "  ✓ holds"
        else:
            oos_warn = "  - neutral"
        halt = " [HALTED]" if s["halted_early"] else ""
        print(
            f"{s['pair']:10s} | "
            f"{s['n_trades']:>5d} | "
            f"{s['win_rate']*100:>5.1f}% | "
            f"{s['expectancy_r']:>+6.3f} | "
            f"{s['profit_factor']:>5.2f} | "
            f"{s['sqn']:>6.2f} | "
            f"{s['max_dd_pct']:>6.2f}% | "
            f"{s['total_return_pct']:>+8.2f}% | "
            f"{s['annualised_return_pct']:>+8.2f}% |{oos_warn}{halt}"
        )


# ----------------------------------------------------------------------
# Main.
# ----------------------------------------------------------------------
def main() -> int:
    banner(" MULTI-PAIR BACKTEST — EUR/USD + USD/JPY + GBP/USD ")
    log(f"Output root: {OUTPUT_ROOT}")

    # 2 years ending today
    end_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
    start_utc = end_utc - timedelta(days=730)

    # Decide data source
    use_synthetic = False
    oanda_client = None
    if os.environ.get("USE_SYNTHETIC", "").lower() in ("1", "true", "yes"):
        use_synthetic = True
        log("Mode: SYNTHETIC (USE_SYNTHETIC=true)")
        # Synthetic mode: 6 months is enough to validate architecture
        start_utc = end_utc - timedelta(days=180)
    else:
        try:
            from OandaAdapter import OandaClient
            oanda_client = OandaClient()
            log(f"Mode: OANDA real data (account={oanda_client.account_id})")
        except Exception as e:
            log(f"OANDA unavailable ({e}); falling back to synthetic")
            use_synthetic = True
            start_utc = end_utc - timedelta(days=180)

    summaries: list[dict] = []
    for spec in PAIRS:
        try:
            s = run_one_pair(
                spec,
                start_utc=start_utc, end_utc=end_utc,
                use_synthetic=use_synthetic,
                oanda_client=oanda_client,
            )
            if s is not None:
                summaries.append(s)
        except Exception as e:
            log(f"[{spec.pair}] FAILED: {e}")
            traceback.print_exc()

    if not summaries:
        log("ERROR: no pair completed successfully.")
        return 1

    print_comparison(summaries)

    # Save combined comparison
    comp_path = OUTPUT_ROOT / "comparison.json"
    with open(comp_path, "w") as f:
        json.dump(summaries, f, indent=2, default=str)
    log(f"\nWrote {comp_path}")

    # Final verdict
    banner(" VERDICT ")
    profitable = [s for s in summaries if s["total_return_pct"] > 0]
    consistent = [
        s for s in summaries
        if s["is_expectancy"] > 0 and s["oos_expectancy"] > 0
    ]
    print(f"  Profitable on {len(profitable)}/{len(summaries)} pairs")
    print(f"  IS+OOS consistent on {len(consistent)}/{len(summaries)} pairs")
    above_150 = [s for s in summaries if s["total_return_pct"] > 150]
    if above_150:
        names = ", ".join(s["pair"] for s in above_150)
        print(f"  EXCEEDS 150% target on: {names}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(3)
