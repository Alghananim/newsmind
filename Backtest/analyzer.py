# -*- coding: utf-8 -*-
"""BacktestAnalyzer — produce the report that decides if we deploy or not.

Coverage
--------
The analyzer produces a `AnalysisReport` with three layers of detail:

    1. **Headline metrics** (Tharp/Carver): expectancy, SQN, profit
       factor, win rate, max DD, longest streak, average bars-held.

    2. **Stability checks** (Lopez de Prado, *AFML* ch.11-13):
        * Walk-forward SQN curve (rolling 60-day window).
        * In-sample vs out-of-sample comparison.
        * Per-month performance table to detect "lucky quarter"
          patterns.

    3. **Cohort analysis**: per setup_type, per market_regime, per
       hour bucket, per news_state. Catches the "system works in
       trend, blows up in chop" failure mode.

Outputs are pure Python — no matplotlib required. The analyzer
returns text reports the operator can paste into a notebook or print.
For visualisation, call `equity_curve_ascii()` to get a console plot.

Reasoning canon
---------------
    * Van Tharp — *Trade Your Way to Financial Freedom*: "the system
      with the highest expectancy is rarely the system with the
      highest single-year return — it is the system whose returns
      are *durable* across regimes."
    * David Aronson — *Evidence-Based TA*: a positive backtest result
      is necessary but not sufficient. Out-of-sample stability is
      the test that actually matters.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from .runner import BacktestResult


# ----------------------------------------------------------------------
# Output dataclass.
# ----------------------------------------------------------------------
@dataclass
class AnalysisReport:
    # ---- headline ---------------------------------------------------
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    expectancy_r: float
    avg_win_r: float
    avg_loss_r: float
    profit_factor: float
    sqn: float
    sqn_label: str
    max_dd_pct: float
    longest_winning_streak: int
    longest_losing_streak: int
    avg_bars_held: float
    final_equity: float
    starting_equity: float
    total_return_pct: float
    annualised_return_pct: float

    # ---- stability --------------------------------------------------
    in_sample: dict = field(default_factory=dict)
    out_of_sample: dict = field(default_factory=dict)
    monthly: list = field(default_factory=list)        # [(YYYY-MM, pnl, n)]
    walk_forward_sqn: list = field(default_factory=list)   # [(end_date, sqn)]

    # ---- cohorts ----------------------------------------------------
    by_setup: dict = field(default_factory=dict)
    by_hour: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)
    by_news_state: dict = field(default_factory=dict)
    by_grade: dict = field(default_factory=dict)

    # ---- counters from runner --------------------------------------
    bars_seen: int = 0
    signals_generated: int = 0
    entries_filled: int = 0
    rejected_by_session: int = 0
    rejected_by_calendar: int = 0
    rejected_by_risk: int = 0
    rejected_by_unfilled_limit: int = 0
    halted_early: bool = False
    halt_reason: str = ""

    # ---- equity curve (sampled daily for compactness) --------------
    equity_curve: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["equity_curve"] = [
            (t.isoformat(), e) for t, e in self.equity_curve
        ]
        return d


# ----------------------------------------------------------------------
# The analyzer.
# ----------------------------------------------------------------------
class BacktestAnalyzer:
    """Build an AnalysisReport from a BacktestResult.

    Construction is lightweight; one analyzer per result.
    """

    def __init__(self, result: BacktestResult,
                 *, oos_pct: float = 30.0,
                 walk_forward_days: int = 60):
        self.result = result
        self._oos_pct = oos_pct
        self._wf_days = walk_forward_days

    # ==================================================================
    # Public entry point.
    # ==================================================================
    def analyze(self) -> AnalysisReport:
        trades = self._read_trades()
        if not trades:
            return self._empty_report(reason="no trades")

        # ---- Headline ----------------------------------------------
        n = len(trades)
        rs = [t.outcome.r_multiple for t in trades]
        wins = [r for r in rs if r > 0.05]
        losses = [r for r in rs if r < -0.05]
        win_rate = len(wins) / n
        avg_win_r = statistics.fmean(wins) if wins else 0.0
        avg_loss_r = abs(statistics.fmean(losses)) if losses else 0.0
        expectancy_r = win_rate * avg_win_r - (1 - win_rate) * avg_loss_r

        sum_wins = sum(wins)
        sum_losses_abs = abs(sum(losses))
        profit_factor = (sum_wins / sum_losses_abs
                         if sum_losses_abs > 0
                         else float("inf") if sum_wins > 0 else 0.0)

        sqn = self._sqn(rs)
        sqn_label = self._sqn_label(sqn, n)

        max_dd_pct = self.result.max_drawdown_pct
        win_streak, lose_streak = self._streaks(rs)
        avg_bars_held = statistics.fmean(t.outcome.bars_held for t in trades)

        starting = self.result.starting_equity
        final = self.result.final_equity
        total_return_pct = (final - starting) / starting * 100.0
        years = max(1e-6, (self.result.ended_at - self.result.started_at).days / 365.25)
        annualised_return_pct = (
            ((final / starting) ** (1.0 / years) - 1.0) * 100.0
            if final > 0 else -100.0
        )

        # ---- Stability ---------------------------------------------
        is_split = int(n * (1 - self._oos_pct / 100.0))
        in_sample = self._cohort_stats(trades[:is_split])
        out_of_sample = self._cohort_stats(trades[is_split:])
        monthly = self._monthly_breakdown(trades)
        wf = self._walk_forward(trades)

        # ---- Cohorts -----------------------------------------------
        by_setup = self._group_stats(trades, lambda t: t.setup_type)
        by_hour = self._group_stats(trades, lambda t: self._hour_bucket(t))
        by_regime = self._group_stats(trades, lambda t: t.market_regime or "unknown")
        by_news = self._group_stats(trades, lambda t: t.news_state or "calm")
        by_grade = self._group_stats(trades, lambda t: self._grade_bucket(t))

        # Equity curve sampled daily
        equity_daily = self._sample_equity_daily()

        return AnalysisReport(
            n_trades=n, n_wins=len(wins), n_losses=len(losses),
            win_rate=win_rate, expectancy_r=expectancy_r,
            avg_win_r=avg_win_r, avg_loss_r=avg_loss_r,
            profit_factor=profit_factor,
            sqn=sqn, sqn_label=sqn_label,
            max_dd_pct=max_dd_pct,
            longest_winning_streak=win_streak,
            longest_losing_streak=lose_streak,
            avg_bars_held=avg_bars_held,
            final_equity=final, starting_equity=starting,
            total_return_pct=total_return_pct,
            annualised_return_pct=annualised_return_pct,
            in_sample=in_sample, out_of_sample=out_of_sample,
            monthly=monthly, walk_forward_sqn=wf,
            by_setup=by_setup, by_hour=by_hour,
            by_regime=by_regime, by_news_state=by_news,
            by_grade=by_grade,
            bars_seen=self.result.bars_seen,
            signals_generated=self.result.signals_generated,
            entries_filled=self.result.entries_filled,
            rejected_by_session=self.result.entries_rejected_by_session,
            rejected_by_calendar=self.result.entries_rejected_by_calendar,
            rejected_by_risk=self.result.entries_rejected_by_risk,
            rejected_by_unfilled_limit=self.result.entries_rejected_by_unfilled_limit,
            halted_early=self.result.halted_early,
            halt_reason=self.result.halt_reason,
            equity_curve=equity_daily,
        )

    # ==================================================================
    # Render helpers.
    # ==================================================================
    def text_report(self, report: Optional[AnalysisReport] = None) -> str:
        r = report or self.analyze()
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append(f" BACKTEST RESULTS — {self.result.config.pair}")
        lines.append(f" {self.result.started_at.date()} → {self.result.ended_at.date()}")
        lines.append("=" * 70)
        lines.append("")
        lines.append("HEADLINE")
        lines.append("--------")
        lines.append(f"  Trades:        {r.n_trades}  ({r.n_wins}W / {r.n_losses}L)")
        lines.append(f"  Win rate:      {r.win_rate*100:.1f}%")
        lines.append(f"  Expectancy:    {r.expectancy_r:+.3f}R")
        lines.append(f"  Avg win:       {r.avg_win_r:+.2f}R    Avg loss: {r.avg_loss_r:.2f}R")
        lines.append(f"  Profit factor: {r.profit_factor:.2f}")
        lines.append(f"  SQN:           {r.sqn:.2f}  ({r.sqn_label})")
        lines.append(f"  Max DD:        {r.max_dd_pct:.2f}%")
        lines.append(f"  Streaks:       longest win {r.longest_winning_streak}, "
                     f"longest loss {r.longest_losing_streak}")
        lines.append(f"  Avg bars held: {r.avg_bars_held:.1f}")
        lines.append("")
        lines.append("EQUITY")
        lines.append("------")
        lines.append(f"  Starting:      ${r.starting_equity:,.2f}")
        lines.append(f"  Ending:        ${r.final_equity:,.2f}")
        lines.append(f"  Total return:  {r.total_return_pct:+.2f}%")
        lines.append(f"  Annualised:    {r.annualised_return_pct:+.2f}% / year")
        if r.halted_early:
            lines.append(f"  HALTED EARLY:  {r.halt_reason}")
        lines.append("")
        lines.append("RUNNER COUNTERS")
        lines.append("---------------")
        lines.append(f"  Bars seen:                    {r.bars_seen}")
        lines.append(f"  Signals generated:            {r.signals_generated}")
        lines.append(f"  Entries filled:               {r.entries_filled}")
        lines.append(f"  Rejected by session filter:   {r.rejected_by_session}")
        lines.append(f"  Rejected by news calendar:    {r.rejected_by_calendar}")
        lines.append(f"  Rejected by risk manager:     {r.rejected_by_risk}")
        lines.append(f"  Limit orders not filled:      {r.rejected_by_unfilled_limit}")
        lines.append("")
        lines.append("STABILITY (in-sample vs out-of-sample)")
        lines.append("--------------------------------------")
        lines.append(f"  IS  (first {100-self._oos_pct:.0f}%): "
                     f"n={r.in_sample.get('n', 0)}, "
                     f"E={r.in_sample.get('expectancy_r', 0):+.3f}R, "
                     f"SQN={r.in_sample.get('sqn', 0):.2f}, "
                     f"WR={r.in_sample.get('win_rate', 0)*100:.0f}%")
        lines.append(f"  OOS (last  {self._oos_pct:.0f}%):  "
                     f"n={r.out_of_sample.get('n', 0)}, "
                     f"E={r.out_of_sample.get('expectancy_r', 0):+.3f}R, "
                     f"SQN={r.out_of_sample.get('sqn', 0):.2f}, "
                     f"WR={r.out_of_sample.get('win_rate', 0)*100:.0f}%")
        if r.in_sample.get("expectancy_r", 0) > 0 > r.out_of_sample.get("expectancy_r", 0):
            lines.append("  WARNING: Out-of-sample expectancy negative — possible overfitting.")
        lines.append("")
        if r.monthly:
            lines.append("MONTHLY P&L")
            lines.append("-----------")
            for ym, pnl, n_ in r.monthly:
                bar = ("+" * max(0, int(pnl / 100))
                       if pnl >= 0
                       else "-" * max(0, int(-pnl / 100)))
                lines.append(f"  {ym}  ${pnl:>+10,.0f}  n={n_:>3}  {bar[:40]}")
            lines.append("")
        if r.by_grade:
            lines.append("")
            lines.append("BY GRADE (A+ / A / B / C, by ChartMind confidence)")
            lines.append("-" * 52)
            for k in ("A+", "A", "B", "C"):
                if k in r.by_grade:
                    st = r.by_grade[k]
                    lines.append(
                        f"  {k:5s}  n={st['n']:>4d}  E={st['expectancy_r']:>+6.3f}R  "
                        f"WR={int(round(st['win_rate']*100)):>3d}%  PF={st.get('profit_factor', 0):>5.2f}"
                    )
        if r.by_setup:
            lines.append("BY SETUP_TYPE")
            lines.append("-------------")
            for k, st in sorted(r.by_setup.items(),
                                key=lambda kv: -kv[1].get("expectancy_r", 0)):
                lines.append(f"  {k:30s}  n={st['n']:>4}  "
                             f"E={st['expectancy_r']:+.3f}R  "
                             f"WR={st['win_rate']*100:.0f}%")
            lines.append("")
        if r.by_hour:
            lines.append("BY HOUR (UTC, 4-hour buckets)")
            lines.append("-----------------------------")
            for k in sorted(r.by_hour):
                st = r.by_hour[k]
                lines.append(f"  {k:>10s}  n={st['n']:>4}  "
                             f"E={st['expectancy_r']:+.3f}R  "
                             f"WR={st['win_rate']*100:.0f}%")
            lines.append("")
        return "\n".join(lines)

    def equity_curve_ascii(self, *, width: int = 60, height: int = 12,
                           report: Optional[AnalysisReport] = None) -> str:
        """Tiny ASCII equity curve for the console."""
        r = report or self.analyze()
        if not r.equity_curve:
            return "(no equity samples)"
        eq = [v for _, v in r.equity_curve]
        emin, emax = min(eq), max(eq)
        if emax <= emin:
            return f"flat at {emin:.0f}"
        rows = []
        for h in range(height, 0, -1):
            level = emin + (emax - emin) * (h / height)
            line = []
            step = max(1, len(eq) // width)
            for i in range(0, len(eq), step):
                v = eq[i]
                ch = "*" if v >= level else " "
                line.append(ch)
            rows.append(f"{level:>8.0f} | {''.join(line)[:width]}")
        rows.append("         +" + "-" * width)
        return "\n".join(rows)

    # ==================================================================
    # Internals.
    # ==================================================================
    def _read_trades(self) -> list:
        if self.result.snb is None:
            return []
        try:
            return self.result.snb.journal.read_all()
        except Exception:
            return []

    def _sqn(self, rs: list[float]) -> float:
        n = len(rs)
        if n < 2:
            return 0.0
        try:
            sd = statistics.stdev(rs)
        except statistics.StatisticsError:
            return 0.0
        if sd == 0:
            return 0.0
        return math.sqrt(n) * statistics.fmean(rs) / sd

    def _sqn_label(self, value: float, n: int) -> str:
        if n < 30:
            return "insufficient_sample"
        if value < 1.6: return "below_average"
        if value < 2.0: return "average"
        if value < 2.5: return "good"
        if value < 3.0: return "excellent"
        return "superb"

    def _streaks(self, rs: list[float]) -> tuple[int, int]:
        win_cur = win_best = lose_cur = lose_best = 0
        for r in rs:
            if r > 0:
                win_cur += 1; lose_cur = 0
                win_best = max(win_best, win_cur)
            elif r < 0:
                lose_cur += 1; win_cur = 0
                lose_best = max(lose_best, lose_cur)
            else:
                win_cur = lose_cur = 0
        return win_best, lose_best

    def _cohort_stats(self, trades: list) -> dict:
        if not trades:
            return {"n": 0}
        rs = [t.outcome.r_multiple for t in trades]
        wins = [r for r in rs if r > 0.05]
        losses = [r for r in rs if r < -0.05]
        win_rate = len(wins) / len(rs)
        avg_win = statistics.fmean(wins) if wins else 0.0
        avg_loss = abs(statistics.fmean(losses)) if losses else 0.0
        sum_wins = sum(wins) if wins else 0.0
        sum_losses_abs = abs(sum(losses)) if losses else 0.0
        pf = (sum_wins / sum_losses_abs) if sum_losses_abs > 0 else (
            999.0 if sum_wins > 0 else 0.0)
        return {
            "n": len(rs),
            "win_rate": win_rate,
            "expectancy_r": win_rate * avg_win - (1 - win_rate) * avg_loss,
            "profit_factor": pf,
            "sqn": self._sqn(rs),
        }

    def _monthly_breakdown(self, trades: list) -> list:
        by_month: dict[str, list] = defaultdict(list)
        for t in trades:
            ym = t.closed_at.strftime("%Y-%m")
            by_month[ym].append(t.outcome.pnl_currency)
        return [(ym, sum(pnls), len(pnls))
                for ym, pnls in sorted(by_month.items())]

    def _walk_forward(self, trades: list) -> list:
        """Rolling-window SQN, sampled monthly."""
        if not trades:
            return []
        out: list[tuple[date, float]] = []
        end = trades[-1].closed_at.date()
        cur = trades[0].closed_at.date() + timedelta(days=self._wf_days)
        while cur <= end:
            window_start = cur - timedelta(days=self._wf_days)
            window_trades = [t for t in trades
                             if window_start <= t.closed_at.date() < cur]
            if len(window_trades) >= 5:
                rs = [t.outcome.r_multiple for t in window_trades]
                out.append((cur, self._sqn(rs)))
            cur += timedelta(days=15)   # sample every two weeks
        return out

    def _group_stats(self, trades: list, key_fn) -> dict:
        groups: dict[str, list] = defaultdict(list)
        for t in trades:
            groups[str(key_fn(t))].append(t)
        return {k: self._cohort_stats(v) for k, v in groups.items()}

    @staticmethod
    def _hour_bucket(t) -> str:
        h = t.opened_at.hour
        for lo, hi in [(0, 4), (4, 8), (8, 12), (12, 16), (16, 20), (20, 24)]:
            if lo <= h < hi:
                return f"{lo:02d}-{hi:02d}_UTC"
        return "unknown"

    @staticmethod
    def _grade_bucket(t) -> str:
        """Map plan_confidence to A+/A/B/C grade. Matches Engine defaults."""
        c = float(getattr(t, "plan_confidence", 0.0) or 0.0)
        if c >= 0.80: return "A+"
        if c >= 0.65: return "A"
        if c >= 0.50: return "B"
        return "C"

    def _sample_equity_daily(self) -> list:
        if not self.result.equity_curve:
            return []
        # Sample one point per day (last in day)
        per_day: dict[date, tuple[datetime, float]] = {}
        for ts, eq in self.result.equity_curve:
            per_day[ts.date()] = (ts, eq)
        return [v for _, v in sorted(per_day.items())]

    def _empty_report(self, reason: str) -> AnalysisReport:
        return AnalysisReport(
            n_trades=0, n_wins=0, n_losses=0, win_rate=0.0,
            expectancy_r=0.0, avg_win_r=0.0, avg_loss_r=0.0,
            profit_factor=0.0, sqn=0.0, sqn_label="no_data",
            max_dd_pct=0.0, longest_winning_streak=0,
            longest_losing_streak=0, avg_bars_held=0.0,
            final_equity=self.result.final_equity,
            starting_equity=self.result.starting_equity,
            total_return_pct=0.0, annualised_return_pct=0.0,
            bars_seen=self.result.bars_seen,
            halted_early=self.result.halted_early,
            halt_reason=self.result.halt_reason or reason,
        )
