# -*- coding: utf-8 -*-
"""Precedent Engine - append-only JSONL store + bucket-based lookup."""
from __future__ import annotations
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from NewsMind.event_classifier import EventRecord


@dataclass
class _PrecedentRow:
    event_id: str
    surprise_z: Optional[float]
    bucket: str
    observed_time_utc: str
    realised_move_pips: Optional[float]
    realised_hours: Optional[float]
    session: Optional[str]


@dataclass
class PrecedentResult:
    n_matches: int
    avg_move_pips: float
    median_move_pips: float
    hit_rate_direction: float
    stdev_move_pips: float
    last_updated: Optional[datetime]


_BUCKET_BOUNDARIES = [0.0, 0.5, 1.5, 3.0]


def _bucket_for_z(z: Optional[float]) -> str:
    if z is None:
        return "none"
    sign = "pos" if z > 0 else ("neg" if z < 0 else "zero")
    az = abs(z)
    if az <= _BUCKET_BOUNDARIES[1]:
        mag = "small"
    elif az <= _BUCKET_BOUNDARIES[2]:
        mag = "medium"
    elif az <= _BUCKET_BOUNDARIES[3]:
        mag = "large"
    else:
        mag = "extreme"
    return f"{sign}_{mag}"


class PrecedentEngine:
    def __init__(self, store_path):
        if store_path is None or str(store_path) == ":memory:":
            self.store_path = None
        else:
            self.store_path = Path(store_path)
        self._rows: List[_PrecedentRow] = []
        self._loaded = False

    def load(self) -> None:
        self._rows = []
        self._loaded = True
        if self.store_path is None:
            return
        try:
            if not self.store_path.exists():
                self.store_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    self.store_path.touch()
                except (PermissionError, OSError):
                    self.store_path = None
                return
        except (PermissionError, OSError):
            self.store_path = None
            return
        try:
            with open(self.store_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                        self._rows.append(_PrecedentRow(**d))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except OSError:
            pass

    def record(self, event: EventRecord,
                  realised_move_pips: Optional[float] = None,
                  realised_hours: Optional[float] = None,
                  session: Optional[str] = None) -> None:
        if not self._loaded:
            self.load()
        row = _PrecedentRow(
            event_id=event.event_id,
            surprise_z=event.surprise_z,
            bucket=_bucket_for_z(event.surprise_z),
            observed_time_utc=(event.observed_time_utc or
                                datetime.now(timezone.utc)).isoformat(),
            realised_move_pips=realised_move_pips,
            realised_hours=realised_hours,
            session=session,
        )
        self._rows.append(row)
        if self.store_path is None:
            return
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.store_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
        except OSError:
            pass

    def lookup(self, event_id: str, surprise_z: Optional[float],
                 n_max: int = 20) -> PrecedentResult:
        if not self._loaded:
            self.load()
        bucket = _bucket_for_z(surprise_z)
        matches = [r for r in self._rows
                   if r.event_id == event_id and r.bucket == bucket
                   and r.realised_move_pips is not None]
        if len(matches) < 5:
            sign = "pos" if (surprise_z or 0) > 0 else (
                "neg" if (surprise_z or 0) < 0 else "zero")
            matches = [r for r in self._rows
                       if r.event_id == event_id and r.bucket.startswith(sign)
                       and r.realised_move_pips is not None]
        if len(matches) < 3:
            matches = [r for r in self._rows
                       if r.event_id == event_id
                       and r.realised_move_pips is not None]
        matches = matches[-n_max:]
        if not matches:
            return PrecedentResult(0, 0.0, 0.0, 0.0, 0.0, None)
        pips = [r.realised_move_pips for r in matches]
        avg = sum(pips) / len(pips)
        srt = sorted(pips)
        mid = srt[len(srt) // 2] if len(srt) % 2 else (
            (srt[len(srt) // 2 - 1] + srt[len(srt) // 2]) / 2.0)
        variance = sum((p - avg) ** 2 for p in pips) / max(1, len(pips) - 1)
        stdev = variance ** 0.5
        expected_sign = 1.0 if (surprise_z or 0) > 0 else (
            -1.0 if (surprise_z or 0) < 0 else 0.0)
        hit = 0
        for p in pips:
            if expected_sign == 0:
                continue
            if (p * expected_sign) > 0:
                hit += 1
        hit_rate = hit / len(pips) if pips else 0.0
        last_ts = max(r.observed_time_utc for r in matches)
        try:
            last_updated = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        except ValueError:
            last_updated = None
        return PrecedentResult(
            n_matches=len(matches), avg_move_pips=avg, median_move_pips=mid,
            hit_rate_direction=hit_rate, stdev_move_pips=stdev,
            last_updated=last_updated,
        )

    def row_count(self) -> int:
        if not self._loaded:
            self.load()
        return len(self._rows)
