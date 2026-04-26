# -*- coding: utf-8 -*-
"""SmartNoteBook storage — JSONL append-only + SQLite for queries.

JSONL files: events_YYYY-MM-DD.jsonl (one event per line, append-only)
SQLite: notebook.db with tables: trade_audit, decision_events, lessons,
        bugs, daily_summaries, weekly_summaries

Failure modes:
   - SQLite locked → fallback to JSONL only + warning
   - duplicate audit_id → log + skip (don't overwrite)
   - missing field → log + write partial + warning
"""
from __future__ import annotations
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Iterator
from .models import (TradeAuditEntry, DecisionEvent, LessonLearned,
                     BugDetected, DailySummary, WeeklySummary)


_LOCK = threading.Lock()


class Storage:
    """Persistent + queryable. Default base_dir = ~/.cowork/smartnotebook/."""

    def __init__(self, base_dir: str):
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.jsonl_dir = self.base / "events"
        self.jsonl_dir.mkdir(exist_ok=True)
        self.db_path = self.base / "notebook.db"
        self._init_db()
        # V4: persistent connection (huge speedup vs open/close per write)
        self._conn = sqlite3.connect(self.db_path, timeout=2.0,
                                     isolation_level=None,    # autocommit
                                     check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self.warnings: list = []
        self.duplicates_count = 0
        self.dropped_count = 0

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trade_audit (
                    trade_id TEXT PRIMARY KEY,
                    audit_id TEXT UNIQUE,
                    pair TEXT,
                    system_mode TEXT,
                    direction TEXT,
                    entry_time TEXT,
                    exit_time TEXT,
                    pnl REAL,
                    classification TEXT,
                    payload TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_trade_pair ON trade_audit(pair);
                CREATE INDEX IF NOT EXISTS idx_trade_class ON trade_audit(classification);

                CREATE TABLE IF NOT EXISTS decision_events (
                    event_id TEXT PRIMARY KEY,
                    audit_id TEXT,
                    timestamp TEXT,
                    event_type TEXT,
                    pair TEXT,
                    gate_decision TEXT,
                    payload TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_dec_pair ON decision_events(pair);
                CREATE INDEX IF NOT EXISTS idx_dec_type ON decision_events(event_type);

                CREATE TABLE IF NOT EXISTS lessons (
                    lesson_id TEXT PRIMARY KEY,
                    pair TEXT,
                    pattern TEXT,
                    observed_count INTEGER,
                    requires_more_evidence INTEGER,
                    payload TEXT
                );

                CREATE TABLE IF NOT EXISTS bugs (
                    bug_id TEXT PRIMARY KEY,
                    affected_mind TEXT,
                    severity TEXT,
                    fixed INTEGER,
                    payload TEXT
                );

                CREATE TABLE IF NOT EXISTS daily_summaries (
                    date TEXT,
                    pair TEXT,
                    payload TEXT,
                    PRIMARY KEY (date, pair)
                );

                CREATE TABLE IF NOT EXISTS weekly_summaries (
                    week_start TEXT PRIMARY KEY,
                    payload TEXT
                );
            """)

    def _jsonl_path(self, dt: datetime) -> Path:
        return self.jsonl_dir / f"events_{dt.strftime('%Y-%m-%d')}.jsonl"

    def _append_jsonl(self, payload: dict, dt: datetime):
        p = self._jsonl_path(dt)
        with _LOCK:
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + "\n")

    # ---------------- Public API ----------------
    def write_trade(self, t: TradeAuditEntry) -> bool:
        if not t.audit_id or not t.trade_id:
            self.warnings.append(f"missing_id_skip:trade_id={t.trade_id} audit_id={t.audit_id}")
            return False
        d = t.to_dict()
        try:
            now = t.entry_time or datetime.now(timezone.utc)
            with _LOCK:
                exists = self._conn.execute(
                    "SELECT 1 FROM trade_audit WHERE trade_id = ? OR audit_id = ?",
                    (t.trade_id, t.audit_id)).fetchone()
                if exists:
                    self.warnings.append(f"duplicate_trade_id_skip:{t.trade_id}")
                    return False
                self._conn.execute(
                    "INSERT INTO trade_audit "
                    "(trade_id, audit_id, pair, system_mode, direction, entry_time, "
                    " exit_time, pnl, classification, payload) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (t.trade_id, t.audit_id, t.pair, t.system_mode, t.direction,
                     d.get("entry_time"), d.get("exit_time"), t.pnl,
                     t.classification, json.dumps(d, default=str)))
            self._append_jsonl({"type": "trade", **d}, now)
            return True
        except sqlite3.IntegrityError:
            self.warnings.append(f"duplicate_trade_id_skip:{t.trade_id}")
            return False
        except Exception as e:
            self.warnings.append(f"write_trade_failed:{e}")
            return False

    def write_event(self, e: DecisionEvent) -> bool:
        if not e.audit_id or not e.event_id:
            self.warnings.append(f"missing_id_event_skip:{e.event_id}")
            return False
        d = e.to_dict()
        try:
            self._append_jsonl({"type": "event", **d}, e.timestamp)
            with _LOCK:
                self._conn.execute(
                    "INSERT OR IGNORE INTO decision_events "
                    "(event_id, audit_id, timestamp, event_type, pair, gate_decision, payload) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (e.event_id, e.audit_id, d["timestamp"], e.event_type, e.pair,
                     e.gate_decision, json.dumps(d, default=str)))
            return True
        except Exception as ex:
            self.warnings.append(f"write_event_failed:{ex}")
            return False

    def write_lesson(self, l: LessonLearned) -> bool:
        try:
            with _LOCK:
                self._conn.execute(
                    "INSERT OR REPLACE INTO lessons "
                    "(lesson_id, pair, pattern, observed_count, requires_more_evidence, payload) "
                    "VALUES (?,?,?,?,?,?)",
                    (l.lesson_id, l.pair, l.pattern, l.observed_count,
                     1 if l.requires_more_evidence else 0,
                     json.dumps(l.to_dict(), default=str)))
            return True
        except Exception as e:
            self.warnings.append(f"write_lesson_failed:{e}")
            return False

    def write_bug(self, b: BugDetected) -> bool:
        try:
            with _LOCK:
                self._conn.execute(
                    "INSERT OR REPLACE INTO bugs "
                    "(bug_id, affected_mind, severity, fixed, payload) "
                    "VALUES (?,?,?,?,?)",
                    (b.bug_id, b.affected_mind, b.severity,
                     1 if b.fixed else 0,
                     json.dumps(b.to_dict(), default=str)))
            return True
        except Exception as e:
            self.warnings.append(f"write_bug_failed:{e}")
            return False

    def write_daily(self, s: DailySummary) -> bool:
        try:
            with _LOCK:
                self._conn.execute(
                    "INSERT OR REPLACE INTO daily_summaries (date, pair, payload) "
                    "VALUES (?,?,?)",
                    (s.date, s.pair, json.dumps(s.to_dict(), default=str)))
            return True
        except Exception as e:
            self.warnings.append(f"write_daily_failed:{e}")
            return False

    def write_weekly(self, s: WeeklySummary) -> bool:
        try:
            with _LOCK:
                self._conn.execute(
                    "INSERT OR REPLACE INTO weekly_summaries (week_start, payload) "
                    "VALUES (?,?)",
                    (s.week_start, json.dumps(s.to_dict(), default=str)))
            return True
        except Exception as e:
            self.warnings.append(f"write_weekly_failed:{e}")
            return False

    # ---------------- Query API ----------------
    def query_trades(self, *, pair: Optional[str] = None,
                     classification: Optional[str] = None,
                     limit: int = 100) -> List[dict]:
        sql = "SELECT payload FROM trade_audit WHERE 1=1"
        params = []
        if pair: sql += " AND pair = ?"; params.append(pair)
        if classification: sql += " AND classification = ?"; params.append(classification)
        sql += f" LIMIT {limit}"
        with _LOCK:
            return [json.loads(r[0]) for r in self._conn.execute(sql, params).fetchall()]

    def query_events(self, *, pair: Optional[str] = None,
                     event_type: Optional[str] = None,
                     limit: int = 100) -> List[dict]:
        sql = "SELECT payload FROM decision_events WHERE 1=1"
        params = []
        if pair: sql += " AND pair = ?"; params.append(pair)
        if event_type: sql += " AND event_type = ?"; params.append(event_type)
        sql += f" LIMIT {limit}"
        with _LOCK:
            return [json.loads(r[0]) for r in self._conn.execute(sql, params).fetchall()]

    def count_pattern(self, pair: str, pattern: str) -> int:
        with _LOCK:
            row = self._conn.execute(
                "SELECT observed_count FROM lessons WHERE pair = ? AND pattern = ?",
                (pair, pattern)).fetchone()
            return row[0] if row else 0

    def all_bugs(self) -> List[dict]:
        with _LOCK:
            return [json.loads(r[0]) for r in self._conn.execute(
                "SELECT payload FROM bugs").fetchall()]
