# -*- coding: utf-8 -*-
"""ChartMind end-to-end verification.

Runs the full brain through three synthetic market scenarios:

    1. Strong uptrend — ChartMind should recommend LONG
    2. Ranging chop — ChartMind should be NEUTRAL
    3. Chaos volatility — ChartMind should ABSTAIN

For each scenario the script prints:

    * scenario label + parameters
    * raw ChartReading summary
    * Multi-TF summary
    * Confluence verdict + top factors
    * Clarity report
    * Final Arabic narrative

Also writes the full report to CHARTMIND_VERIFICATION.md so Mansur
can open it in Notepad / VS Code for a calm read.

Usage (via the top-level BAT):
    SHOW_CHARTMIND.bat
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from ChartMind import (
    ChartMind, ClarityScanner, NarrativeGenerator,
    RegimePriors, SelfCalibration, CalibratedConfidence,
)


# ---------------------------------------------------------------------------
# Synthetic scenario generators (pure NumPy — no look-ahead, independent).
# ---------------------------------------------------------------------------
def _make_bars(close: np.ndarray, start_ts="2025-01-01",
               freq="15min") -> pd.DataFrame:
    """Wrap a close series into a plausible OHLCV dataframe."""
    n = len(close)
    idx = pd.date_range(start_ts, periods=n, freq=freq, tz="UTC")
    # Realistic wicks derived from close volatility
    step = np.diff(close, prepend=close[0])
    wick = np.maximum(np.abs(step) * 0.5, 0.00005)
    high = close + wick
    low = close - wick
    open_ = np.concatenate(([close[0]], close[:-1]))
    volume = np.random.RandomState(42).randint(500, 2000, size=n)
    return pd.DataFrame({
        "Open": open_, "High": high, "Low": low, "Close": close,
        "Volume": volume,
    }, index=idx)


def scenario_uptrend(n: int = 600) -> pd.DataFrame:
    """Steady uptrend with modest noise — tuned for realistic EUR/USD
    scale (≈ 15-pip daily range, 400-pip total move over 600 bars)."""
    rng = np.random.RandomState(1)
    # 400 pips over 600 bars = 0.04 total drift
    drift = np.linspace(0, 0.04, n)
    # small per-bar noise, mean-reverts so ATR stays normal
    step = rng.randn(n) * 0.0003
    noise = np.cumsum(step) - 0.3 * np.cumsum(np.roll(step, 1))
    close = 1.1000 + drift + noise * 0.15
    return _make_bars(close)


def scenario_downtrend(n: int = 600) -> pd.DataFrame:
    """Steady downtrend — mirror of the uptrend scenario."""
    rng = np.random.RandomState(11)
    drift = np.linspace(0, -0.04, n)   # 400 pips down
    step = rng.randn(n) * 0.0003
    noise = np.cumsum(step) - 0.3 * np.cumsum(np.roll(step, 1))
    close = 1.1000 + drift + noise * 0.15
    return _make_bars(close)


def scenario_range(n: int = 600) -> pd.DataFrame:
    """Sideways chop — mean-reverting around 1.10 with normal vol."""
    rng = np.random.RandomState(2)
    # Ornstein-Uhlenbeck-like mean reversion
    close = np.zeros(n)
    close[0] = 1.1000
    for i in range(1, n):
        close[i] = (0.995 * close[i-1]
                    + 0.005 * 1.1000
                    + rng.randn() * 0.0004)
    return _make_bars(close)


def scenario_chaos(n: int = 600) -> pd.DataFrame:
    """Extreme volatility burst in the last 100 bars — news-like."""
    rng = np.random.RandomState(3)
    calm_len = n - 100
    calm_step = rng.randn(calm_len) * 0.0002
    calm = 1.1000 + np.cumsum(calm_step) - np.cumsum(calm_step) * 0.3
    storm_step = rng.randn(100) * 0.0015     # 10× wider noise
    storm = calm[-1] + np.cumsum(storm_step)
    close = np.concatenate([calm, storm])
    return _make_bars(close)


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------
def _build_multi_tf(df: pd.DataFrame) -> dict:
    """Derive coarser timeframes by resampling the M15 frame."""
    out: dict = {"M15": df}
    try:
        out["H1"] = df.resample("1h").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna()
    except Exception:
        pass
    try:
        out["H4"] = df.resample("4h").agg({
            "Open": "first", "High": "max", "Low": "min",
            "Close": "last", "Volume": "sum",
        }).dropna()
    except Exception:
        pass
    return out


def run_scenario(label: str, df: pd.DataFrame,
                 cm: ChartMind, scanner: ClarityScanner,
                 ng: NarrativeGenerator,
                 cc: CalibratedConfidence) -> str:
    """Run all 8 upgrades on one scenario. Returns a markdown report."""
    reading = cm.read(df, pair="EUR_USD")
    tf_dfs = _build_multi_tf(df)
    mtf = cm.read_multi_tf(tf_dfs, pair="EUR_USD")
    conf = cm.confluence(reading, mtf=mtf)

    # Calibrated from the raw confluence strength on the recommended side.
    if conf.verdict == "long":
        raw = conf.long_conviction
    elif conf.verdict == "short":
        raw = conf.short_conviction
    else:
        raw = max(conf.long_conviction, conf.short_conviction, 0.5)
    cal_res = cc.calibrate(raw)

    clarity = scanner.scan(
        reading=reading, mtf=mtf, confluence=conf,
        calibrated=cal_res,
    )
    narr = ng.generate(
        reading=reading, mtf=mtf, confluence=conf,
        calibrated=cal_res, clarity=clarity,
    )

    lines: list[str] = []
    lines.append(f"## {label}")
    lines.append("")
    lines.append("### ChartReading summary")
    lines.append("```")
    lines.append(reading.summary)
    lines.append("```")
    lines.append("")
    lines.append("### Multi-TF summary")
    lines.append("```")
    lines.append(mtf.summary)
    lines.append("```")
    lines.append("")
    lines.append("### Confluence")
    lines.append("```")
    lines.append(conf.summary)
    lines.append("```")
    lines.append("")
    lines.append("### Clarity (conflict + anti-pattern scan)")
    lines.append("```")
    lines.append(clarity.summary)
    lines.append("```")
    lines.append("")
    lines.append("### Calibrated confidence")
    lines.append(
        f"- raw: `{cal_res.raw:.3f}` — "
        f"calibrated: `{cal_res.calibrated:.3f}` — "
        f"CI95: `[{cal_res.ci_low:.3f}, {cal_res.ci_high:.3f}]` — "
        f"trust: `{cal_res.trust}` (n={cal_res.n_reference})"
    )
    lines.append("")
    lines.append("### Final narrative (Arabic)")
    lines.append("```")
    lines.append(narr.arabic_text)
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    # Reconfigure stdout for UTF-8 so the Arabic narrative prints cleanly
    # in Windows cmd.
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is not None and hasattr(s, "reconfigure"):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    print("=" * 60)
    print("  ChartMind End-to-End Verification")
    print("=" * 60)
    print()

    cm = ChartMind()
    scanner = ClarityScanner()
    ng = NarrativeGenerator()

    # Fresh empty priors + calibration (no history) — the calibrated
    # confidence will correctly report trust='none'. That's expected for
    # a fresh deployment.
    priors = RegimePriors()
    ledger_path = _HERE / "_verify_calibration_ledger.jsonl"
    # Reset ledger for a clean run every time.
    if ledger_path.exists():
        ledger_path.unlink()
    cal = SelfCalibration(priors=priors, ledger_path=ledger_path)
    cc = CalibratedConfidence(calibration=cal)

    scenarios = [
        ("Scenario 1 — Strong Uptrend (expect LONG bias)",    scenario_uptrend()),
        ("Scenario 2 — Strong Downtrend (expect SHORT bias)", scenario_downtrend()),
        ("Scenario 3 — Ranging Chop (expect NEUTRAL)",         scenario_range()),
        ("Scenario 4 — Chaos Volatility (expect ABSTAIN)",     scenario_chaos()),
    ]

    all_reports: list[str] = [
        "# ChartMind Verification Report",
        "",
        "This report is auto-generated by running ChartMind on three",
        "synthetic market scenarios. Inspect each section and verify",
        "the brain's verdicts match common sense.",
        "",
        "---",
        "",
    ]

    for label, df in scenarios:
        print(f"\n>>> Running: {label}")
        report = run_scenario(label, df, cm, scanner, ng, cc)
        all_reports.append(report)
        # Short console summary so the user sees something live.
        reading = cm.read(df)
        mtf = cm.read_multi_tf(_build_multi_tf(df))
        conf = cm.confluence(reading, mtf=mtf)
        clarity = scanner.scan(reading=reading, mtf=mtf, confluence=conf)
        print(f"    verdict: {conf.verdict:8s} "
              f"strength: {conf.verdict_strength:.2f}  "
              f"clarity: {clarity.verdict}")

    # Write the full markdown report.
    out_path = _PROJECT_ROOT / "CHARTMIND_VERIFICATION.md"
    out_path.write_text("\n".join(all_reports), encoding="utf-8")
    print()
    print("=" * 60)
    print(f"Full report written to:")
    print(f"  {out_path}")
    print("=" * 60)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
