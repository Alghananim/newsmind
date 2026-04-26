# -*- coding: utf-8 -*-
"""Attribution — identify the responsible brain for win/loss."""
from __future__ import annotations
from .models import TradeAuditEntry, AttributionResult, MindOutputs


def attribute(t: TradeAuditEntry) -> AttributionResult:
    if t.mind_outputs is None:
        return AttributionResult(decision_quality="unclear",
                                primary_failure_factor="missing_mind_outputs")
    m = t.mind_outputs
    is_win = t.pnl > 0
    is_loss = t.pnl < 0

    # trade_dir_word: what direction did WE actually trade?
    trade_dir_word = ("bullish" if t.direction == "buy"
                      else ("bearish" if t.direction == "sell" else ""))

    supporting = []         # brains whose direction matches OUR trade direction
    contradicting = []
    for name, brain_dir in (
        ("news", m.news_market_bias if m.news_market_bias != "neutral" else m.news_bias),
        ("market", m.market_direction),
        ("chart", m.chart_trend_direction)):
        if brain_dir in ("bullish", "bearish") and trade_dir_word:
            if brain_dir == trade_dir_word:
                supporting.append(name)
            else:
                contradicting.append(name)

    primary_success = ""
    primary_failure = ""
    responsible = ""
    quality = "unclear"

    # Classification-based overrides (highest priority)
    cls = (t.classification or "")
    if cls == "spread_loss":
        return AttributionResult(
            primary_failure_factor="spread_or_slippage_excess",
            responsible_mind="execution",
            supporting_minds=tuple(supporting),
            contradicting_minds=tuple(contradicting),
            decision_quality="bad")
    if cls == "system_bug":
        return AttributionResult(
            primary_failure_factor="system_bug_detected",
            responsible_mind="system",
            supporting_minds=tuple(supporting),
            contradicting_minds=tuple(contradicting),
            decision_quality="bad")

    if is_win:
        if not supporting:
            quality = "lucky"
            primary_success = "luck_no_brain_aligned"
            responsible = "luck"
        elif len(supporting) == 1 and not contradicting:
            quality = "lucky"          # only one brain backed it
            primary_success = f"single_brain_alignment:{supporting}"
            responsible = supporting[0]
        elif len(supporting) >= 2 and not contradicting:
            quality = "good"
            primary_success = f"all_aligned:{supporting}"
            responsible = supporting[0]
        elif contradicting:
            quality = "mixed"
            primary_success = f"partial_alignment:{supporting}"
            responsible = supporting[0] if supporting else "luck"

    elif is_loss:
        # Check if any brain warned but gate overrode
        warn_against = []
        if m.news_perm in ("wait", "block") and m.gate_decision == "enter":
            warn_against.append("news_warned")
        if m.market_perm in ("wait", "block") and m.gate_decision == "enter":
            warn_against.append("market_warned")
        if m.chart_perm in ("wait", "block") and m.gate_decision == "enter":
            warn_against.append("chart_warned")

        if m.chart_late_entry:
            quality = "bad"; primary_failure = "late_entry_chart_should_block"
            responsible = "chart"
        elif m.chart_fake_breakout:
            quality = "bad"; primary_failure = "fake_breakout_chart_missed"
            responsible = "chart"
        elif "choppy" in (m.market_regime or ""):
            quality = "bad"; primary_failure = "choppy_market_market_should_block"
            responsible = "market"
        elif warn_against:
            quality = "bad"; primary_failure = f"gate_overrode_brain_warnings:{warn_against}"
            responsible = "gate"
        elif len(supporting) >= 2:
            # 2+ brains supported the (losing) trade → valid_loss
            quality = "valid_loss"
            primary_failure = "all_aligned_but_market_disagreed"
            responsible = "none"
        elif not supporting and len(contradicting) >= 2:
            # Entered against the brains
            quality = "bad"; primary_failure = f"entered_against_brains:{contradicting}"
            responsible = "gate"
        else:
            quality = "mixed"; primary_failure = "unclear"
            responsible = "unclear"

    return AttributionResult(
        primary_success_factor=primary_success,
        primary_failure_factor=primary_failure,
        responsible_mind=responsible,
        supporting_minds=tuple(supporting),
        contradicting_minds=tuple(contradicting),
        decision_quality=quality,
    )
