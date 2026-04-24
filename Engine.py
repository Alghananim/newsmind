# -*- coding: utf-8 -*-
"""Engine - composes ChartMind + MarketMind + NewsMind.

Halt-first precedence: news > market > chart.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional


def _lazy_chartmind():
    try:
        from ChartMind import ChartMind as _CM  # type: ignore
        return _CM
    except ImportError:
        return None


def _lazy_marketmind():
    try:
        from MarketMind import MarketMind as _MM  # type: ignore
        return _MM
    except ImportError:
        return None


def _lazy_newsmind():
    try:
        from NewsMind import NewsMind as _NM  # type: ignore
        return _NM
    except ImportError:
        return None


@dataclass
class Decision:
    timestamp: datetime
    action: str
    reason: str
    chart_analysis: Any = None
    market_context: Any = None
    news_context: Any = None
    halt_sources: List[str] = field(default_factory=list)

    @classmethod
    def skip(cls, reason: str,
              halt_sources: Optional[List[str]] = None,
              timestamp: Optional[datetime] = None,
              news_context: Any = None,
              market_context: Any = None) -> "Decision":
        return cls(
            timestamp=timestamp or datetime.now(timezone.utc),
            action="skip", reason=reason,
            halt_sources=halt_sources or [],
            news_context=news_context, market_context=market_context,
        )


class Engine:
    def __init__(self,
                  chartmind_kwargs: Optional[dict] = None,
                  newsmind_config_dir: Optional[Path] = None,
                  enable_market: bool = True,
                  enable_news: bool = True):
        CM = _lazy_chartmind()
        self.cm = CM(**(chartmind_kwargs or {})) if CM is not None else None
        self.mm = None
        if enable_market:
            MM = _lazy_marketmind()
            if MM is not None:
                self.mm = MM()
        self.nm = None
        if enable_news:
            NM = _lazy_newsmind()
            if NM is not None:
                self.nm = NM(config_dir=newsmind_config_dir)

    def version_summary(self) -> dict:
        out = {}
        try:
            import ChartMind
            out["chartmind"] = getattr(ChartMind, "__version__", "unknown")
        except ImportError:
            pass
        if self.mm is not None:
            import MarketMind as _mm
            out["marketmind"] = getattr(_mm, "__version__", "unknown")
        if self.nm is not None:
            import NewsMind as _nm
            out["newsmind"] = getattr(_nm, "__version__", "unknown")
        return out

    def step(self, bar=None, bundle=None,
              now_utc: Optional[datetime] = None) -> Decision:
        now_utc = now_utc or datetime.now(timezone.utc)
        market_ctx = None
        if self.mm is not None and bundle is not None:
            try:
                market_ctx = self.mm.analyze(bundle)
            except Exception:
                market_ctx = None
        news_ctx = None
        if self.nm is not None:
            try:
                news_ctx = self.nm.context_now(now_utc)
            except Exception:
                news_ctx = None
        halt_sources = []
        halt_reason = None
        if news_ctx is not None and (
            news_ctx.do_not_trade or news_ctx.window_state.trading_halted
        ):
            halt_sources.append("news")
            halt_reason = (news_ctx.do_not_trade_reason or
                           news_ctx.window_state.window_reason or
                           "news halt")
        if market_ctx is not None and getattr(market_ctx, "halt_trading", False):
            halt_sources.append("market")
            if halt_reason is None:
                halt_reason = getattr(market_ctx, "halt_reason", "market halt")
        if halt_sources:
            return Decision.skip(
                reason=halt_reason or "halt",
                halt_sources=halt_sources, timestamp=now_utc,
                news_context=news_ctx, market_context=market_ctx,
            )
        chart_analysis = None
        if bar is not None and self.cm is not None:
            try:
                chart_analysis = self.cm.analyze(bar)
            except (TypeError, AttributeError):
                pass
        if chart_analysis is not None and market_ctx is not None:
            self._inject_market(chart_analysis, market_ctx)
        if chart_analysis is not None and news_ctx is not None:
            self._inject_news(chart_analysis, news_ctx, market_ctx)
        action = self._derive_action(chart_analysis, news_ctx, market_ctx)
        return Decision(
            timestamp=now_utc, action=action, reason="composed decision",
            chart_analysis=chart_analysis,
            market_context=market_ctx, news_context=news_ctx,
        )

    def _inject_market(self, chart_analysis, market_ctx) -> None:
        try:
            from MarketMind.integration import (
                make_market_factor, make_market_conflict, make_market_challenge,
            )
        except ImportError:
            return
        f = make_market_factor(market_ctx)
        if f is not None and hasattr(chart_analysis, "confluence_factors"):
            chart_analysis.confluence_factors.append(f)
        mc = make_market_conflict(market_ctx, chart_analysis)
        if mc is not None and hasattr(chart_analysis, "conflicts"):
            chart_analysis.conflicts.append(mc)
        mch = make_market_challenge(market_ctx)
        if mch is not None and hasattr(chart_analysis, "devils_advocate"):
            da = chart_analysis.devils_advocate
            if da is not None and hasattr(da, "challenges"):
                da.challenges.append(mch)

    def _inject_news(self, chart_analysis, news_ctx, market_ctx) -> None:
        try:
            from NewsMind.integration import (
                make_news_factor, make_news_conflict, make_news_challenge,
            )
        except ImportError:
            return
        f = make_news_factor(news_ctx, market_ctx)
        if f is not None and hasattr(chart_analysis, "confluence_factors"):
            chart_analysis.confluence_factors.append(f)
        nc = make_news_conflict(news_ctx, chart_analysis, market_ctx)
        if nc is not None and hasattr(chart_analysis, "conflicts"):
            chart_analysis.conflicts.append(nc)
        nch = make_news_challenge(news_ctx)
        if nch is not None and hasattr(chart_analysis, "devils_advocate"):
            da = chart_analysis.devils_advocate
            if da is not None and hasattr(da, "challenges"):
                da.challenges.append(nch)

    def _derive_action(self, chart_analysis, news_ctx, market_ctx) -> str:
        if news_ctx is not None and news_ctx.do_not_trade:
            return "skip"
        if chart_analysis is None:
            if news_ctx is not None and news_ctx.bias_strength >= 0.25:
                return news_ctx.net_bias if news_ctx.net_bias != "neutral" else "skip"
            return "skip"
        for attr in ("final_decision", "net_direction", "direction"):
            val = getattr(chart_analysis, attr, None)
            if val in ("long", "short", "skip", "flat", "neutral"):
                return "skip" if val in ("flat", "neutral") else val
        return "skip"
