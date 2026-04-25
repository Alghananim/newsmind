# -*- coding: utf-8 -*-
"""Narrative — Arabic-language Telegram messages for GateMind events.

The narrative layer turns structured events into clear, scannable
Telegram messages. Doctrine borrowed from Steenbarger and Douglas:
the trader needs to *understand* every message in under five seconds
or the system loses trust. Long, poorly-formatted messages get
ignored, then the trader misses real signals.

Format conventions
------------------
    * Title line: emoji + pair + price + verdict word
    * Body: 4-8 short lines, each "Label: value"
    * Footer: only on errors / vetoes — "Reason: ..."
    * Arabic body, English-only fields (pair codes, prices, R:R)
    * No formatting that breaks Telegram MarkdownV2

Telegram delivery is sender-agnostic: this module produces *strings*.
The actual HTTP send is done elsewhere (a thin wrapper that calls
`https://api.telegram.org/bot{TOKEN}/sendMessage`). Decoupling
formatting from transport means we can also pipe these strings to
a log file, an email, or stdout for testing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


# --------------------------------------------------------------------
# Verdict-to-emoji.
# --------------------------------------------------------------------
_VERDICT_EMOJI = {
    "long":    "🟢",
    "short":   "🔴",
    "neutral": "⚪",
    "veto":    "⚠️",
    "halt":    "🛑",
    "fill":    "✅",
    "close":   "🏁",
    "error":   "❗",
}

_VERDICT_AR = {
    "long":    "شراء محتمل",
    "short":   "بيع محتمل",
    "neutral": "لا اتجاه",
    "veto":    "رفض",
    "halt":    "توقف كامل",
    "fill":    "تنفيذ",
    "close":   "إغلاق",
    "error":   "خطأ",
}


# --------------------------------------------------------------------
# Builders.
# --------------------------------------------------------------------
def decision_message(
    *,
    pair: str,
    price: float,
    direction: str,
    confidence_pct: float,
    grades: list[tuple[str, str]],          # [(brain_name, grade)]
    plan_summary: str = "",
    setup: str = "",
    entry: Optional[float] = None,
    stop: Optional[float] = None,
    target: Optional[float] = None,
    rr: Optional[float] = None,
    time_budget_bars: Optional[int] = None,
    ts: Optional[datetime] = None,
) -> str:
    """Long/short signal — full detail, ready for execution review."""
    emoji = _VERDICT_EMOJI.get(direction, "⚪")
    verdict = _VERDICT_AR.get(direction, direction)
    when = ts.strftime("%H:%M UTC") if ts else ""
    lines: list[str] = []
    lines.append(
        f"{emoji} {pair} @ {price:.5f}  •  {verdict} ({confidence_pct:.0f}%)"
    )
    if when:
        lines.append(f"الوقت: {when}")
    # Brain grades
    if grades:
        gtxt = "  ".join(f"{name}={grade}" for name, grade in grades)
        lines.append(f"التقييمات: {gtxt}")
    if setup:
        lines.append(f"الإعداد: {setup}")
    if entry is not None and stop is not None and target is not None:
        risk_pips = abs(entry - stop) / 0.0001
        rew_pips = abs(target - entry) / 0.0001
        lines.append(
            f"الدخول: {entry:.5f}   "
            f"الوقف: {stop:.5f} ({risk_pips:.1f}p)   "
            f"الهدف: {target:.5f} ({rew_pips:.1f}p)"
        )
    if rr is not None:
        rr_line = f"R:R = {rr:.2f}"
        if time_budget_bars is not None:
            rr_line += f"  •  الوقت المخصص: {time_budget_bars} شمعة"
        lines.append(rr_line)
    if plan_summary:
        lines.append(plan_summary)
    return "\n".join(lines)


def veto_message(
    *,
    pair: str,
    price: float,
    reasons: list[str],
    ts: Optional[datetime] = None,
) -> str:
    """Trade was generated but rejected by the gate or kill switch."""
    when = ts.strftime("%H:%M UTC") if ts else ""
    head = f"⚠️ {pair} @ {price:.5f}  •  رفض الإشارة"
    if when:
        head += f"  ({when})"
    body_lines = ["الأسباب:"]
    for r in reasons[:6]:
        body_lines.append(f"  • {r}")
    if len(reasons) > 6:
        body_lines.append(f"  … و{len(reasons) - 6} سبب إضافي في السجل")
    return head + "\n" + "\n".join(body_lines)


def fill_message(
    *,
    pair: str,
    direction: str,
    lot: float,
    filled_price: float,
    requested_price: float,
    stop_price: float,
    target_price: float,
    risk_amount: float,
    slippage_pips: Optional[float] = None,
    broker_order_id: str = "",
    ts: Optional[datetime] = None,
) -> str:
    """Order filled — entry confirmed."""
    emoji = _VERDICT_EMOJI["fill"]
    when = ts.strftime("%H:%M UTC") if ts else ""
    dir_ar = "شراء" if direction == "long" else "بيع"
    lines = [
        f"{emoji} {pair} {dir_ar} نُفّذ  •  {filled_price:.5f}",
    ]
    if when:
        lines.append(f"الوقت: {when}")
    lines.append(f"الحجم: {lot:.2f} لوت")
    if slippage_pips is not None:
        sign = "+" if slippage_pips >= 0 else ""
        lines.append(
            f"السعر المطلوب: {requested_price:.5f}   "
            f"الانزلاق: {sign}{slippage_pips:.2f} pip"
        )
    lines.append(f"الوقف: {stop_price:.5f}   الهدف: {target_price:.5f}")
    lines.append(f"المخاطرة: {risk_amount:.2f}")
    if broker_order_id:
        lines.append(f"الرقم: {broker_order_id}")
    return "\n".join(lines)


def close_message(
    *,
    pair: str,
    direction: str,
    entry_price: float,
    exit_price: float,
    pnl_currency: float,
    pnl_pips: float,
    r_multiple: float,
    bars_held: int,
    reason: str,
    ts: Optional[datetime] = None,
) -> str:
    """Position closed — exit confirmed."""
    emoji = "🟢" if pnl_currency > 0 else ("🔴" if pnl_currency < 0 else "⚪")
    when = ts.strftime("%H:%M UTC") if ts else ""
    dir_ar = "شراء" if direction == "long" else "بيع"
    sign = "+" if pnl_currency >= 0 else ""
    lines = [
        f"{emoji} {pair} {dir_ar} أُغلق  •  {exit_price:.5f}",
    ]
    if when:
        lines.append(f"الوقت: {when}")
    lines.append(
        f"النتيجة: {sign}{pnl_pips:.1f} pip   "
        f"({sign}{pnl_currency:.2f}, {sign}{r_multiple:.2f}R)"
    )
    lines.append(
        f"الدخول: {entry_price:.5f} → الخروج: {exit_price:.5f}   "
        f"({bars_held} شمعة)"
    )
    if reason:
        lines.append(f"السبب: {reason}")
    return "\n".join(lines)


def kill_switch_message(
    *,
    fired_switches: list[str],
    reasons: list[str],
    equity: Optional[float] = None,
    drawdown: Optional[float] = None,
    daily_pnl_pct: Optional[float] = None,
    ts: Optional[datetime] = None,
) -> str:
    """A kill switch fired — trading halted."""
    when = ts.strftime("%H:%M UTC") if ts else ""
    head = "🛑 GateMind: توقف"
    if when:
        head += f"  ({when})"
    lines = [head]
    if fired_switches:
        lines.append(
            "القواطع التي عملت: " + " • ".join(fired_switches)
        )
    if equity is not None:
        line = f"الإكويتي: {equity:.2f}"
        if drawdown is not None:
            line += f"   الدروداون: {drawdown:.1%}"
        if daily_pnl_pct is not None:
            line += f"   اليوم: {daily_pnl_pct:+.2%}"
        lines.append(line)
    lines.append("الأسباب:")
    for r in reasons[:6]:
        lines.append(f"  • {r}")
    return "\n".join(lines)


def error_message(
    *,
    pair: str,
    error_code: str,
    error_text: str,
    ts: Optional[datetime] = None,
) -> str:
    """Internal or broker error — needs human attention."""
    emoji = _VERDICT_EMOJI["error"]
    when = ts.strftime("%H:%M UTC") if ts else ""
    head = f"{emoji} GateMind error  •  {pair}"
    if when:
        head += f"  ({when})"
    body = f"الكود: {error_code}\nالرسالة: {error_text[:300]}"
    return head + "\n" + body


# --------------------------------------------------------------------
# Telegram delivery — thin wrapper, so callers don't repeat HTTP code.
# --------------------------------------------------------------------
def send_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
    timeout_seconds: float = 10.0,
) -> tuple[bool, str]:
    """Send a single Telegram message. Returns (ok, error_text).

    Network failures are caught and returned in `error_text`; we never
    raise out of this function so the caller's main loop is unaffected
    by a Telegram outage.
    """
    if not bot_token or not chat_id:
        return False, "missing bot_token or chat_id"
    try:
        import requests   # type: ignore
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        body = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        resp = requests.post(url, data=body, timeout=timeout_seconds)
        if resp.status_code == 200:
            return True, ""
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
