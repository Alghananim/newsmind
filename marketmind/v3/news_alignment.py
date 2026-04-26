# -*- coding: utf-8 -*-
"""News alignment — does the current price action match NewsMind's verdict?

Inputs:
    news_verdict (NewsMind NewsVerdict object or dict-like)
    market_direction (bullish/bearish/neutral/unclear)

Output:
    one of: aligned / divergent / no_news / blocked_by_news / news_caution

Used by permission_engine to:
  * Hard-block when NewsMind says block
  * Cap grade at B when news=wait
  * Treat divergence (news bullish but market bearish) as suspicious
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class NewsAlignment:
    label: str = "no_news"
    grade_cap: str = "A+"        # max grade allowed by news context
    rationale: str = ""


def assess(news_verdict, market_direction: str) -> NewsAlignment:
    if news_verdict is None:
        return NewsAlignment(label="no_news", grade_cap="A+",
                             rationale="no_news_verdict_provided")

    perm = getattr(news_verdict, "trade_permission", None) or news_verdict.get(
        "trade_permission", "wait")
    bias = getattr(news_verdict, "market_bias", None) or news_verdict.get(
        "market_bias", "unclear")
    risk = getattr(news_verdict, "risk_mode", None) or news_verdict.get(
        "risk_mode", "unclear")

    # 1. Hard-block from NewsMind
    if perm == "block":
        return NewsAlignment(label="blocked_by_news",
                             grade_cap="C",
                             rationale=f"news_block:{getattr(news_verdict,'reason','')}")

    # 2. NewsMind wait => cap grade at B
    if perm == "wait":
        return NewsAlignment(label="news_caution", grade_cap="B",
                             rationale=f"news_wait:{getattr(news_verdict,'reason','')}")

    # 3. NewsMind allow with bias — check alignment
    if bias == "bullish" and market_direction == "bullish":
        return NewsAlignment(label="aligned", grade_cap="A+", rationale="news_bull+market_bull")
    if bias == "bearish" and market_direction == "bearish":
        return NewsAlignment(label="aligned", grade_cap="A+", rationale="news_bear+market_bear")
    if bias in ("bullish","bearish") and market_direction in ("bullish","bearish"):
        return NewsAlignment(label="divergent", grade_cap="B",
                             rationale=f"news_{bias}+market_{market_direction}")
    if bias in ("neutral","unclear"):
        return NewsAlignment(label="no_clear_signal", grade_cap="A",
                             rationale="news_no_directional_bias")

    return NewsAlignment(label="no_news", grade_cap="A+", rationale="default")
