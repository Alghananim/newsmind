# -*- coding: utf-8 -*-
"""Calibrated confidence — upgrade #6.

The Confluence Engine (#3) and RegimePriors (#4) together produce raw
probability numbers like "0.72 chance of win." But those numbers only
match reality if the system has been honest about the past. Real-world
models almost always produce *miscalibrated* probabilities: a model
that says "70% win" might historically win only 60% of the time
(over-confidence), or vice versa.

This module closes that gap. It:

  1. Reads the reliability data from SelfCalibration (#5).
  2. Builds a monotone mapping from raw_proba → empirically-adjusted
     proba, using an isotonic-regression-lite technique (pool adjacent
     violators — a textbook recipe that guarantees the mapping is
     non-decreasing).
  3. Exposes `calibrate(raw)` so downstream code can convert any model
     output to a trustable probability before making trading decisions.
  4. Tracks how much data backs the calibration — wide credible
     intervals when the sample is small, narrower as N grows.

Without this layer, the system claims "75%" and loses 50% of the time
and the trader stops trusting it. With it, the system claims 75% when
it actually happens 75% of the time, or honestly degrades the claim.

Theory reference (no copyrighted text involved):
  * Platt (1999) sigmoid calibration for SVMs — original idea.
  * Zadrozny & Elkan (2002) isotonic regression for classifiers —
    the monotone, non-parametric version we implement here.
  * Bailey & López de Prado (2014) — Deflated Sharpe Ratio, the
    same spirit: correct the raw number for finite-sample optimism.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ChartMind.calibration import SelfCalibration, CalibrationReport


# ---------------------------------------------------------------------------
# Result of a single calibration call.
# ---------------------------------------------------------------------------
@dataclass
class CalibratedProba:
    raw: float                 # the input probability
    calibrated: float          # the empirically-adjusted probability
    ci_low: float              # 95% credible interval lower bound
    ci_high: float             # 95% credible interval upper bound
    n_reference: int           # sample size backing the adjustment
    trust: str                 # "high" | "medium" | "low" | "none"

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "calibrated": self.calibrated,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "n_reference": self.n_reference,
            "trust": self.trust,
        }


# ---------------------------------------------------------------------------
# Core class.
# ---------------------------------------------------------------------------
class CalibratedConfidence:
    """Maps raw predicted probabilities to empirically-adjusted ones.

    Lifecycle:
      * `refresh()` rebuilds the internal table from SelfCalibration's
        latest reliability buckets. Call after every batch of N trades
        (say N=50) to keep the calibration current.
      * `calibrate(raw)` returns a CalibratedProba for any probability
        in [0, 1]. Safe to call before `refresh()` — falls back to
        raw with low trust.
    """

    # Minimum trades in a bucket before we trust its adjusted rate.
    # Below this count, we blend the bucket's actual_rate back toward
    # the raw value (soft shrinkage). Prevents a 2-trade bucket min
    # claiming certainty.
    MIN_TRUSTED_N: int = 15
    # Threshold above which we call the mapping "high trust" overall.
    HIGH_TRUST_TOTAL_N: int = 200

    def __init__(self, calibration: SelfCalibration):
        self.calibration = calibration
        # Internal table: sorted list of (raw_mid, adjusted, bucket_n)
        self._table: list[tuple[float, float, int]] = []
        self._total_n: int = 0
        self.refresh()

    # ------------------------------------------------------------------
    # Refresh — rebuild mapping from history.
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        """Rebuild the calibration mapping from the latest ledger state."""
        report: CalibrationReport = self.calibration.calibration_report(n_buckets=10)
        rows: list[tuple[float, float, int]] = []
        for b in report.buckets:
            if b.n == 0:
                continue
            # Blend actual rate back toward raw based on bucket size.
            # With n >= MIN_TRUSTED_N we trust the bucket's own rate.
            # With n < MIN_TRUSTED_N we shrink toward the mean pred.
            if b.n >= self.MIN_TRUSTED_N:
                adjusted = b.actual_rate
            else:
                weight = b.n / self.MIN_TRUSTED_N
                adjusted = weight * b.actual_rate + (1 - weight) * b.mean_pred
            rows.append((b.mean_pred, adjusted, b.n))

        rows.sort(key=lambda r: r[0])
        # Enforce monotonicity via pool-adjacent-violators (PAV).
        # This converts arbitrary bucket averages into a non-decreasing
        # calibration curve — required for sensible behaviour.
        rows = self._pav(rows)
        self._table = rows
        self._total_n = report.n_total

    # ------------------------------------------------------------------
    # Calibrate — the hot path.
    # ------------------------------------------------------------------
    def calibrate(self, raw_proba: float) -> CalibratedProba:
        """Return a CalibratedProba for a raw probability in [0, 1]."""
        raw = max(0.0, min(1.0, float(raw_proba)))
        trust = self._overall_trust()

        if not self._table or trust == "none":
            # No history — pass through, wide interval.
            return CalibratedProba(
                raw=raw, calibrated=raw,
                ci_low=max(0.0, raw - 0.25),
                ci_high=min(1.0, raw + 0.25),
                n_reference=0, trust="none",
            )

        # Linear interpolation on the (mid, adjusted) table.
        mids = [r[0] for r in self._table]
        vals = [r[1] for r in self._table]
        ns = [r[2] for r in self._table]

        if raw <= mids[0]:
            adjusted = vals[0]
            bucket_n = ns[0]
        elif raw >= mids[-1]:
            adjusted = vals[-1]
            bucket_n = ns[-1]
        else:
            # Find surrounding buckets and interpolate linearly.
            for i in range(len(mids) - 1):
                if mids[i] <= raw <= mids[i + 1]:
                    span = mids[i + 1] - mids[i]
                    if span <= 0:
                        adjusted = vals[i]
                    else:
                        t = (raw - mids[i]) / span
                        adjusted = vals[i] * (1 - t) + vals[i + 1] * t
                    bucket_n = (ns[i] + ns[i + 1]) // 2
                    break
            else:
                adjusted = raw
                bucket_n = 0

        # Credible interval: narrower when bucket has more data.
        # Based on binomial stdev approx, scaled for 95%.
        if bucket_n > 0:
            s = (adjusted * (1 - adjusted) / bucket_n) ** 0.5
            width = 1.96 * s
        else:
            width = 0.25
        ci_low = max(0.0, adjusted - width)
        ci_high = min(1.0, adjusted + width)

        return CalibratedProba(
            raw=raw,
            calibrated=float(adjusted),
            ci_low=float(ci_low),
            ci_high=float(ci_high),
            n_reference=int(bucket_n),
            trust=trust,
        )

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------
    def _overall_trust(self) -> str:
        if self._total_n >= self.HIGH_TRUST_TOTAL_N:
            return "high"
        if self._total_n >= 50:
            return "medium"
        if self._total_n >= 15:
            return "low"
        return "none"

    @staticmethod
    def _pav(rows: list[tuple[float, float, int]]
             ) -> list[tuple[float, float, int]]:
        """Pool-Adjacent-Violators: enforce non-decreasing adjusted
        values across sorted-by-mid buckets. Neighbours that violate
        monotonicity are merged (weighted by sample size).
        """
        if len(rows) <= 1:
            return list(rows)
        result: list[list] = [list(row) for row in rows]   # mutable copies
        i = 0
        while i < len(result) - 1:
            mid_a, val_a, n_a = result[i]
            mid_b, val_b, n_b = result[i + 1]
            if val_a > val_b:
                # Violation — pool a+b into a single weighted row.
                new_mid = (mid_a * n_a + mid_b * n_b) / (n_a + n_b) if (n_a + n_b) else mid_a
                new_val = (val_a * n_a + val_b * n_b) / (n_a + n_b) if (n_a + n_b) else val_a
                new_n = n_a + n_b
                result[i] = [new_mid, new_val, new_n]
                del result[i + 1]
                if i > 0:
                    i -= 1      # recheck the merged block against its predecessor
            else:
                i += 1
        return [tuple(r) for r in result]

    # ------------------------------------------------------------------
    # Introspection.
    # ------------------------------------------------------------------
    def table(self) -> list[dict]:
        """Dump the current calibration map — for dashboards / auditing."""
        return [
            {"raw_mid": m, "adjusted": a, "n": n}
            for m, a, n in self._table
        ]

    @property
    def total_n(self) -> int:
        return self._total_n

    @property
    def trust(self) -> str:
        return self._overall_trust()
