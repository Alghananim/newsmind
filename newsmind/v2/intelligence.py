# -*- coding: utf-8 -*-
"""IntelligenceLayer — converts a NewsItem into directional impact assessment.

Three classifier passes:
    1. EVENT_KEYWORDS — CPI/NFP/FOMC/ECB/BoJ/BoE etc.
    2. SURPRISE_PARSER — "X% vs Y% expected" / "+Xk vs +Yk"
    3. PROPAGATION — event+surprise → USD direction → per-pair direction
                     with risk-mode override (risk-off → JPY haven flow)

Conservative: cannot classify confidently → unclear → permission engine
biases to wait. Unverified social → caps confidence and forces wait.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional, Literal
from .models import NewsItem


Direction = Literal["up", "down", "neutral", "unclear"]
RiskMode  = Literal["risk_on", "risk_off", "unclear"]


@dataclass
class ImpactAssessment:
    usd_dir: Direction = "unclear"
    eur_dir: Direction = "unclear"
    jpy_dir: Direction = "unclear"
    eur_usd_dir: Direction = "unclear"
    usd_jpy_dir: Direction = "unclear"
    risk_mode: RiskMode = "unclear"
    market_bias_per_pair: dict = field(default_factory=dict)
    impact_level: str = "unknown"
    surprise_score: float = 0.0
    rationale: tuple = ()
    confidence: float = 0.0
    is_political_unverified: bool = False


_EVENT_KEYWORDS = [
    ("US_CPI",       ("us cpi", "cpi (us)", "u.s. cpi", "core cpi"), "high",   "up"),
    ("US_PPI",       ("us ppi", "ppi (us)", "u.s. ppi"),             "medium", "up"),
    ("US_NFP",       ("nfp", "nonfarm", "non-farm", "non farm"),     "high",   "up"),
    ("US_RETAIL",    ("retail sales (us)", "us retail"),             "medium", "up"),
    ("US_GDP",       ("us gdp", "gdp (us"),                          "high",   "up"),
    ("US_JOBLESS",   ("jobless claims",),                            "low",    "down"),
    ("FOMC",         ("fomc", "fed rate decision", "federal reserve rate"), "high", "up"),
    ("FED_SPEAKER",  ("powell", "fed chair", "fed governor", "fed official"), "medium", "up"),
    ("ECB_DECISION", ("ecb rate", "european central bank rate", "ecb hawkish",
                      "ecb dovish", "ecb hike", "ecb cut", "ecb unexpectedly"),
                                                                     "high",   "down"),
    ("BOJ_DECISION", ("boj rate", "bank of japan rate", "boj decision"), "high", "neutral"),
    ("BOE_DECISION", ("boe rate", "bank of england rate"),           "high",   "neutral"),
    ("UK_CPI",       ("uk cpi", "u.k. cpi"),                         "high",   "neutral"),
]

_HAWKISH = ("hawkish", "hike", "raise rates", "tighten", "above target",
            "more hikes", "further hikes", "restrictive")
_DOVISH  = ("dovish", "cut", "lower rates", "ease", "easing",
            "rate cuts", "accommodative", "below target")
_HOT_CUES  = ("hot", "stronger", "above expected", "above forecast",
              "beats", "beat estimates", "more than expected", "above consensus")
_COLD_CUES = ("soft", "weak", "weaker", "below expected", "below forecast",
              "miss", "missed estimates", "less than expected",
              "below consensus", "cooling")
_RISK_OFF_CUES = ("flight to safety", "haven", "safe haven", "risk off",
                  "risk-off", "selloff", "rout", "panic", "war", "escalation",
                  "sanctions", "geopolitical", "crisis", "crash", "vix spike")
_RISK_ON_CUES  = ("risk on", "risk-on", "rally", "stocks up", "appetite",
                  "bullish sentiment", "trade deal", "stimulus")
_INTERVENTION  = ("intervene", "intervention", "fx intervention",
                  "yen intervention", "sell dollar")
_UNVERIFIED_TYPES = ("social",)


_VS_RE = re.compile(r"([+\-]?\d+(?:\.\d+)?)\s*(?:k|%)?\s*vs\.?\s*"
                    r"([+\-]?\d+(?:\.\d+)?)", re.IGNORECASE)

def _has_any(text: str, words) -> bool:
    t = text.lower()
    return any(w in t for w in words)

def _parse_surprise(headline: str) -> Optional[float]:
    m = _VS_RE.search(headline)
    if not m: return None
    try:
        actual = float(m.group(1))
        forecast = float(m.group(2))
        if forecast == 0: return None
        return (actual - forecast) / abs(forecast)
    except Exception:
        return None


class IntelligenceLayer:
    def assess(self, item: NewsItem) -> ImpactAssessment:
        text = (item.headline or "") + " " + (item.body or "")
        text_l = text.lower()
        ass = ImpactAssessment()
        triggers = []

        is_social = item.source_type in _UNVERIFIED_TYPES
        is_unverified = item.confirmation_count < 2
        ass.is_political_unverified = is_social or (is_unverified and is_social)
        if ass.is_political_unverified:
            triggers.append("political_or_social_unverified")

        matched_event = None
        for name, kws, level, usd_when_hot in _EVENT_KEYWORDS:
            if _has_any(text_l, kws):
                matched_event = (name, level, usd_when_hot)
                ass.impact_level = level
                triggers.append(f"event:{name}")
                break

        surprise = _parse_surprise(text)
        if surprise is not None:
            ass.surprise_score = surprise
            triggers.append(f"surprise:{surprise:+.2f}")

        is_hot = _has_any(text_l, _HOT_CUES) or (surprise is not None and surprise > 0.05)
        is_cold = _has_any(text_l, _COLD_CUES) or (surprise is not None and surprise < -0.05)
        is_hawkish = _has_any(text_l, _HAWKISH)
        is_dovish = _has_any(text_l, _DOVISH)
        is_risk_off = _has_any(text_l, _RISK_OFF_CUES)
        is_risk_on = _has_any(text_l, _RISK_ON_CUES)
        is_intervention = _has_any(text_l, _INTERVENTION)

        # Surprise trumps cue keywords if both fire
        if surprise is not None:
            if surprise > 0.05: is_cold = False
            elif surprise < -0.05: is_hot = False

        if is_hot: triggers.append("hot")
        if is_cold: triggers.append("cold")
        if is_hawkish: triggers.append("hawkish")
        if is_dovish: triggers.append("dovish")
        if is_risk_off: triggers.append("risk_off_cue")
        if is_risk_on: triggers.append("risk_on_cue")
        if is_intervention: triggers.append("intervention")

        usd = "unclear"
        if matched_event:
            name, level, usd_when_hot = matched_event
            if usd_when_hot == "up":
                if is_hot or is_hawkish: usd = "up"
                elif is_cold or is_dovish: usd = "down"
            elif usd_when_hot == "down":
                if is_hot or is_hawkish: usd = "down"
                elif is_cold or is_dovish: usd = "up"
            elif usd_when_hot == "neutral":
                if is_hawkish: usd = "down"
                elif is_dovish: usd = "up"
            else:
                usd = "unclear"
        else:
            if is_hawkish: usd = "up"
            elif is_dovish: usd = "down"
            elif is_hot: usd = "up"
            elif is_cold: usd = "down"

        if is_risk_off:
            ass.risk_mode = "risk_off"
            triggers.append("risk_mode:off")
            if ass.impact_level in ("unknown", "low"):
                ass.impact_level = "high"
                triggers.append("risk_off_implies_high_impact")
            if usd == "unclear":
                usd = "up"
                triggers.append("usd_safe_haven_default")
        elif is_risk_on:
            ass.risk_mode = "risk_on"
            triggers.append("risk_mode:on")
        else:
            ass.risk_mode = "unclear"

        ass.usd_dir = usd
        if usd == "up": ass.eur_dir = "down"
        elif usd == "down": ass.eur_dir = "up"
        else: ass.eur_dir = "unclear"

        if ass.risk_mode == "risk_off":
            ass.jpy_dir = "up"
        elif ass.risk_mode == "risk_on":
            ass.jpy_dir = "down"
        elif is_intervention:
            ass.jpy_dir = "up"
        else:
            if usd == "up": ass.jpy_dir = "down"
            elif usd == "down": ass.jpy_dir = "up"
            else: ass.jpy_dir = "unclear"

        if ass.eur_dir == "up" and ass.usd_dir == "down": ass.eur_usd_dir = "up"
        elif ass.eur_dir == "down" and ass.usd_dir == "up": ass.eur_usd_dir = "down"
        elif ass.usd_dir == "up": ass.eur_usd_dir = "down"
        elif ass.usd_dir == "down": ass.eur_usd_dir = "up"
        else: ass.eur_usd_dir = "unclear"

        if ass.risk_mode == "risk_off":
            ass.usd_jpy_dir = "down"
        elif ass.risk_mode == "risk_on" and ass.usd_dir != "down":
            ass.usd_jpy_dir = "up"
        elif is_intervention:
            ass.usd_jpy_dir = "down"
        elif ass.usd_dir == "up" and ass.jpy_dir == "down": ass.usd_jpy_dir = "up"
        elif ass.usd_dir == "down" and ass.jpy_dir == "up": ass.usd_jpy_dir = "down"
        elif ass.usd_dir == "up": ass.usd_jpy_dir = "up"
        elif ass.usd_dir == "down": ass.usd_jpy_dir = "down"
        else: ass.usd_jpy_dir = "unclear"

        dir_to_bias = {"up": "bullish", "down": "bearish",
                       "neutral": "neutral", "unclear": "unclear"}
        ass.market_bias_per_pair = {
            "EUR/USD": dir_to_bias[ass.eur_usd_dir],
            "USD/JPY": dir_to_bias[ass.usd_jpy_dir],
        }

        conf = 0.0
        if matched_event: conf += 0.4
        if surprise is not None: conf += 0.2
        if item.confirmation_count >= 2: conf += 0.3
        if is_risk_off or is_risk_on: conf += 0.1
        if ass.is_political_unverified: conf = min(conf, 0.3)
        ass.confidence = round(conf, 2)

        ass.rationale = tuple(triggers)
        return ass


_DEFAULT = IntelligenceLayer()
def assess(item: NewsItem) -> ImpactAssessment:
    return _DEFAULT.assess(item)
