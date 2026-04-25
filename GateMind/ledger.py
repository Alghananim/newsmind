# -*- coding: utf-8 -*-
"""Ledger — append-only audit trail for every decision and trade event.

Trading audit logs are not optional. As Brett Steenbarger writes in
*The Daily Trading Coach*: "trades you cannot replay, you cannot
learn from." Every gate, every veto, every fill, every exit must be
captured with enough context to reconstruct the moment.

Storage format
--------------
JSON Lines (`.jsonl`), one record per line. Why JSON Lines and not a
database:

    * Append-only is `>>` — concurrency-safe at OS level (POSIX
      O_APPEND guarantees atomic writes < PIPE_BUF).
    * `grep` works on it, `jq` works on it, `pandas.read_json(lines=
      True)` works on it.
    * No schema migrations. New fields just appear; old records are
      still readable.
    * If the file grows past tolerance, rotate by date — one
      directory listing tells you the trading history span.

Record types
------------
We define seven canonical event types. Anything novel goes under
`"event": "annotation"` with a free-form `details` payload.

    decision  — gate evaluated, trade approved (no order yet)
    veto      — gate evaluated, trade refused (with reasons)
    submit    — order sent to broker
    fill      — order filled (entry confirmation)
    update    — stop moved, target moved, partial close, etc.
    close     — position closed (exit confirmation)
    error     — broker error or internal exception
    annotation— free-form notes (manual journal entries, reviews)

Each record has a UTC timestamp, a monotonic sequence number (for
tie-breaking same-millisecond events), and a JSON payload. The
sequence number is persisted alongside the file so restarts pick up
where they left off.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional, Any


# --------------------------------------------------------------------
# Record schema.
# --------------------------------------------------------------------
@dataclass
class LedgerRecord:
    seq: int
    ts: str                # ISO 8601 UTC
    event: str             # one of the seven canonical types
    pair: str              # often "EUR_USD"
    payload: dict          # event-specific data

    def to_jsonl(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))


# --------------------------------------------------------------------
# The Ledger.
# --------------------------------------------------------------------
class Ledger:
    """Append-only JSONL audit trail with date-based file rotation.

    Files live under `dir/`, named `gatemind-YYYY-MM-DD.jsonl`. A
    sidecar `gatemind.seq` holds the last sequence number used.
    """

    def __init__(self, directory: str):
        self._dir = directory
        os.makedirs(self._dir, exist_ok=True)
        self._lock = threading.Lock()
        self._seq_path = os.path.join(self._dir, "gatemind.seq")
        self._seq = self._load_seq()

    # ----- sequence persistence -----------------------------------
    def _load_seq(self) -> int:
        try:
            with open(self._seq_path, "r") as f:
                return int((f.read() or "0").strip() or "0")
        except FileNotFoundError:
            return 0
        except Exception:
            return 0

    def _persist_seq(self) -> None:
        tmp = self._seq_path + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(self._seq))
        os.replace(tmp, self._seq_path)

    def _file_for(self, ts: datetime) -> str:
        return os.path.join(
            self._dir,
            f"gatemind-{ts.date().isoformat()}.jsonl",
        )

    # ----- append -------------------------------------------------
    def write(self, event: str, pair: str, payload: dict,
              ts: Optional[datetime] = None) -> int:
        """Append one record. Return the assigned seq number."""
        if ts is None:
            ts = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        with self._lock:
            self._seq += 1
            rec = LedgerRecord(
                seq=self._seq,
                ts=ts.isoformat(),
                event=event,
                pair=pair,
                payload=_sanitise(payload),
            )
            path = self._file_for(ts)
            with open(path, "a") as f:
                f.write(rec.to_jsonl() + "\n")
            self._persist_seq()
            return self._seq

    # ----- convenience wrappers -----------------------------------
    def decision(self, pair: str, payload: dict) -> int:
        return self.write("decision", pair, payload)

    def veto(self, pair: str, payload: dict) -> int:
        return self.write("veto", pair, payload)

    def submit(self, pair: str, payload: dict) -> int:
        return self.write("submit", pair, payload)

    def fill(self, pair: str, payload: dict) -> int:
        return self.write("fill", pair, payload)

    def update(self, pair: str, payload: dict) -> int:
        return self.write("update", pair, payload)

    def close(self, pair: str, payload: dict) -> int:
        return self.write("close", pair, payload)

    def error(self, pair: str, payload: dict) -> int:
        return self.write("error", pair, payload)

    def annotation(self, pair: str, text: str) -> int:
        return self.write("annotation", pair, {"text": text})

    # ----- read / replay ------------------------------------------
    def read_day(self, day_iso: str) -> list[LedgerRecord]:
        """Return every record for a single calendar day (UTC)."""
        path = os.path.join(self._dir, f"gatemind-{day_iso}.jsonl")
        if not os.path.exists(path):
            return []
        out: list[LedgerRecord] = []
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    out.append(LedgerRecord(
                        seq=int(d["seq"]),
                        ts=str(d["ts"]),
                        event=str(d["event"]),
                        pair=str(d.get("pair", "")),
                        payload=d.get("payload", {}),
                    ))
                except Exception:
                    # Skip corrupted lines but keep reading.
                    continue
        return out


# --------------------------------------------------------------------
# Helpers.
# --------------------------------------------------------------------
def _sanitise(obj: Any) -> Any:
    """Convert datetime / sets / etc into JSON-safe equivalents."""
    if isinstance(obj, dict):
        return {str(k): _sanitise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitise(v) for v in obj]
    if isinstance(obj, set):
        return [_sanitise(v) for v in sorted(obj, key=str)]
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            obj = obj.replace(tzinfo=timezone.utc)
        return obj.isoformat()
    if isinstance(obj, (int, float, str, bool)) or obj is None:
        return obj
    # Fallback: try to_dict, else str.
    if hasattr(obj, "to_dict") and callable(obj.to_dict):
        try:
            return _sanitise(obj.to_dict())
        except Exception:
            pass
    return str(obj)
