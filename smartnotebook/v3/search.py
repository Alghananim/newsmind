# -*- coding: utf-8 -*-
"""Search / query interface — answer narrative questions about decisions."""
from __future__ import annotations
from collections import Counter
from typing import Optional
from .storage import Storage


def why_did_we_lose(storage: Storage, *, date: Optional[str] = None,
                    pair: Optional[str] = None) -> dict:
    trades = storage.query_trades(pair=pair, limit=1000)
    if date:
        trades = [t for t in trades if t.get("entry_time","").startswith(date)]
    losses = [t for t in trades if t.get("pnl", 0) < 0]
    if not losses:
        return {"summary": "no losses on this filter", "count": 0}
    causes = Counter(t.get("classification","") for t in losses)
    return {"summary": f"{len(losses)} losses",
            "top_causes": causes.most_common(5),
            "responsible_minds": Counter(
                (t.get("attribution",{}) or {}).get("responsible_mind","")
                for t in losses).most_common(5)}


def why_did_we_win(storage: Storage, *, date: Optional[str] = None,
                   pair: Optional[str] = None) -> dict:
    trades = storage.query_trades(pair=pair, limit=1000)
    if date:
        trades = [t for t in trades if t.get("entry_time","").startswith(date)]
    wins = [t for t in trades if t.get("pnl", 0) > 0]
    if not wins:
        return {"summary": "no wins on this filter", "count": 0}
    reasons = Counter(t.get("classification","") for t in wins)
    return {"summary": f"{len(wins)} wins",
            "top_reasons": reasons.most_common(5),
            "lucky_vs_logical": {
                "logical": sum(1 for t in wins if t.get("classification") == "logical_win"),
                "lucky": sum(1 for t in wins if t.get("classification","").startswith("lucky")),
            }}


def most_wrong_brain(storage: Storage, *, pair: Optional[str] = None) -> dict:
    trades = storage.query_trades(pair=pair, limit=1000)
    losses = [t for t in trades if t.get("pnl", 0) < 0]
    counts = Counter(
        (t.get("attribution",{}) or {}).get("responsible_mind","")
        for t in losses)
    return dict(counts.most_common(5))


def trades_that_should_have_been_blocked(storage: Storage,
                                         *, pair: Optional[str] = None) -> list:
    trades = storage.query_trades(pair=pair, limit=1000)
    return [t for t in trades
            if t.get("pnl", 0) < 0
            and t.get("classification","") in (
                "bad_loss_late_entry", "bad_loss_fake_breakout",
                "bad_loss_choppy_market", "bad_loss_misaligned",
                "bad_loss_rr_too_low", "spread_loss")]
