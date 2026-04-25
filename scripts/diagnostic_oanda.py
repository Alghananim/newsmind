#!/usr/bin/env python3
"""diagnostic_oanda.py — exhaustive isolation tests on REAL OANDA data.

Per pair, runs N targeted variants and records:
  * baseline (no variant)
  * by_setup analysis (which patterns make money)
  * by_hour analysis
  * by_grade analysis
  * RR sweep (1.5, 2.0, 2.5, 3.0)
  * Setup blacklist tests (drop biggest losers)

Output: one small JSON per pair with the full diagnostic table.
Final aggregator builds the LOCKED production config from the data.
"""
from __future__ import annotations
import json, os, sys, time, traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Backtest import (BacktestConfig, BacktestData, BacktestRunner,
                      BacktestAnalyzer)
from Backtest.variants import VariantFilter
from SmartNoteBook import SmartNoteBook, SmartNoteBookConfig
from ChartMind import ChartMind


PAIR_PARAMS = {
    "EUR/USD": dict(pair_pip=0.0001, pip_value_per_lot=10.0, fb=0.5),
    "USD/JPY": dict(pair_pip=0.01,   pip_value_per_lot=6.66, fb=0.8),
    "GBP/USD": dict(pair_pip=0.0001, pip_value_per_lot=10.0, fb=0.9),
}


def slug(s): return s.replace("/", "_").lower()
def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}",
          flush=True)


def run_one(pair: str, name: str, variant: VariantFilter, oanda_client,
            cache_dir: Path, days: int = 730):
    """Run ONE backtest, return summary dict."""
    spec = PAIR_PARAMS[pair]
    end_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start_utc = end_utc - timedelta(days=days)

    cache_path = cache_dir / f"{slug(pair)}_m15.jsonl"
    data = BacktestData(client=oanda_client, cache_path=str(cache_path),
                        pair=pair, granularity="M15")
    bars = data.load(start=start_utc, end=end_utc)
    if len(bars) < 100:
        return None

    out_dir = cache_dir / f"diag_{name}_{slug(pair)}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cfg = BacktestConfig(
        pair=pair, starting_equity=10_000.0, risk_per_trade_pct=0.5,
        pair_pip=spec["pair_pip"], pip_value_per_lot=spec["pip_value_per_lot"],
        fallback_spread_pips=spec["fb"], output_dir=str(out_dir),
    )
    snb = SmartNoteBook(SmartNoteBookConfig(
        state_dir=str(out_dir / "snb"), pair=pair))
    cm = ChartMind()
    runner = BacktestRunner(config=cfg, chartmind=cm, snb=snb,
                            variant_filter=variant)
    t0 = time.time()
    res = runner.run(bars)
    elapsed = time.time() - t0
    rep = BacktestAnalyzer(res, oos_pct=30.0).analyze()
    summ = {
        "pair": pair, "test": name, "variant": variant.name,
        "n_trades": rep.n_trades, "win_rate": rep.win_rate,
        "expectancy_r": rep.expectancy_r, "profit_factor": rep.profit_factor,
        "sqn": rep.sqn, "max_dd_pct": rep.max_dd_pct,
        "total_return_pct": rep.total_return_pct,
        "halted_early": res.halted_early,
        "halt_count": runner.halt_count,
        "is_e": rep.in_sample.get("expectancy_r", 0),
        "oos_e": rep.out_of_sample.get("expectancy_r", 0),
        "rej_variant": runner.rej_variant,
        "by_setup": {k: {"n": v["n"], "wr": v["win_rate"],
                         "e": v["expectancy_r"], "pf": v.get("profit_factor", 0)}
                     for k, v in rep.by_setup.items()},
        "by_hour": {k: {"n": v["n"], "wr": v["win_rate"], "e": v["expectancy_r"]}
                    for k, v in rep.by_hour.items()},
        "by_grade": {k: {"n": v["n"], "wr": v["win_rate"],
                         "e": v["expectancy_r"], "pf": v.get("profit_factor", 0)}
                     for k, v in rep.by_grade.items()},
        "elapsed_s": round(elapsed, 1),
    }
    log(f"  [{elapsed:>5.1f}s] {name:36s} | n={rep.n_trades:>4d}  "
        f"WR={rep.win_rate*100:>4.1f}%  E={rep.expectancy_r:>+6.3f}R  "
        f"PF={rep.profit_factor:>5.2f}  ret={rep.total_return_pct:>+7.2f}%  "
        f"halt={'Y' if res.halted_early else 'N'}")
    return summ


# ---- Tests catalog -----------------------------------------------------
TESTS = [
    # T1: pure baseline
    ("T01_baseline", VariantFilter(name="diag_baseline")),

    # T2: kill_asia (block 0-7 UTC) — already known to help
    ("T02_kill_asia",
     VariantFilter(name="diag_kill_asia", blocked_hours_utc=(0,1,2,3,4,5,6,7))),

    # T3: drop biggest known losers
    ("T03_drop_doubles",
     VariantFilter(name="diag_drop_doubles",
                   blocked_setups=("pattern_double_bottom", "pattern_double_top",
                                   "two_legged_pullback"))),

    # T4: continuation only
    ("T04_continuation_only",
     VariantFilter(name="diag_cont",
                   allowed_setups=("signal_entry_continuation",))),

    # T5: kill_asia + drop_doubles (combined)
    ("T05_kill_asia_drop_doubles",
     VariantFilter(name="diag_combo",
                   blocked_hours_utc=(0,1,2,3,4,5,6,7),
                   blocked_setups=("pattern_double_bottom", "pattern_double_top",
                                   "two_legged_pullback"),
                   halt_pause_days=7)),

    # T6: regime trending only
    ("T06_regime_trending",
     VariantFilter(name="diag_regime",
                   blocked_hours_utc=(0,1,2,3,4,5,6,7),
                   allowed_regimes=("TRENDING_UP", "TRENDING_DOWN"),
                   min_adx=25.0,
                   halt_pause_days=7)),

    # T7: high-confidence only
    ("T07_high_conf",
     VariantFilter(name="diag_conf",
                   blocked_hours_utc=(0,1,2,3,4,5,6,7),
                   min_confidence=0.55,
                   halt_pause_days=7)),

    # T8: tight RR
    ("T08_min_rr_15",
     VariantFilter(name="diag_rr15",
                   blocked_hours_utc=(0,1,2,3,4,5,6,7),
                   min_rr=1.5)),

    # T9: high RR
    ("T09_min_rr_30",
     VariantFilter(name="diag_rr30",
                   blocked_hours_utc=(0,1,2,3,4,5,6,7),
                   min_rr=3.0)),

    # T10: surgical — no_halt + drop losers + kill_asia + regime
    ("T10_surgical",
     VariantFilter(name="diag_surgical",
                   blocked_hours_utc=(0,1,2,3,4,5,6,7),
                   blocked_setups=("pattern_double_bottom", "pattern_double_top"),
                   allowed_regimes=("TRENDING_UP", "TRENDING_DOWN"),
                   min_adx=22.0,
                   min_rr=2.0,
                   halt_pause_days=7,
                   atr_surge_threshold=2.0)),

    # T11: PRODUCTION_SAFE — all 12 audit-driven commandments applied
    ("T11_production_safe", get_variant("production_safe")),

    # T12: PRODUCTION_STRICT — A grade only, conservative caps
    ("T12_production_strict", get_variant("production_strict")),
]


def main() -> int:
    pair = os.environ.get("PAIR", "EUR/USD").strip()
    out_root = Path(os.environ.get("OUTPUT_DIR", "diag_output"))
    out_root.mkdir(parents=True, exist_ok=True)
    cache_dir = out_root
    cache_dir.mkdir(parents=True, exist_ok=True)

    log(f"=== DIAGNOSTIC ON REAL OANDA: {pair} ===")
    from OandaAdapter import OandaClient
    client = OandaClient()

    results = []
    for name, variant in TESTS:
        try:
            r = run_one(pair, name, variant, client, cache_dir)
            if r:
                results.append(r)
        except Exception as e:
            log(f"  FAILED {name}: {e}")
            traceback.print_exc()

    out_file = out_root / f"diagnostic_{slug(pair)}.json"
    out_file.write_text(json.dumps(results, indent=2, default=str))
    log(f"\nWrote {out_file}")

    # Print verdict
    log("\n=== RANKED BY TOTAL RETURN ===")
    for r in sorted(results, key=lambda x: -x["total_return_pct"]):
        log(f"  {r['test']:30s} ret={r['total_return_pct']:>+7.2f}%  "
            f"E={r['expectancy_r']:>+6.3f}R  n={r['n_trades']:>4d}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
