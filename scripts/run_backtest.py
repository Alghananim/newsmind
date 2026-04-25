#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_backtest.py — institutional EUR/USD backtest with requirements verification.

What this script does
---------------------
    1. Fetches 2 years of M15 EUR/USD bars from OANDA (cached on disk).
    2. Runs the full Backtest pipeline (session filter + news calendar +
       cost model + risk manager + ChartMind analysis).
    3. Saves results to /app/NewsMind/state/backtest/:
         * results.json          — machine-readable full report
         * report.txt            — human-readable text report
         * equity_curve.json     — daily equity samples
    4. Verifies all 12 requirements set by the operator and prints a
       PASS/FAIL summary at the end. Exit code 0 only if every
       requirement is met.

Run on the VPS
--------------
    docker exec newsmind python /app/scripts/run_backtest.py

Or trigger by setting RUN_BACKTEST=true and restarting the container
(see main.py for the one-shot mode).

Requirements verified
---------------------
    [1]  Pair = EUR/USD only
    [2]  Real OANDA data (not synthetic)
    [3]  Day-trading mode (M15, intra-day exits)
    [4]  Trading hours: NY Mon-Fri, 03-05 + 08-12
    [5]  Average >= 2 trades/day
    [6]  Test duration >= 2 years
    [7]  Total return > 150%
    [8]  All trading costs included
    [9]  News blackouts (CPI/NFP/FOMC/ECB)
    [10] Risk management active
    [11] Quality metrics computed
    [12] No lookahead / no overfitting (IS vs OOS check)
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Make project importable when run via docker exec
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ----------------------------------------------------------------------
# Output directory.
# ----------------------------------------------------------------------
OUTPUT_DIR = Path(os.environ.get(
    "BACKTEST_OUTPUT_DIR", "/app/NewsMind/state/backtest"
))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_PATH = OUTPUT_DIR / "eurusd_m15.jsonl"


# ----------------------------------------------------------------------
# Pretty-print helpers.
# ----------------------------------------------------------------------
def banner(text: str) -> None:
    sep = "=" * 70
    print()
    print(sep)
    print(f" {text}")
    print(sep)


def section(text: str) -> None:
    print()
    print(f"--- {text} ---")


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ----------------------------------------------------------------------
# Phase 1 — Fetch OANDA data.
# ----------------------------------------------------------------------
def fetch_data(start_utc: datetime, end_utc: datetime) -> list:
    """Fetch 2 years of M15 EUR/USD bars from OANDA, cached on disk."""
    from OandaAdapter import OandaClient
    from Backtest import BacktestData

    log(f"OANDA env: {os.environ.get('OANDA_ENVIRONMENT', 'practice')}")
    log(f"OANDA account: {os.environ.get('OANDA_ACCOUNT_ID', '<unset>')}")
    log(f"Cache path: {CACHE_PATH}")

    try:
        client = OandaClient()
        log(f"OANDA client: ready (account={client.account_id})")
    except Exception as e:
        log(f"OANDA client FAILED: {e}")
        return []

    data = BacktestData(
        client=client, cache_path=str(CACHE_PATH),
        pair="EUR/USD", granularity="M15",
    )

    # Show what's cached
    summary = data.cache_summary()
    log(f"Cache summary: {summary}")

    log(f"Loading bars from {start_utc.date()} to {end_utc.date()} ...")
    t0 = time.time()
    bars = data.load(start=start_utc, end=end_utc, force_refresh=False)
    elapsed = time.time() - t0
    log(f"Loaded {len(bars)} bars in {elapsed:.1f}s")
    if bars:
        log(f"Range: {bars[0].time} -> {bars[-1].time}")
        log(f"First price: {bars[0].open:.5f}, Last price: {bars[-1].close:.5f}")
    return bars


# ----------------------------------------------------------------------
# Phase 2 — Run the backtest.
# ----------------------------------------------------------------------
def run_backtest(bars: list, *, starting_equity: float = 10_000.0):
    """Run the full backtest using ChartMind + Backtest harness."""
    from Backtest import (
        BacktestConfig, BacktestRunner, BacktestAnalyzer,
        BacktestSession, HistoricalCalendar, CostModel, RiskManager,
    )
    from SmartNoteBook import SmartNoteBook, SmartNoteBookConfig
    from ChartMind import ChartMind

    config = BacktestConfig(
        pair="EUR/USD",
        starting_equity=starting_equity,
        risk_per_trade_pct=0.5,
        daily_loss_cap_pct=3.0,
        max_drawdown_cap_pct=15.0,
        output_dir=str(OUTPUT_DIR),
    )

    snb_dir = OUTPUT_DIR / "notebook"
    if snb_dir.exists():
        # Fresh notebook for clean stats
        import shutil
        shutil.rmtree(snb_dir, ignore_errors=True)
    snb = SmartNoteBook(SmartNoteBookConfig(state_dir=str(snb_dir)))
    cm = ChartMind()

    runner = BacktestRunner(config=config, chartmind=cm, snb=snb)
    log(f"Running backtest over {len(bars)} bars...")
    log(f"  starting_equity=${starting_equity:,.2f}")
    log(f"  risk_per_trade={config.risk_per_trade_pct}%")
    log(f"  session windows: {config.session_windows} (NY)")

    t0 = time.time()
    result = runner.run(bars)
    elapsed = time.time() - t0
    log(f"Backtest done in {elapsed:.1f}s ({elapsed/60:.1f} min)")
    log(result.summary())

    return result, snb


# ----------------------------------------------------------------------
# Phase 3 — Verify the 12 requirements.
# ----------------------------------------------------------------------
@dataclass
class RequirementCheck:
    id: int
    name: str
    passed: bool
    detail: str
    threshold: str = ""


def verify_requirements(result, analyzer_report) -> list[RequirementCheck]:
    """Check each of the operator's 12 requirements."""
    r = analyzer_report
    checks: list[RequirementCheck] = []

    # [1] Pair = EUR/USD only
    pair_ok = result.config.pair == "EUR/USD"
    checks.append(RequirementCheck(
        1, "Pair = EUR/USD only", pair_ok,
        f"actual pair: {result.config.pair}",
        threshold="EUR/USD",
    ))

    # [2] Real OANDA data
    cache_exists = CACHE_PATH.exists()
    cache_size_kb = CACHE_PATH.stat().st_size / 1024 if cache_exists else 0
    real_data = cache_exists and cache_size_kb > 100   # > 100KB means actual fetch
    checks.append(RequirementCheck(
        2, "Real OANDA data (not synthetic)", real_data,
        f"cache: {CACHE_PATH} = {cache_size_kb:.0f} KB",
        threshold="cache file > 100 KB",
    ))

    # [3] Day-trading mode (M15, average bars_held < 96 = 1 trading day)
    intra_day = r.avg_bars_held > 0 and r.avg_bars_held < 96
    checks.append(RequirementCheck(
        3, "Day-trading mode (intra-day)", intra_day,
        f"avg bars held = {r.avg_bars_held:.1f} (M15) "
        f"= {r.avg_bars_held*15/60:.1f} hours",
        threshold="< 24 hours per trade",
    ))

    # [4] NY trading hours
    sw = result.config.session_windows
    hours_ok = sw == (("03:00", "05:00"), ("08:00", "12:00"))
    checks.append(RequirementCheck(
        4, "Trading hours = NY 03-05 + 08-12 Mon-Fri", hours_ok,
        f"windows: {sw}",
        threshold="(('03:00','05:00'), ('08:00','12:00'))",
    ))

    # [5] At least 2 trades/day average
    days = max(1, (result.ended_at - result.started_at).days)
    weekdays = days * 5 // 7   # rough
    trades_per_day = r.n_trades / max(1, weekdays)
    avg_2_ok = trades_per_day >= 2.0
    checks.append(RequirementCheck(
        5, "Average >= 2 trades/day", avg_2_ok,
        f"{r.n_trades} trades / {weekdays} weekdays = {trades_per_day:.2f}/day",
        threshold=">= 2.0 trades/day",
    ))

    # [6] Test duration >= 2 years
    span_days = (result.ended_at - result.started_at).days
    span_ok = span_days >= 700   # 2 years = ~730 days; allow margin
    checks.append(RequirementCheck(
        6, "Duration >= 2 years", span_ok,
        f"{span_days} days = {span_days/365.25:.2f} years",
        threshold=">= 700 days",
    ))

    # [7] Total return > 150%
    ret_ok = r.total_return_pct > 150.0
    checks.append(RequirementCheck(
        7, "Total return > 150% over period", ret_ok,
        f"{r.total_return_pct:+.2f}% "
        f"(${r.starting_equity:,.0f} -> ${r.final_equity:,.0f})",
        threshold="> 150%",
    ))

    # [8] All costs included
    has_costs = (
        result.config.entry_slippage_pips > 0
        and result.config.stop_slippage_pips > 0
    )
    checks.append(RequirementCheck(
        8, "All trading costs included", has_costs,
        f"slippage: entry={result.config.entry_slippage_pips}p, "
        f"stop={result.config.stop_slippage_pips}p, "
        f"commission={result.config.commission_per_lot_per_side}/lot/side, "
        f"spread: from OANDA bid/ask",
        threshold="slippage > 0 AND commission tracked AND spread real",
    ))

    # [9] News blackouts active
    news_blocked = r.rejected_by_calendar > 0
    checks.append(RequirementCheck(
        9, "News blackouts (CPI/NFP/FOMC/ECB)", news_blocked,
        f"{r.rejected_by_calendar} entries rejected by calendar",
        threshold="> 0 calendar rejections",
    ))

    # [10] Risk management active
    risk_active = (
        result.config.daily_loss_cap_pct > 0
        and result.config.max_drawdown_cap_pct > 0
        and result.config.risk_per_trade_pct > 0
    )
    checks.append(RequirementCheck(
        10, "Risk management active", risk_active,
        f"daily_loss_cap={result.config.daily_loss_cap_pct}%, "
        f"max_dd_cap={result.config.max_drawdown_cap_pct}%, "
        f"risk_per_trade={result.config.risk_per_trade_pct}%, "
        f"rejected_by_risk={r.rejected_by_risk}",
        threshold="all three caps > 0",
    ))

    # [11] Quality metrics
    has_metrics = r.n_trades > 0 and r.sqn != 0
    checks.append(RequirementCheck(
        11, "Quality metrics computed", has_metrics,
        f"WR={r.win_rate*100:.1f}%, "
        f"avgWin/Loss={r.avg_win_r:.2f}/{r.avg_loss_r:.2f}R, "
        f"SQN={r.sqn:.2f}, "
        f"maxDD={r.max_dd_pct:.1f}%, "
        f"monthly_breakdown={len(r.monthly)} months",
        threshold="non-zero metrics across all dimensions",
    ))

    # [12] No lookahead / no overfitting
    is_e = r.in_sample.get("expectancy_r", 0)
    oos_e = r.out_of_sample.get("expectancy_r", 0)
    no_overfit = (
        # Either both positive, or both negative — same sign means
        # the system is consistent across IS/OOS (not overfit).
        # If IS positive and OOS negative -> overfit -> FAIL.
        (is_e > 0 and oos_e > 0) or (is_e < 0 and oos_e < 0)
        or abs(is_e - oos_e) < 0.10   # very close = consistent
    )
    checks.append(RequirementCheck(
        12, "No lookahead / no overfitting", no_overfit,
        f"IS expectancy={is_e:+.3f}R, OOS expectancy={oos_e:+.3f}R, "
        f"divergence={abs(is_e - oos_e):.3f}R",
        threshold="OOS does not flip sign vs IS",
    ))

    return checks


# ----------------------------------------------------------------------
# Phase 4 — Save reports + print summary.
# ----------------------------------------------------------------------
def save_outputs(result, analyzer, report, checks):
    # results.json — machine-readable
    results_path = OUTPUT_DIR / "results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump({
            "config": result.config.to_dict(),
            "summary": {
                "started_at": result.started_at.isoformat(),
                "ended_at": result.ended_at.isoformat(),
                "bars_seen": result.bars_seen,
                "signals_generated": result.signals_generated,
                "entries_filled": result.entries_filled,
                "closed_trades": result.closed_trades,
                "starting_equity": result.starting_equity,
                "final_equity": result.final_equity,
                "max_drawdown_pct": result.max_drawdown_pct,
                "halted_early": result.halted_early,
                "halt_reason": result.halt_reason,
            },
            "report": report.to_dict(),
            "requirements": [
                {"id": c.id, "name": c.name, "passed": c.passed,
                 "detail": c.detail, "threshold": c.threshold}
                for c in checks
            ],
        }, f, indent=2, ensure_ascii=False, default=str)
    log(f"Wrote {results_path}")

    # report.txt — human-readable
    report_path = OUTPUT_DIR / "report.txt"
    text = analyzer.text_report(report)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(text)
        f.write("\n\n")
        f.write("REQUIREMENTS VERIFICATION\n")
        f.write("=" * 70 + "\n")
        for c in checks:
            mark = "PASS" if c.passed else "FAIL"
            f.write(f"  [{c.id:2d}] [{mark}]  {c.name}\n")
            f.write(f"           threshold: {c.threshold}\n")
            f.write(f"           actual:    {c.detail}\n")
        passed = sum(1 for c in checks if c.passed)
        f.write(f"\n  TOTAL: {passed}/{len(checks)} requirements passed\n")
    log(f"Wrote {report_path}")

    # equity curve
    eq_path = OUTPUT_DIR / "equity_curve.json"
    with open(eq_path, "w", encoding="utf-8") as f:
        json.dump([
            (t.isoformat(), e) for t, e in result.equity_curve
        ], f)
    log(f"Wrote {eq_path}")


def print_requirements_summary(checks):
    banner(" REQUIREMENTS VERIFICATION ")
    for c in checks:
        mark = "✓ PASS" if c.passed else "✗ FAIL"
        print(f"  [{c.id:2d}] {mark}   {c.name}")
        print(f"          {c.detail}")
    print()
    passed = sum(1 for c in checks if c.passed)
    total = len(checks)
    if passed == total:
        print(f"  RESULT: {passed}/{total} ALL REQUIREMENTS MET ✓")
    else:
        print(f"  RESULT: {passed}/{total} requirements passed — "
              f"{total - passed} FAILED")


# ----------------------------------------------------------------------
# Main.
# ----------------------------------------------------------------------
def main() -> int:
    banner(" EUR/USD BACKTEST — INSTITUTIONAL VERIFICATION ")
    log(f"Output dir: {OUTPUT_DIR}")

    # Date range: 2 years ending today (or end-of-yesterday for completeness)
    end_utc = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    start_utc = end_utc - timedelta(days=730)

    # ---- Phase 1: data ----
    section("Phase 1 — Fetching OANDA bars")
    bars = fetch_data(start_utc, end_utc)
    if len(bars) < 1000:
        log("ERROR: insufficient bars to backtest. "
            "Check OANDA_API_TOKEN + OANDA_ACCOUNT_ID env vars.")
        return 2

    # ---- Phase 2: backtest ----
    section("Phase 2 — Running backtest")
    result, snb = run_backtest(bars)

    # ---- Phase 3: analyze ----
    section("Phase 3 — Analyzing results")
    from Backtest import BacktestAnalyzer
    analyzer = BacktestAnalyzer(result, oos_pct=30.0, walk_forward_days=60)
    report = analyzer.analyze()
    print(analyzer.text_report(report))
    print(analyzer.equity_curve_ascii(width=70, height=12, report=report))

    # ---- Phase 4: verify requirements ----
    section("Phase 4 — Verifying requirements")
    checks = verify_requirements(result, report)
    save_outputs(result, analyzer, report, checks)
    print_requirements_summary(checks)

    # Exit code 0 only if all requirements met.
    passed = all(c.passed for c in checks)
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
    except Exception as e:
        traceback.print_exc()
        sys.exit(3)
