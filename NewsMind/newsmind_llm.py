# -*- coding: utf-8 -*-
"""NewsMind LLM wrapper — narrative reasoning over the news context.

The mechanical NewsMind already produces a `NewsContext` with the
event window state, regime, active narratives, recent signals,
net_bias, conviction, and a do_not_trade flag. The LLM wrapper's job
is to interpret the *narrative state* — what story is the market
telling itself this hour, and does the proposed trade direction agree
with it?

Reasoning canon
---------------
    * Robert Shiller — *Narrative Economics* (markets move on stories,
      not just facts; the same data can carry different prices under
      different narratives)
    * Kahneman — anchoring/availability/recency as the engines that
      make narratives sticky and dangerous
    * Taleb — *Black Swan*: tail events live in the news long before
      they live in the chart; we read for them deliberately
    * George Soros — reflexivity: the narrative shapes flows that
      shape the data the next narrative reads
    * Andersen et al (2003) — micro effects of macro announcements
      (the canonical empirical paper on FX response windows)

The LLM is asked to set `do_not_trade=true` when:
    - a tier-1 event is in the next ~30 minutes
    - the narrative is in the *unresolved* phase (data has dropped
      but the market hasn't picked a side)
    - SmartNoteBook flagged a news_state lesson that this trade falls
      into

Output is a single JSON object enforced via JSON-mode in LLMCore.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from LLMCore import (
    BrainPrompt,
    LLMClient,
    LLMConfig,
    LLMResponse,
    build_prompt,
)


# ----------------------------------------------------------------------
# Output container.
# ----------------------------------------------------------------------
@dataclass
class LLMNewsVerdict:
    ok: bool
    net_bias: str                     # "long" | "short" | "neutral"
    bias_strength: float              # 0..1
    conviction: str                   # "low" | "medium" | "high"
    grade: str                        # "A+" | "A" | "B" | "C" | "F"
    confidence: float                 # 0..1
    do_not_trade: bool
    do_not_trade_reason: str
    narrative: str
    active_themes: list[str]
    raw_response: Optional[LLMResponse] = None

    @classmethod
    def from_failure(cls, error: str) -> "LLMNewsVerdict":
        return cls(
            ok=False, net_bias="neutral", bias_strength=0.0,
            conviction="low", grade="F", confidence=0.0,
            do_not_trade=False, do_not_trade_reason="",
            narrative=f"LLM unavailable: {error}",
            active_themes=[],
        )


# ----------------------------------------------------------------------
# Prompt pieces.
# ----------------------------------------------------------------------
_ROLE = (
    "You are NewsMind, the news and narrative brain of a EUR/USD "
    "trading system. You read scheduled releases, unscheduled "
    "headlines, and the macro narratives the market is currently "
    "trading. You answer: what story is moving EUR/USD right now, "
    "does it support the proposed direction, and is there an "
    "imminent event that should keep us flat?"
)

_PRINCIPLES = """
1. Stories beat data points (Shiller). A small data miss inside a
   strong existing narrative produces a big move; a big miss against
   the narrative gets faded. Read the story first, the print second.
2. Honour the event window. Andersen et al show FX prints have a
   distinct pre-/at-/post-event microstructure. Inside the pre-window
   for any tier-1 event, set do_not_trade=true unless explicitly
   overridden by the trader's rules.
3. Watch for reflexivity (Soros). When the narrative starts to drive
   flows that drive the next data point, you are late to the trade —
   lower confidence.
4. Distinguish signal from chatter. A single Reuters headline does
   not move FX; a cluster of corroborating sources does. Reduce
   bias_strength when a "signal" comes from one outlet only.
5. Refuse to invent narratives. If the data is mixed, the right
   answer is conviction=low and a 1-2 sentence narrative explaining
   the ambiguity. Do not paper over disagreement.
6. SmartNoteBook lessons about news_state cohorts override your
   narrative-only read; if a lesson says "avoid post_event trades on
   trend_up regime" and the system is in that cohort, the lesson wins.
""".strip()

_SCHEMA = {
    "type": "object",
    "required": [
        "net_bias", "bias_strength", "conviction",
        "grade", "confidence",
        "do_not_trade", "do_not_trade_reason",
        "narrative", "active_themes",
    ],
    "properties": {
        "net_bias": {"type": "string", "enum": ["long", "short", "neutral"]},
        "bias_strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "conviction": {"type": "string", "enum": ["low", "medium", "high"]},
        "grade": {"type": "string", "enum": ["A+", "A", "B", "C", "F"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "do_not_trade": {"type": "boolean"},
        "do_not_trade_reason": {"type": "string"},
        "narrative": {"type": "string", "maxLength": 1500},
        "active_themes": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 6,
        },
    },
}

_SCHEMA_EXPLANATION = """
- `net_bias`: long = supports EUR strength / USD weakness; short = the
  opposite; neutral when stories cancel.
- `bias_strength`: 0..1; how strongly the news/narrative state pushes
  EUR/USD direction. Discount when only one source supports it.
- `conviction`: your own meta-confidence in the narrative read.
- `grade` + `confidence`: A+>=0.80, A>=0.65, B>=0.50, C>=0.35, F<0.35.
- `do_not_trade`: true when a tier-1 event window is active or when
  the narrative is unresolved post-event.
- `do_not_trade_reason`: one sentence; required when do_not_trade=true.
- `narrative`: 3-6 sentences. Name the dominant story and the
  evidence; reference any active SmartNoteBook lesson you used.
- `active_themes`: short tags (e.g. "fed_cut_repricing",
  "ecb_dovish_pivot", "risk_off_rotation").
""".strip()


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def think(*,
          mechanical_news_context: Any,
          injection_block_text: str = "",
          market_summary: str = "",
          client: LLMClient,
          cfg: Optional[LLMConfig] = None,
          now: Optional[datetime] = None,
          ) -> LLMNewsVerdict:
    """Reason over the NewsContext and return an augmented verdict."""
    nc = mechanical_news_context

    user_payload = {
        "mechanical_net_bias": getattr(nc, "net_bias", "neutral"),
        "mechanical_bias_strength": float(getattr(nc, "bias_strength", 0.0)),
        "mechanical_conviction": getattr(nc, "conviction", "low"),
        "mechanical_confidence": float(getattr(nc, "confidence", 0.0)),
        "do_not_trade_mechanical": bool(getattr(nc, "do_not_trade", False)),
        "do_not_trade_reason_mechanical": getattr(nc, "do_not_trade_reason", ""),
        "summary_one_liner": getattr(nc, "summary_one_liner", ""),
        "narrative_mechanical": getattr(nc, "narrative", ""),
        "window_state": _safe_to_dict(getattr(nc, "window_state", None)),
        "regime": _safe_to_dict(getattr(nc, "regime", None)),
        "active_narratives": [
            _safe_to_dict(n)
            for n in (getattr(nc, "active_narratives", []) or [])[:8]
        ],
        "recent_signals": [
            _safe_to_dict(s)
            for s in (getattr(nc, "signals_24h", []) or [])[:10]
        ],
        "next_event": _safe_to_dict(getattr(nc, "next_event", None)),
        "last_event": _safe_to_dict(getattr(nc, "last_event", None)),
        "market_summary": market_summary,
    }

    prompt: BrainPrompt = build_prompt(
        role=_ROLE,
        principles=_PRINCIPLES,
        schema=_SCHEMA,
        schema_explanation=_SCHEMA_EXPLANATION,
        user_payload=user_payload,
        injection_block_text=injection_block_text,
        generated_at=now or datetime.now(timezone.utc),
    )

    resp = client.complete_json(
        system=prompt.system, user=prompt.user, cfg=cfg,
    )
    if not resp.ok or resp.data is None:
        return LLMNewsVerdict.from_failure(resp.error or "no data")
    return _parse(resp)


# ----------------------------------------------------------------------
# Internals.
# ----------------------------------------------------------------------
def _safe_to_dict(o: Any) -> Any:
    if o is None:
        return None
    if isinstance(o, dict):
        return o
    if hasattr(o, "to_dict"):
        try:
            return o.to_dict()
        except Exception:
            pass
    if hasattr(o, "__dict__"):
        return {k: v for k, v in o.__dict__.items() if not k.startswith("_")}
    return str(o)


def _parse(resp: LLMResponse) -> LLMNewsVerdict:
    d = resp.data or {}
    nb = str(d.get("net_bias", "neutral")).lower()
    if nb not in ("long", "short", "neutral"):
        nb = "neutral"
    conv = str(d.get("conviction", "low")).lower()
    if conv not in ("low", "medium", "high"):
        conv = "low"
    grade = str(d.get("grade", "F"))
    if grade not in ("A+", "A", "B", "C", "F"):
        grade = "F"
    try:
        bs = max(0.0, min(1.0, float(d.get("bias_strength", 0.0))))
    except (TypeError, ValueError):
        bs = 0.0
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence", 0.0))))
    except (TypeError, ValueError):
        conf = 0.0
    return LLMNewsVerdict(
        ok=True,
        net_bias=nb,
        bias_strength=bs,
        conviction=conv,
        grade=grade,
        confidence=conf,
        do_not_trade=bool(d.get("do_not_trade", False)),
        do_not_trade_reason=str(d.get("do_not_trade_reason", "") or "")[:500],
        narrative=str(d.get("narrative", "") or "")[:2000],
        active_themes=[str(t)[:80] for t in (d.get("active_themes") or [])][:6],
        raw_response=resp,
    )
