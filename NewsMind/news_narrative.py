# -*- coding: utf-8 -*-
"""English narrative builder for NewsContext."""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from NewsMind.NewsMind import NewsContext


def build_narrative(ctx: "NewsContext") -> str:
    parts = []
    if ctx.do_not_trade:
        parts.append(f"Stand down: {ctx.do_not_trade_reason or 'news conditions unsafe'}.")
    elif ctx.window_state.trading_halted:
        parts.append(f"Trading halted: {ctx.window_state.window_reason}.")
    if ctx.regime:
        if ctx.regime.regime == "crisis":
            parts.append(
                f"News regime is CRISIS "
                f"({ctx.regime.tier1_count_24h} Tier-1 in last 24h; "
                f"black-swan={ctx.regime.black_swan_suspected}).")
        elif ctx.regime.regime == "busy":
            parts.append(
                f"News regime is busy ({ctx.regime.event_density_24h} "
                f"Tier-1/2 events in 24h).")
        else:
            parts.append("News regime is quiet; no major releases pressing.")
    if ctx.last_event is not None:
        last = ctx.last_event
        line = f"Last release: {last.label}"
        if last.surprise_z is not None:
            line += f" (surprise z={last.surprise_z:+.2f})"
        parts.append(line + ".")
    if ctx.next_event is not None and not ctx.window_state.trading_halted:
        nx = ctx.next_event
        mins = ctx.window_state.t_to_event_min
        mt = f" in {mins:.0f} min" if mins is not None else ""
        parts.append(f"Next scheduled: {nx.label}{mt}.")
    if ctx.active_narratives:
        dom = max(ctx.active_narratives,
                  key=lambda n: n.reflexivity_stage * n.conviction)
        parts.append(
            f"Dominant narrative: {dom.label} at reflexivity stage "
            f"{dom.reflexivity_stage}/8 (conviction {dom.conviction:.2f}).")
    if not ctx.do_not_trade:
        bt = ("bullish EUR/USD" if ctx.net_bias == "long"
              else "bearish EUR/USD" if ctx.net_bias == "short"
              else "neutral")
        parts.append(
            f"Net news bias {bt}; strength {ctx.bias_strength:.2f}; "
            f"conviction {ctx.conviction}; confidence {ctx.confidence:.2f}.")
    if not parts:
        return "NewsMind has no active signal."
    return " ".join(parts)


def one_liner(ctx: "NewsContext") -> str:
    if ctx.do_not_trade:
        reason = ctx.do_not_trade_reason or ctx.window_state.window_reason or "halt"
        return f"NM: HALT - {reason[:90]}"
    regime = ctx.regime.regime if ctx.regime else "?"
    if ctx.net_bias == "neutral":
        return (f"NM: neutral | regime={regime} | conviction={ctx.conviction} | "
                f"conf={ctx.confidence:.2f}")
    return (f"NM: {ctx.net_bias} {ctx.bias_strength:.2f} | regime={regime} | "
            f"conviction={ctx.conviction}")[:120]
