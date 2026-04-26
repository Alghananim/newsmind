# -*- coding: utf-8 -*-
"""News gate — inherits NewsMind block + checks high-impact event proximity."""
from __future__ import annotations
from typing import Optional
from .models import BrainSummary


def check(news: Optional[BrainSummary]) -> dict:
    if news is None:
        return {"status": "block", "details": "no_news_input"}
    if news.permission == "block":
        return {"status": "block", "details": f"news_block:{news.reason[:80]}"}
    if news.permission == "wait":
        return {"status": "wait", "details": f"news_wait:{news.reason[:80]}"}
    # allow
    return {"status": "ok", "details": f"news_allow_grade={news.grade}"}
