# -*- coding: utf-8 -*-
"""BacktestConfig — single source of truth for every knob.

Why a dedicated config module
-----------------------------
A backtest exposes ~40 parameters, from data range to risk caps to
analysis options. Scattering them across constructor arguments makes
it impossible to reproduce a result months later. We keep everything
in one immutable dataclass that is logged at the start of every run
and stored alongside the results.

Reproducibility rule (Lopez de Prado, *AFML* ch.11): a backtest
without its full config is not reproducible. We log the config to
JSON next to the equity curve so any reviewer can re-run the same
experiment exactly.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class BacktestConfig:
    """Frozen so a single run cannot accidentally mutate parameters.

    Every field carries a short rationale comment.
    """
    # ---- Pair and data range --------------------------------------
    pair: str = "EUR/USD"
    granularity: str = "M15"

    # The default range covers the most recent two years available
    # to OANDA's practice account. The runner clamps to whatever
    # OANDA actually returns, so an exact future date is fine.
    start_utc: Optional[datetime] = None     # None = 2 years before end
    end_utc: Optional[datetime] = None       # None = "now" at run time

    # ---- Account and sizing ---------------------------------------
    starting_equity: float = 10_000.0
    risk_per_trade_pct: float = 0.5          # of current equity
    pip_value_per_lot: float = 10.0          # USD per pip per std lot
    pair_pip: float = 0.0001                 # EUR/USD pip definition
    units_per_lot: int = 100_000

    # ---- Session filter (NY time) ---------------------------------
    # The system trades only during the high-liquidity windows:
    #   - 03:00-05:00 NY = London early session (good EUR/USD action)
    #   - 08:00-12:00 NY = London/NY overlap (highest EUR/USD vol +
    #                       most economic data releases)
    session_tz: str = "America/New_York"
    session_windows: tuple = (
        ("03:00", "05:00"),                  # window A
        ("08:00", "12:00"),                  # window B
    )

    # ---- News blackout --------------------------------------------
    # Block trade entries within ± `news_blackout_minutes_*` of any
    # tier-1 event. Open positions are managed by the monitor (which
    # may force-close before news per its own rules).
    news_blackout_minutes_pre: int = 15
    news_blackout_minutes_post: int = 15

    # ---- Costs ----------------------------------------------------
    # OANDA Practice has zero commission. OANDA Live spread-only:
    # commission ~0; commission account: $5 per round-turn-per lot.
    # We default to a conservative spread-only model that matches
    # what most retail traders actually pay.
    commission_per_lot_per_side: float = 0.0
    # Slippage in pips applied at entry and at stop hits. Limit
    # orders fill at the limit price (no slippage by definition).
    entry_slippage_pips: float = 0.5
    stop_slippage_pips: float = 1.0
    target_slippage_pips: float = 0.2   # realistic queue/partial slippage on TP
    # Sometimes spread is unavailable in the data; fall back to a
    # realistic average for EUR/USD.
    fallback_spread_pips: float = 0.5

    # ---- Risk caps ------------------------------------------------
    # Daily loss cap: stop trading for the day if the day's realised
    # P&L is below -daily_loss_cap_pct of starting-of-day equity.
    daily_loss_cap_pct: float = 3.0
    # Max drawdown cap: stop the whole backtest if equity drops
    # max_drawdown_cap_pct below the running peak. (Reproduces the
    # real-world kill switch a prudent operator would set.)
    max_drawdown_cap_pct: float = 15.0
    # Consecutive-loss cap: pause trading for the rest of the day
    # after this many consecutive losses (Steenbarger cooling-off).
    max_consecutive_losses: int = 3

    # ---- Walk-forward + holdout -----------------------------------
    # Reserve the last N percent of the period for out-of-sample
    # validation. The runner reports IS and OOS metrics separately.
    out_of_sample_pct: float = 30.0
    # Walk-forward window (in days) for the rolling SQN curve in the
    # analyzer. 60 = ~1 calendar quarter.
    walk_forward_days: int = 60

    # ---- Engine integration ---------------------------------------
    # Whether to feed each brain through its LLM wrapper during the
    # backtest. Default OFF because 1000+ trades * 5 LLM calls each
    # = several thousand $ in API costs. Use mechanical-only for
    # the main runs and optional LLM mode for spot validation.
    enable_llm: bool = False
    llm_model: str = "gpt-5"

    # ---- Output ---------------------------------------------------
    output_dir: str = "/app/NewsMind/state/backtest"
    save_equity_curve: bool = True
    save_trade_log: bool = True
    save_config_snapshot: bool = True

    # ---- Reproducibility ------------------------------------------
    seed: int = 42                           # for any tie-breaking RNG

    # ===============================================================
    # Helpers.
    # ===============================================================
    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("start_utc", "end_utc"):
            v = d.get(k)
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        return d

    def effective_end_utc(self) -> datetime:
        return self.end_utc or datetime.now(timezone.utc)
