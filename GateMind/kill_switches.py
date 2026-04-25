# -*- coding: utf-8 -*-
"""Kill switches — the hard limits that override every other rule.

Kill switches are not "soft" filters. The decision module already
filtered for trade *quality*. This module asks one question:

    "Does the *account* allow ANY trade right now?"

A `True` answer here vetoes whatever the rest of the pipeline
recommends. The veto is unconditional and silent — the Reasons string
gets logged to the ledger, but no human approval is sought. That's
the point: by the time a kill switch fires, the trader's emotional
brain is the last decision-maker we want involved.

Rules implemented (all individually toggleable via config)
----------------------------------------------------------

    * Daily loss limit   — Mark Douglas, *Trading in the Zone*: a
                           pre-committed loss cap is the only line
                           between a trader and his worst day. Default
                           = -3% of session-start equity.

    * Drawdown limit     — Larry Hite (Schwager): "if you don't bet,
                           you can't win. If you lose all your chips,
                           you can't bet." Default = -15% from peak;
                           past this we stop entirely until manual
                           reset.

    * News blackout      — Niederhoffer / Lopez de Prado: variance
                           explodes around tier-1 economic releases;
                           spreads widen 5-10x; stops slip wildly.
                           Default: 30min before to 15min after every
                           tier-1 event NewsMind reports.

    * Spread guard       — Harris (Trading & Exchanges): execution
                           cost dominates scalping P&L. Refuse trades
                           when current spread > spread_pct_rank
                           (default 75th percentile of recent 500 bars).

    * Max concurrent     — single-position scalping doctrine. One
      positions               trade at a time per pair to avoid
                           correlated-failure fat tails.

    * Weekend / holiday  — no entries Friday after 21:00 UTC; no
      window                 entries Sunday before 22:00 UTC; no
                           entries on Christmas, New Year, etc.
                           Configurable list of UTC-date holidays.

    * Margin floor       — refuse to open if margin would drop the
                           account below `min_margin_pct`. Hard floor.

Each rule's check returns a (passed: bool, reason: str) tuple. The
top-level `evaluate()` returns the full set so the audit log can
capture every gate that fired, not just the first.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional


# --------------------------------------------------------------------
# Config.
# --------------------------------------------------------------------
@dataclass
class KillSwitchConfig:
    """All thresholds in one place. Edit per account, not per-trade."""
    enable_daily_loss: bool = True
    daily_loss_pct: float = 0.03        # 3% of session-start equity

    enable_drawdown: bool = True
    drawdown_pct: float = 0.15          # 15% peak-to-trough triggers halt

    enable_news_blackout: bool = True
    news_blackout_pre_min: int = 30
    news_blackout_post_min: int = 15

    enable_spread_guard: bool = True
    max_spread_pips: float = 2.0        # absolute cap, EUR/USD
    max_spread_rank: float = 0.75       # rolling percentile rank cap

    enable_max_concurrent: bool = True
    max_concurrent_per_pair: int = 1
    max_concurrent_total: int = 2

    enable_weekend_window: bool = True
    weekend_friday_close_utc: time = time(21, 0)
    weekend_sunday_open_utc: time = time(22, 0)

    enable_holiday_calendar: bool = True
    holidays_utc: list[date] = field(default_factory=list)

    enable_margin_floor: bool = True
    min_margin_pct: float = 0.10        # never let margin push below 10%
                                        # of equity in available cash

    # External soft-disable: if True, every gate is bypassed. Used in
    # backtests, NOT in live trading.
    bypass_all: bool = False


# --------------------------------------------------------------------
# Inputs needed for evaluation.
# --------------------------------------------------------------------
@dataclass
class KillSwitchInputs:
    now_utc: datetime
    equity: float
    today_starting_equity: float
    today_realised_pnl: float
    today_unrealised_pnl: float
    peak_equity: float
    open_positions_pair: int       # count for the pair we want to open
    open_positions_total: int      # count overall
    current_spread_pips: float
    spread_percentile_rank: float  # 0..1
    proposed_margin: float         # margin to be locked by the new trade
    cash_available: float
    upcoming_news_events: list[dict] = field(default_factory=list)
    # Each event is: {"name": str, "ts_utc": datetime, "tier": 1|2|3}


# --------------------------------------------------------------------
# Output.
# --------------------------------------------------------------------
@dataclass
class KillSwitchVerdict:
    halted: bool                       # if True, no new trades
    reasons: list[str]                 # always populated, even on pass
    fired_switches: list[str]          # names of gates that tripped

    def to_dict(self) -> dict:
        return {
            "halted": self.halted,
            "reasons": list(self.reasons),
            "fired_switches": list(self.fired_switches),
        }


# --------------------------------------------------------------------
# Individual gates.
# --------------------------------------------------------------------
def _check_daily_loss(i: KillSwitchInputs, c: KillSwitchConfig) -> tuple[bool, str]:
    if not c.enable_daily_loss:
        return True, ""
    if i.today_starting_equity <= 0:
        return True, ""
    pnl = i.today_realised_pnl + i.today_unrealised_pnl
    pct = pnl / i.today_starting_equity
    if pct <= -c.daily_loss_pct:
        return False, (
            f"daily_loss: P&L {pct:+.2%} of session-start equity "
            f"is at/below cap -{c.daily_loss_pct:.0%}; halt for the day"
        )
    return True, ""


def _check_drawdown(i: KillSwitchInputs, c: KillSwitchConfig) -> tuple[bool, str]:
    if not c.enable_drawdown or i.peak_equity <= 0:
        return True, ""
    dd = max(0.0, i.peak_equity - i.equity) / i.peak_equity
    if dd >= c.drawdown_pct:
        return False, (
            f"drawdown: {dd:.2%} from peak >= cap {c.drawdown_pct:.0%}; "
            "halt until manual reset"
        )
    return True, ""


def _check_news_blackout(
    i: KillSwitchInputs, c: KillSwitchConfig
) -> tuple[bool, str]:
    if not c.enable_news_blackout or not i.upcoming_news_events:
        return True, ""
    pre = timedelta(minutes=c.news_blackout_pre_min)
    post = timedelta(minutes=c.news_blackout_post_min)
    for ev in i.upcoming_news_events:
        ts = ev.get("ts_utc")
        if not isinstance(ts, datetime):
            continue
        tier = int(ev.get("tier", 3) or 3)
        if tier > 1:
            continue  # only tier-1 triggers blackout
        if ts - pre <= i.now_utc <= ts + post:
            return False, (
                f"news_blackout: {ev.get('name', 'tier-1 event')} "
                f"at {ts.isoformat()}; in window "
                f"[-{c.news_blackout_pre_min}m, +{c.news_blackout_post_min}m]"
            )
    return True, ""


def _check_spread(i: KillSwitchInputs, c: KillSwitchConfig) -> tuple[bool, str]:
    if not c.enable_spread_guard:
        return True, ""
    if i.current_spread_pips > c.max_spread_pips:
        return False, (
            f"spread_guard: current spread {i.current_spread_pips:.2f} pips "
            f"> cap {c.max_spread_pips:.2f}"
        )
    if i.spread_percentile_rank > c.max_spread_rank:
        return False, (
            f"spread_guard: spread percentile {i.spread_percentile_rank:.2f} "
            f"> cap {c.max_spread_rank:.2f} (recent distribution)"
        )
    return True, ""


def _check_max_concurrent(
    i: KillSwitchInputs, c: KillSwitchConfig
) -> tuple[bool, str]:
    if not c.enable_max_concurrent:
        return True, ""
    if i.open_positions_pair >= c.max_concurrent_per_pair:
        return False, (
            f"max_concurrent: already {i.open_positions_pair} open in this "
            f"pair (cap {c.max_concurrent_per_pair})"
        )
    if i.open_positions_total >= c.max_concurrent_total:
        return False, (
            f"max_concurrent: total open {i.open_positions_total} "
            f">= cap {c.max_concurrent_total}"
        )
    return True, ""


def _check_weekend_window(
    i: KillSwitchInputs, c: KillSwitchConfig
) -> tuple[bool, str]:
    if not c.enable_weekend_window:
        return True, ""
    wd = i.now_utc.weekday()
    t = i.now_utc.time()
    # 0=Mon .. 4=Fri 5=Sat 6=Sun
    if wd == 4 and t >= c.weekend_friday_close_utc:
        return False, (
            f"weekend: Friday after {c.weekend_friday_close_utc} UTC, "
            "no new entries"
        )
    if wd == 5:
        return False, "weekend: Saturday, no entries"
    if wd == 6 and t < c.weekend_sunday_open_utc:
        return False, (
            f"weekend: Sunday before {c.weekend_sunday_open_utc} UTC, "
            "no entries"
        )
    return True, ""


def _check_holiday(
    i: KillSwitchInputs, c: KillSwitchConfig
) -> tuple[bool, str]:
    if not c.enable_holiday_calendar or not c.holidays_utc:
        return True, ""
    today = i.now_utc.date()
    if today in c.holidays_utc:
        return False, f"holiday: {today.isoformat()} on configured list"
    return True, ""


def _check_margin_floor(
    i: KillSwitchInputs, c: KillSwitchConfig
) -> tuple[bool, str]:
    if not c.enable_margin_floor:
        return True, ""
    if i.equity <= 0:
        return False, "margin_floor: zero or negative equity"
    cash_after = i.cash_available - i.proposed_margin
    pct_after = cash_after / i.equity if i.equity > 0 else 0.0
    if pct_after < c.min_margin_pct:
        return False, (
            f"margin_floor: cash post-trade {pct_after:.1%} of equity "
            f"< floor {c.min_margin_pct:.0%}"
        )
    return True, ""


# --------------------------------------------------------------------
# Public API.
# --------------------------------------------------------------------
def evaluate(
    inputs: KillSwitchInputs,
    cfg: Optional[KillSwitchConfig] = None,
) -> KillSwitchVerdict:
    """Run every kill-switch gate; return the aggregate verdict."""
    if cfg is None:
        cfg = KillSwitchConfig()

    if cfg.bypass_all:
        return KillSwitchVerdict(
            halted=False,
            reasons=["bypass_all: kill switches disabled (backtest mode)"],
            fired_switches=[],
        )

    checks = [
        ("daily_loss",     _check_daily_loss),
        ("drawdown",       _check_drawdown),
        ("news_blackout",  _check_news_blackout),
        ("spread_guard",   _check_spread),
        ("max_concurrent", _check_max_concurrent),
        ("weekend_window", _check_weekend_window),
        ("holiday",        _check_holiday),
        ("margin_floor",   _check_margin_floor),
    ]
    reasons: list[str] = []
    fired: list[str] = []
    for name, fn in checks:
        ok, msg = fn(inputs, cfg)
        if not ok:
            fired.append(name)
            reasons.append(msg)
    halted = bool(fired)
    if not halted:
        reasons.append("all kill switches green")
    return KillSwitchVerdict(
        halted=halted,
        reasons=reasons,
        fired_switches=fired,
    )
