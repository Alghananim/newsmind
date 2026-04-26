# -*- coding: utf-8 -*-
"""Classifier — distinguishes win/loss outcome categories."""
from __future__ import annotations
from .models import TradeAuditEntry, MindOutputs


def _all_top_grades(m: MindOutputs) -> bool:
    return all(g in ("A", "A+") for g in (m.news_grade, m.market_grade, m.chart_grade))


def _all_aligned_direction(m: MindOutputs) -> bool:
    """Brains internally agree on direction (regardless of trade)."""
    dirs = (m.news_market_bias if m.news_market_bias != "neutral" else m.news_bias,
            m.market_direction, m.chart_trend_direction)
    clear = [d for d in dirs if d in ("bullish", "bearish")]
    return len(clear) >= 2 and len(set(clear)) == 1


def _aligned_with_trade(m: MindOutputs, trade_direction: str) -> bool:
    """Brains aligned WITH the actual trade direction."""
    trade_dir_word = "bullish" if trade_direction == "buy" else (
        "bearish" if trade_direction == "sell" else "")
    if not trade_dir_word: return False
    dirs = (m.news_market_bias if m.news_market_bias != "neutral" else m.news_bias,
            m.market_direction, m.chart_trend_direction)
    aligned = sum(1 for d in dirs if d == trade_dir_word)
    return aligned >= 2


def classify(t: TradeAuditEntry) -> str:
    if t.mind_outputs is None:
        return "missing_mind_outputs"
    m = t.mind_outputs

    if abs(t.pnl) < 1e-9:
        return "breakeven"

    # Win path
    if t.pnl > 0:
        if t.expected_rr and t.mfe < 0.3 * abs((t.entry_price or 0) - (t.stop_loss or 0)):
            return "lucky_win_thin_margin"
        if not _all_top_grades(m):
            return "lucky_win_grade_mismatch"
        if not _aligned_with_trade(m, t.direction):
            return "lucky_win_direction_misaligned"
        if m.chart_late_entry:
            return "lucky_win_despite_late_entry"
        return "logical_win"

    # Loss path (pnl < 0)
    # System bug detection FIRST
    if "bug" in (m.gate_reason or "").lower():
        return "system_bug"
    if m.chart_late_entry:
        return "bad_loss_late_entry"
    if m.chart_fake_breakout:
        return "bad_loss_fake_breakout"
    if t.actual_slippage and t.spread_at_entry and (
        t.actual_slippage > 1.5 * (t.slippage_estimate or 0.5)):
        return "spread_loss"
    if "choppy" in (m.market_regime or "") or "high_volatility" in (m.market_regime or ""):
        return "bad_loss_choppy_market"

    # Brains aligned internally but trade went opposite direction
    if _all_aligned_direction(m) and not _aligned_with_trade(m, t.direction):
        return "bad_loss_misaligned"

    # Brains internally NOT aligned at all
    if not _all_aligned_direction(m):
        return "bad_loss_misaligned"

    if t.expected_rr and t.expected_rr < 1.0:
        return "bad_loss_rr_too_low"

    # If everything was OK and we still lost, valid_loss
    if _all_top_grades(m) and _aligned_with_trade(m, t.direction):
        return "valid_loss"

    return "bad_loss_unclear"
