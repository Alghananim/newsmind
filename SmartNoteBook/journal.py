# -*- coding: utf-8 -*-
"""Journal — the source of truth for every trade the system ever makes.

Design principles
-----------------
1. **Append-only**. Every closed trade becomes one JSONL line; we never
   rewrite history. Steenbarger's first lesson in *The Daily Trading
   Coach* is that the journal must be honest and immutable — a moving-
   target log is no log at all.

2. **Wide schema**. The TradeRecord has ~40 fields because the value of
   a journal lies in *what you can re-query later*. If we only stored
   PnL we could never ask "did setup X work better at 14:00 UTC than at
   08:00 UTC?". Following Lopez de Prado's meta-labeling philosophy
   (AFML ch.3): record everything that *could* be a feature.

3. **Atomic writes**. Each append is a single fsync'd line so a crash
   mid-write cannot corrupt earlier records. The file rotation is
   monthly (`trades-YYYY-MM.jsonl`) so individual files stay small
   enough to grep through by hand if needed.

4. **Both quality dimensions**. Every record carries
   `decision_quality_grade` AND `outcome_quality_grade`. This is Annie
   Duke's central rule: judging a poker hand by whether you won is
   "resulting" — a fast track to overconfidence after lucky wins and
   capitulation after unlucky losses. We record both so the metrics
   layer can correlate them and surface mismatches.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional


# ----------------------------------------------------------------------
# Sub-records.
# ----------------------------------------------------------------------
@dataclass
class BrainGradeRecord:
    """Snapshot of one brain's verdict at decision time.

    Captured for every brain (NewsMind, ChartMind, MarketMind) so we can
    later ask: "when ChartMind said A and NewsMind said B, what
    happened?" — the kind of cohort question that drives system
    revisions (Carver, *Systematic Trading*, ch.18).
    """
    brain: str                  # "newsmind" | "chartmind" | "marketmind"
    grade: str                  # "A+" | "A" | "B" | "C"
    direction: str              # "long" | "short" | "neutral"
    confidence: float           # 0.0 - 1.0
    rationale: str = ""         # short text from the brain
    veto_flags: list[str] = field(default_factory=list)


@dataclass
class TradeOutcome:
    """How a trade actually resolved."""
    exit_price: float
    exit_reason: str            # "target", "stop", "time_decay", "manual",
                                # "setup_invalidated", "kill_switch", "partial"
    closed_at: datetime
    pnl_currency: float
    pnl_pips: float
    r_multiple: float
    bars_held: int
    max_favourable_excursion_pips: float = 0.0   # MFE
    max_adverse_excursion_pips: float = 0.0      # MAE


# ----------------------------------------------------------------------
# Main record.
# ----------------------------------------------------------------------
@dataclass
class TradeRecord:
    """A single closed trade — the atomic unit of journal memory.

    We separate three logical sections inside one record to keep
    grouping clear: identity, decision context, plan + execution,
    outcome + review. The whole record is one JSONL line.
    """
    # ----- identity --------------------------------------------------
    trade_id: str                          # UUID4
    pair: str                              # "EUR/USD"
    opened_at: datetime
    closed_at: datetime

    # ----- decision context ------------------------------------------
    brain_grades: list[BrainGradeRecord]   # one per brain
    gate_combined_confidence: float        # geometric mean from GateMind
    market_regime: str                     # "trend_up" | "trend_down" | "range" | "volatile"
    news_state: str                        # "calm" | "pre_event" | "post_event" | "blackout"
    spread_pips_at_entry: float
    spread_percentile_rank: float          # 0.0 (calm) - 1.0 (wide)

    # ----- plan -------------------------------------------------------
    setup_type: str                        # "breakout_pullback" | "trend_continuation" | ...
    direction: str                         # "long" | "short"
    entry_price: float
    stop_price: float
    target_price: float
    rr_planned: float
    time_budget_bars: int
    plan_rationale: str                    # ChartMind's narrative
    plan_confidence: float                 # ChartMind's confidence at plan time

    # ----- execution --------------------------------------------------
    filled_price: float
    requested_price: float
    slippage_pips: float
    lot_size: float
    risk_amount_currency: float
    sizing_method: str                     # "fixed_fractional" | "fixed_r" | "quarter_kelly"
    broker_order_id: str

    # ----- outcome ----------------------------------------------------
    outcome: TradeOutcome

    # ----- post-trade review (filled by post_mortem.py) --------------
    decision_quality_grade: int = 0        # 1 (poor process) - 5 (excellent process)
    outcome_quality_grade: int = 0         # 1 (poor result) - 5 (excellent result)
    what_went_right: str = ""
    what_went_wrong: str = ""
    what_id_change: str = ""               # Steenbarger's "what would I do differently"
    one_sentence_lesson: str = ""          # forced brevity — the headline lesson
    tags: list[str] = field(default_factory=list)

    # ----- pre-mortem (filled before submit) -------------------------
    pre_mortem_top_risk: str = ""          # Klein: the *predicted* failure mode
    pre_mortem_predicted_outcome: str = "" # "win" | "loss" | "scratch"

    # ----- annotations ------------------------------------------------
    annotations: list[str] = field(default_factory=list)
    schema_version: int = 1

    # ----- serialisation ---------------------------------------------
    def to_dict(self) -> dict:
        d = asdict(self)
        # datetime -> ISO8601 (timezone-aware, UTC)
        for key in ("opened_at", "closed_at"):
            v = d.get(key)
            if isinstance(v, datetime):
                d[key] = v.isoformat()
        if isinstance(d.get("outcome", {}).get("closed_at"), datetime):
            d["outcome"]["closed_at"] = d["outcome"]["closed_at"].isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        # Be permissive: missing optional fields default; datetimes get
        # parsed back; sub-records get re-constructed.
        d = dict(d)  # shallow copy, don't mutate caller
        for key in ("opened_at", "closed_at"):
            v = d.get(key)
            if isinstance(v, str):
                d[key] = datetime.fromisoformat(v.replace("Z", "+00:00"))
        bg = [
            BrainGradeRecord(**g) if not isinstance(g, BrainGradeRecord) else g
            for g in d.get("brain_grades", [])
        ]
        d["brain_grades"] = bg
        oc = d.get("outcome")
        if isinstance(oc, dict):
            oc = dict(oc)
            ts = oc.get("closed_at")
            if isinstance(ts, str):
                oc["closed_at"] = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            d["outcome"] = TradeOutcome(**oc)
        return cls(**d)


# ----------------------------------------------------------------------
# The journal store itself.
# ----------------------------------------------------------------------
class Journal:
    """Append-only JSONL trade journal with monthly rotation.

    Files are named `trades-YYYY-MM.jsonl` inside `directory`. Reads
    span all months unless callers narrow with a date filter. All
    public methods are thread-safe.
    """

    def __init__(self, directory: str | Path):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ----- write side ------------------------------------------------
    def append(self, record: TradeRecord) -> None:
        """Append one trade record as a single JSONL line.

        Atomicity strategy: open in append mode, write the full line
        (including newline) in one syscall, fsync. POSIX guarantees
        appends < PIPE_BUF (typically 4 KiB) are atomic; a TradeRecord
        in JSON is normally 1-3 KiB so we fit. If a record exceeds the
        limit we still fsync to flush, accepting the (rare) risk of a
        torn write across a crash boundary — the post-crash repair job
        can rebuild from broker logs.
        """
        line = json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"))
        path = self._monthly_path(record.opened_at)
        with self._lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass

    def annotate(self, trade_id: str, note: str,
                 ts: Optional[datetime] = None) -> bool:
        """Add a human note to an existing trade by writing a sidecar
        line. We never rewrite the original record; the metrics layer
        joins annotations back at read time.
        """
        ts = ts or datetime.now(timezone.utc)
        side_path = self._dir / "annotations.jsonl"
        line = json.dumps({
            "trade_id": trade_id,
            "ts": ts.isoformat(),
            "note": note,
        }, ensure_ascii=False)
        with self._lock:
            with open(side_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
        return True

    # ----- read side -------------------------------------------------
    def read_all(self) -> list[TradeRecord]:
        """Return every trade in the journal, ordered by `opened_at`."""
        records: list[TradeRecord] = []
        for path in sorted(self._dir.glob("trades-*.jsonl")):
            records.extend(self._read_file(path))
        records.sort(key=lambda r: r.opened_at)
        return records

    def read_recent(self, n: int) -> list[TradeRecord]:
        """Return the most-recent N trades (closed_at descending)."""
        all_ = self.read_all()
        all_.sort(key=lambda r: r.closed_at, reverse=True)
        return all_[:n]

    def read_between(self, start: datetime, end: datetime) -> list[TradeRecord]:
        """Trades whose `opened_at` is in [start, end)."""
        return [r for r in self.read_all() if start <= r.opened_at < end]

    def read_since(self, since: datetime) -> list[TradeRecord]:
        return [r for r in self.read_all() if r.opened_at >= since]

    def find(self, trade_id: str) -> Optional[TradeRecord]:
        for r in self.read_all():
            if r.trade_id == trade_id:
                return r
        return None

    def iter_all(self) -> Iterator[TradeRecord]:
        """Stream trades without loading the whole journal into memory.
        Useful when the journal grows past a few thousand records.
        """
        for path in sorted(self._dir.glob("trades-*.jsonl")):
            yield from self._read_file(path)

    # ----- update review fields (single-record rewrite) -------------
    def attach_review(self, trade_id: str, *,
                      decision_quality_grade: int,
                      outcome_quality_grade: int,
                      what_went_right: str,
                      what_went_wrong: str,
                      what_id_change: str,
                      one_sentence_lesson: str,
                      tags: Iterable[str] = ()) -> bool:
        """Patch the post-mortem fields on a trade. Implemented as a
        full file rewrite of the relevant month — slow, but cleaner
        than a sidecar for review fields that are referenced often.

        Strategy: read all trades from the month containing the trade,
        replace the matching record's review fields, write to a temp
        file, atomically rename. Any other writer racing us is
        serialised by `self._lock`.
        """
        target = self.find(trade_id)
        if target is None:
            return False
        path = self._monthly_path(target.opened_at)
        with self._lock:
            records = self._read_file(path)
            for r in records:
                if r.trade_id == trade_id:
                    r.decision_quality_grade = int(decision_quality_grade)
                    r.outcome_quality_grade = int(outcome_quality_grade)
                    r.what_went_right = what_went_right
                    r.what_went_wrong = what_went_wrong
                    r.what_id_change = what_id_change
                    r.one_sentence_lesson = one_sentence_lesson
                    r.tags = list(tags)
                    break
            tmp = path.with_suffix(".jsonl.tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for r in records:
                    f.write(json.dumps(r.to_dict(), ensure_ascii=False,
                                       separators=(",", ":")) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, path)
        return True

    # ----- internals -------------------------------------------------
    def _monthly_path(self, when: datetime) -> Path:
        # Ensure UTC for filename consistency.
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return self._dir / f"trades-{when.strftime('%Y-%m')}.jsonl"

    def _read_file(self, path: Path) -> list[TradeRecord]:
        out: list[TradeRecord] = []
        if not path.exists():
            return out
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    out.append(TradeRecord.from_dict(d))
                except Exception:
                    # A malformed line is logged-but-skipped rather than
                    # halting the whole read. The journal is meant to be
                    # readable even with crash debris.
                    continue
        return out


# ----------------------------------------------------------------------
# Convenience helpers.
# ----------------------------------------------------------------------
def new_trade_id() -> str:
    import uuid
    return str(uuid.uuid4())
