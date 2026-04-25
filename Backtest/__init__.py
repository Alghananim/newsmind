# -*- coding: utf-8 -*-
"""Backtest — institutional-grade backtesting harness for the EUR/USD system.

Design principles
-----------------
This package is the truth-teller for the rest of the system. It must:

    1. Never look ahead. Every decision sees only bars [0..t], never
       beyond. We enforce this structurally via BacktestClock + the
       `bars_seen` slicing — not by convention.

    2. Charge real costs. Spread, slippage, and commission together
       eat 30-50% of edge in retail FX (Carver, *Systematic Trading*
       ch.14). We model all three from OANDA's actual bid/ask data
       plus deterministic slippage rules.

    3. Respect session and news constraints. The system trades only
       during NY 03:00-05:00 and 08:00-12:00 windows, Mon-Fri, and
       blacks out around tier-1 economic events.

    4. Detect overfitting. Walk-forward analysis (60-day windows) and
       out-of-sample holdout (last 30% of period) are non-negotiable.
       A backtest result without OOS validation is not a result
       (Lopez de Prado, *AFML* ch.11-12).

    5. Tell the truth even when ugly. The analyzer reports negative
       expectancy as plainly as positive. We never tune parameters
       to hit a profit target — that is the textbook overfitting
       failure mode (Aronson, *Evidence-Based TA*, ch.7).

Public API
----------
    from Backtest import (
        BacktestSession,                # NY trading hours filter
        HistoricalCalendar,             # NFP/CPI/FOMC/ECB blackouts
        CostModel,                      # spread + slippage + commission
        RiskManager,                    # daily loss cap + max DD
        BacktestData,                   # OANDA historical bars loader
        BacktestRunner,                 # the main engine
        BacktestAnalyzer,               # metrics + reports
        BacktestConfig,                 # one place for all knobs
    )

The package depends only on the rest of the system (Engine, GateMind,
SmartNoteBook, OandaAdapter); it adds no new external libraries.
"""
from __future__ import annotations

from .config import BacktestConfig
from .session import BacktestSession
from .calendar import HistoricalCalendar, CalendarEvent
from .costs import CostModel, FillResult
from .risk import RiskManager, RiskState, RiskVerdict
from .data import BacktestData
from .runner import BacktestRunner, BacktestResult
from .analyzer import BacktestAnalyzer, AnalysisReport

__all__ = [
    "BacktestConfig",
    "BacktestSession",
    "HistoricalCalendar", "CalendarEvent",
    "CostModel", "FillResult",
    "RiskManager", "RiskState", "RiskVerdict",
    "BacktestData",
    "BacktestRunner", "BacktestResult",
    "BacktestAnalyzer", "AnalysisReport",
]

__version__ = "1.0.0"
