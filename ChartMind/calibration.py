# -*- coding: utf-8 -*-
"""Self-calibration Bayesian loop — upgrade #5.

Closes the loop from decision → trade → outcome → belief update.

At decision time the system records:
  * which (pair, pattern, regime, session, vol) context it was trading
  * what probability of success it predicted (from priors or from ML)
  * the trade_id issued by the broker / executor

At trade-close time the system records:
  * whether the trade finished profitable
  * the realised R-multiple

With both sides recorded, the system can:
  1. Update the underlying RegimePriors with the new observation —
     so tomorrow's lookup reflects today's experience.
  2. Produce a *reliability diagram*: for each predicted-probability
     bucket, what fraction actually won? A well-calibrated model has
     predicted≈actual on the diagonal. A miscalibrated one bulges.
  3. Track drift: if last 100 trades have systematically worse win
     rates than the prior expected, the regime has shifted and the
     model should be retrained.

This is the machinery the rest of the system uses to LEARN over time.
Without it, the priors are frozen at training day and the system
never gets smarter. With it, the system compounds intelligence the
way Thorp's own accounts compounded capital — one honest observation
at a time.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ChartMind.priors import RegimePriors, PriorContext


# ---------------------------------------------------------------------------
# Ledger record: one line per prediction/outcome pair.
# ---------------------------------------------------------------------------
@dataclass
class TradePrediction:
    trade_id: str
    context_key: str                   # RegimePriors context key
    pattern: str
    regime: str
    session: str
    vol_bucket: str
    pair: str
    side: str                          # "long" | "short"
    predicted_proba: float             # [0, 1]
    predicted_at: str                  # ISO UTC
    # Outcome fields — filled in at close time
    outcome: Optional[bool] = None
    closed_at: Optional[str] = None
    pnl_r: Optional[float] = None


# ---------------------------------------------------------------------------
# Reliability-diagram bucket.
# ---------------------------------------------------------------------------
@dataclass
class CalibrationBucket:
    lo: float                          # predicted_proba lower bound (inclusive)
    hi: float                          # predicted_proba upper bound (exclusive)
    n: int                             # trades in this bucket
    wins: int                          # actual wins
    mean_pred: float                   # mean predicted proba inside bucket
    actual_rate: float                 # wins / n  (0 if n=0)

    @property
    def miscalibration(self) -> float:
        """Signed gap: positive → model under-predicted, negative →
        over-predicted. For a perfectly calibrated bucket, ≈ 0."""
        return 0.0 if self.n == 0 else self.actual_rate - self.mean_pred


# ---------------------------------------------------------------------------
# Full calibration report.
# ---------------------------------------------------------------------------
@dataclass
class CalibrationReport:
    n_total: int                       # predictions with recorded outcomes
    n_open: int                        # predictions still pending outcome
    brier_score: float                 # lower is better (0 = perfect)
    overall_accuracy: float            # fraction where predicted>0.5 matched outcome
    buckets: list                      # list[CalibrationBucket]
    drift_alert: bool                  # True if recent window deviates materially

    def to_dict(self) -> dict:
        return {
            "n_total": self.n_total,
            "n_open": self.n_open,
            "brier_score": self.brier_score,
            "overall_accuracy": self.overall_accuracy,
            "buckets": [asdict(b) for b in self.buckets],
            "drift_alert": self.drift_alert,
        }


# ---------------------------------------------------------------------------
# The loop itself.
# ---------------------------------------------------------------------------
class SelfCalibration:
    """Stateful ledger that persists prediction/outcome pairs and
    updates a RegimePriors object as new outcomes come in.

    Usage:
        priors = RegimePriors.load("priors.json")
        cal = SelfCalibration(priors=priors,
                              ledger_path="calibration.jsonl")

        # --- at signal time ---
        ctx = RegimePriors.context_from_reading(reading, "bullish_engulfing")
        cal.log_prediction(trade_id="T-1234", ctx=ctx, pair="EUR_USD",
                           side="long", predicted_proba=0.67)

        # --- at trade close ---
        cal.log_outcome(trade_id="T-1234", success=True, pnl_r=+1.5)

        # --- anytime ---
        report = cal.calibration_report()
        if report.drift_alert:
            # Pause trading / retrain / notify
            ...
    """

    # Window of recent trades used for drift detection.
    DRIFT_WINDOW: int = 100
    # Absolute gap between recent win-rate and overall expected that
    # counts as "drift" (triggers the alert).
    DRIFT_THRESHOLD: float = 0.10

    def __init__(self, priors: RegimePriors, ledger_path: str | Path):
        self.priors = priors
        self.ledger_path = Path(ledger_path)
        self._records: dict[str, TradePrediction] = {}
        self._order: list[str] = []   # insertion order of trade_ids
        self._load()

    # ------------------------------------------------------------------
    # Core API.
    # ------------------------------------------------------------------
    def log_prediction(self, *, trade_id: str, ctx: PriorContext,
                       pair: str, side: str,
                       predicted_proba: float) -> None:
        rec = TradePrediction(
            trade_id=str(trade_id),
            context_key=ctx.key(),
            pattern=ctx.pattern,
            regime=ctx.regime,
            session=ctx.session,
            vol_bucket=ctx.vol_bucket,
            pair=pair,
            side=side,
            predicted_proba=float(predicted_proba),
            predicted_at=_now_iso(),
        )
        self._records[rec.trade_id] = rec
        if rec.trade_id not in self._order:
            self._order.append(rec.trade_id)
        self._append_line(rec)

    def log_outcome(self, *, trade_id: str, success: bool,
                    pnl_r: Optional[float] = None) -> None:
        rec = self._records.get(str(trade_id))
        if rec is None:
            # Unknown trade_id — silently ignore. This lets the executor
            # call log_outcome defensively without needing to remember
            # whether log_prediction fired first.
            return
        rec.outcome = bool(success)
        rec.closed_at = _now_iso()
        if pnl_r is not None:
            rec.pnl_r = float(pnl_r)

        # Feed back into the priors ------------------------------------
        ctx = PriorContext(
            pattern=rec.pattern,
            regime=rec.regime,
            session=rec.session,
            vol_bucket=rec.vol_bucket,
            pair=rec.pair,
        )
        self.priors.observe(ctx, success=bool(success))

        # Rewrite ledger with updated record
        self._rewrite_ledger()

    # ------------------------------------------------------------------
    # Reporting.
    # ------------------------------------------------------------------
    def calibration_report(self, n_buckets: int = 10) -> CalibrationReport:
        closed = [r for r in self._records.values() if r.outcome is not None]
        open_ = [r for r in self._records.values() if r.outcome is None]
        if not closed:
            return CalibrationReport(
                n_total=0, n_open=len(open_),
                brier_score=0.0, overall_accuracy=0.0,
                buckets=[], drift_alert=False,
            )

        # Brier score = mean((predicted - actual)^2)
        brier = sum(
            (r.predicted_proba - (1.0 if r.outcome else 0.0)) ** 2
            for r in closed
        ) / len(closed)

        # Classification-style accuracy at the 0.5 threshold
        acc = sum(
            1 for r in closed
            if (r.predicted_proba >= 0.5) == (r.outcome is True)
        ) / len(closed)

        # Buckets
        edges = [i / n_buckets for i in range(n_buckets + 1)]
        buckets: list[CalibrationBucket] = []
        for i in range(n_buckets):
            lo, hi = edges[i], edges[i + 1]
            inside = [
                r for r in closed
                if lo <= r.predicted_proba < (hi if i < n_buckets - 1 else hi + 1e-9)
            ]
            n = len(inside)
            wins = sum(1 for r in inside if r.outcome)
            mean_pred = (sum(r.predicted_proba for r in inside) / n) if n else 0.0
            rate = (wins / n) if n else 0.0
            buckets.append(CalibrationBucket(
                lo=lo, hi=hi, n=n, wins=wins,
                mean_pred=mean_pred, actual_rate=rate,
            ))

        # Drift detection: compare recent-window actual vs expected.
        recent = closed[-self.DRIFT_WINDOW:] if len(closed) > self.DRIFT_WINDOW \
            else closed
        recent_actual = sum(1 for r in recent if r.outcome) / max(1, len(recent))
        recent_expected = sum(r.predicted_proba for r in recent) / max(1, len(recent))
        drift = abs(recent_actual - recent_expected) > self.DRIFT_THRESHOLD \
            and len(recent) >= min(20, self.DRIFT_WINDOW // 2)

        return CalibrationReport(
            n_total=len(closed),
            n_open=len(open_),
            brier_score=brier,
            overall_accuracy=acc,
            buckets=buckets,
            drift_alert=drift,
        )

    def recent_trades(self, n: int = 20) -> list[dict]:
        """Most recent records (for dashboard / Telegram)."""
        ids = self._order[-n:]
        return [asdict(self._records[i]) for i in ids if i in self._records]

    # ------------------------------------------------------------------
    # Persistence (JSONL — one record per line, append-friendly).
    # ------------------------------------------------------------------
    def _append_line(self, rec: TradePrediction) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(rec)) + "\n")

    def _rewrite_ledger(self) -> None:
        """When we update an existing record's outcome, we rewrite the
        whole file. Cheap for thousand-record ledgers; switch to SQLite
        if the ledger ever exceeds 100k rows."""
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.ledger_path, "w", encoding="utf-8") as f:
            for tid in self._order:
                rec = self._records.get(tid)
                if rec is not None:
                    f.write(json.dumps(asdict(rec)) + "\n")

    def _load(self) -> None:
        if not self.ledger_path.exists():
            return
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        rec = TradePrediction(**d)
                    except Exception:
                        continue
                    self._records[rec.trade_id] = rec
                    if rec.trade_id not in self._order:
                        self._order.append(rec.trade_id)
        except Exception:
            pass


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
