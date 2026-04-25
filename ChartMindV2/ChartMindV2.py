# -*- coding: utf-8 -*-
"""ChartMindV2 — the rebuilt orchestrator.

Replaces the v1 pattern-matching pipeline with a 6-factor confluence
system. Honest contract identical to v1 so the runner / Engine can
drop it in.

Pipeline per analyze() call:
    1. Trend (multi-timeframe)
    2. Structure (S/R levels)
    3. Candle (patterns at structure)
    4. Momentum (RSI/MACD/divergence)
    5. Regime (delegated to Backtest.regime if available)
    6. Confluence scoring (6 factors)
    7. Plan generation (SL/TP/RR)
    8. Grade assignment (A+/A/B/C)

Output: AnalysisV2 with .actionable, .directive, and .plan compatible
with v1 callers.
"""
from __future__ import annotations
import uuid
from typing import Optional

from .models import (AnalysisV2, TradePlan, RegimeReading)
from .trend import TrendAnalyzer
from .structure import StructureAnalyzer
from .candles import CandleAnalyzer
from .momentum import MomentumAnalyzer
from .confluence import ConfluenceScorer
from .planner import EntryPlanner
from .grade import assign_grade


class ChartMindV2:
    """Drop-in replacement for ChartMind. Same .analyze(df, ...) interface.

    Optional:
        min_grade — reject plans below this grade ("C" = no filter,
                    "B" = require >=B, etc.). Default "B" — only B/A/A+.
    """
    name = "ChartMindV2"
    version = "2.0.0"

    def __init__(self,
                 pair_pip: float = 0.0001,
                 min_confluence: float = 4.0,
                 min_grade: str = "B",
                 min_rr: float = 2.0):
        self.pair_pip = pair_pip
        self.min_confluence = min_confluence
        self.min_grade = min_grade
        self.min_rr = min_rr

        self.trend = TrendAnalyzer()
        self.structure = StructureAnalyzer(pair_pip=pair_pip)
        self.candles = CandleAnalyzer(pair_pip=pair_pip)
        self.momentum = MomentumAnalyzer()
        self.scorer = ConfluenceScorer(
            min_confluence=min_confluence, pair_pip=pair_pip)
        self.planner = EntryPlanner(pair_pip=pair_pip, min_rr=min_rr)

        # Regime detector — try the existing one, fall back to None
        self._regime_detector = None
        try:
            from Backtest.regime import RegimeDetector
            self._regime_detector = RegimeDetector(pair_pip=pair_pip)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public: same interface as v1.
    # ------------------------------------------------------------------
    def analyze(self, df_or_bars, pair: str = "EUR_USD",
                pair_pip: float = 0.0001) -> AnalysisV2:
        """Accepts either:
          - a pandas DataFrame with columns open/high/low/close (v1 style)
          - a list of BacktestBar-like objects (v2 native)
        """
        if pair_pip and pair_pip != self.pair_pip:
            self.pair_pip = pair_pip
            self.structure.pair_pip = pair_pip
            self.candles.pair_pip = pair_pip
            self.scorer.pair_pip = pair_pip
            self.planner.pair_pip = pair_pip
            if self._regime_detector:
                try:
                    self._regime_detector.pair_pip = pair_pip
                except Exception:
                    pass

        bars = self._coerce_bars(df_or_bars)
        if len(bars) < 200:
            return AnalysisV2()  # not enough history

        # 1-2-3-4 Readings
        trend_r = self.trend.analyze_m15(bars)
        struct_r = self.structure.analyze(bars)
        candle_r = self.candles.analyze(bars, struct_r)
        mom_r = self.momentum.analyze(bars)

        # 5 Regime
        regime_r = None
        if self._regime_detector:
            try:
                rd = self._regime_detector.classify(bars)
                regime_r = RegimeReading(
                    label=rd.regime, adx=rd.adx,
                    atr_pips=rd.atr_short,
                    regime_confidence=rd.confidence,
                )
            except Exception:
                regime_r = None

        # 6 Confluence
        cur_price = bars[-1].close
        direction, score, breakdown = self.scorer.score(
            cur_price=cur_price,
            trend=trend_r, structure=struct_r,
            candle=candle_r, momentum=mom_r, regime=regime_r,
        )

        if direction == "none" or score < self.min_confluence:
            # No actionable setup
            empty = AnalysisV2(
                trend=trend_r, structure=struct_r, candle=candle_r,
                momentum=mom_r, regime=regime_r,
                plan=TradePlan(
                    setup_type="v2_no_setup", direction="neutral",
                    entry_price=cur_price, stop_price=cur_price,
                    target_price=cur_price, rr_ratio=0.0,
                    time_budget_bars=0, confidence=0.0,
                    rationale=f"confluence {score:.0f}/6 below threshold",
                    is_actionable=False,
                    reason_if_not="confluence_below_min",
                    confluence_score=score,
                    confluence_breakdown=breakdown,
                ),
            )
            return empty

        # 7 Plan
        plan = self.planner.plan(
            direction=direction, confluence_score=score,
            m15_bars=bars,
            trend=trend_r, structure=struct_r,
            candle=candle_r, momentum=mom_r, regime=regime_r,
        )
        if plan is None:
            return AnalysisV2(
                trend=trend_r, structure=struct_r, candle=candle_r,
                momentum=mom_r, regime=regime_r,
                plan=TradePlan(
                    setup_type="v2_geometry_failed", direction=direction,
                    entry_price=cur_price, stop_price=cur_price,
                    target_price=cur_price, rr_ratio=0.0,
                    time_budget_bars=0, confidence=0.0,
                    rationale="SL/TP geometry < min R:R",
                    is_actionable=False,
                    reason_if_not="geometry_failed",
                    confluence_score=score,
                    confluence_breakdown=breakdown,
                ),
            )

        plan.confluence_breakdown = breakdown
        plan.plan_id = uuid.uuid4().hex[:8]

        # 8 Grade
        plan.grade = assign_grade(plan)
        if not self._grade_passes(plan.grade):
            plan.is_actionable = False
            plan.reason_if_not = f"grade_below_min:{plan.grade}<{self.min_grade}"

        return AnalysisV2(
            plan=plan, trend=trend_r, structure=struct_r,
            candle=candle_r, momentum=mom_r, regime=regime_r,
        )

    # ------------------------------------------------------------------
    # Helpers.
    # ------------------------------------------------------------------
    def _coerce_bars(self, x) -> list:
        """Convert input to list of bar-like objects with .open/.high/.low/.close/.time."""
        if isinstance(x, list):
            return x
        # pandas DataFrame fallback
        try:
            import pandas as pd
            if isinstance(x, pd.DataFrame):
                bars = []
                for ts, row in x.iterrows():
                    bars.append(type("B", (), {
                        "time": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    })())
                return bars
        except Exception:
            pass
        return []

    def _grade_passes(self, g: str) -> bool:
        order = {"C": 0, "B": 1, "A": 2, "A+": 3}
        return order.get(g, 0) >= order.get(self.min_grade, 0)
