# -*- coding: utf-8 -*-
"""MarketMind LLM wrapper — macro reasoning over cross-asset state.

The mechanical MarketMind already produces a `MarketContext` with
USD/EUR strength composites, a synthetic DXY, RORO, and a net bias +
strength. The LLM wrapper's job is to *interpret* that snapshot the
way a macro trader would talk through it before agreeing the read:

    "DXY is up but yields are flat — that's flow-driven, not rate-
     driven, so it's less durable. RORO is risk-off, which usually
     supports USD, fits. But SmartNoteBook says 'avoid post_event'
     and we're 35min into the FOMC window — wait."

Reasoning canon
---------------
    * Marc Chandler — *Making Sense of the Dollar*
    * Ashraf Laidi — *Currency Trading and Intermarket Analysis*
    * Soros — reflexivity (positioning matters as much as fundamentals)
    * Lo — adaptive markets (regimes shift; what worked last cycle
      may now be the contra-signal)

The LLM is asked to lower confidence (and possibly veto) when:
    - the mechanical bias relies on a single composite (DXY only,
      no yield confirmation)
    - the active narrative the system is running has not been
      validated by the data the wrapper sees
    - a SmartNoteBook lesson explicitly speaks to this regime

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
class LLMMacroVerdict:
    ok: bool
    net_bias: str                     # "long" | "short" | "neutral"
    bias_strength: float              # 0..1
    grade: str                        # "A+" | "A" | "B" | "C" | "F"
    confidence: float                 # 0..1
    halt_recommendation: bool
    halt_reason: str
    supporting_factors: list[str]
    opposing_factors: list[str]
    narrative: str
    raw_response: Optional[LLMResponse] = None

    @classmethod
    def from_failure(cls, error: str) -> "LLMMacroVerdict":
        return cls(
            ok=False, net_bias="neutral", bias_strength=0.0,
            grade="F", confidence=0.0,
            halt_recommendation=False, halt_reason="",
            supporting_factors=[], opposing_factors=[],
            narrative=f"LLM unavailable: {error}",
        )


# ----------------------------------------------------------------------
# Prompt pieces.
# ----------------------------------------------------------------------
_ROLE = (
    "You are MarketMind, the cross-asset macro brain of a EUR/USD "
    "trading system. You read DXY, RORO, USD/EUR strength composites, "
    "and yield/risk signals together — never one in isolation. You "
    "answer one question per cycle: does the macro picture support, "
    "oppose, or stay neutral on the proposed direction, and how "
    "strongly?"
)

_PRINCIPLES = """
1. No single-factor reads. A USD-up call must show in DXY AND yields
   AND RORO; a divergence is itself information.
2. Distinguish flow from fundamentals. A short-burst DXY rally on
   month-end flow is not a regime change.
3. Honour reflexivity (Soros). When positioning is one-sided, the
   incremental flow gets thinner — strength of the bias should be
   discounted.
4. Adaptive markets (Lo). What worked in last quarter's regime may
   now be the contra-signal; the SmartNoteBook lessons attached to
   regime cohorts are your guide here.
5. Halt when the data does not justify a directional view. Neutral
   with low confidence is the correct answer when composites disagree.
6. Never invent data. If a composite is unavailable, say so in the
   narrative and reduce confidence accordingly.
""".strip()

_SCHEMA = {
    "type": "object",
    "required": [
        "net_bias", "bias_strength", "grade", "confidence",
        "halt_recommendation", "halt_reason",
        "supporting_factors", "opposing_factors", "narrative",
    ],
    "properties": {
        "net_bias": {"type": "string", "enum": ["long", "short", "neutral"]},
        "bias_strength": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "grade": {"type": "string", "enum": ["A+", "A", "B", "C", "F"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "halt_recommendation": {"type": "boolean"},
        "halt_reason": {"type": "string"},
        "supporting_factors": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "opposing_factors": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "narrative": {"type": "string", "maxLength": 1500},
    },
}

_SCHEMA_EXPLANATION = """
- `net_bias`: long = USD weakness/EUR strength = bullish EUR/USD;
  short = the opposite; neutral when composites disagree.
- `bias_strength`: 0..1 — how strongly the cross-asset picture
  supports the bias. Use the mechanical bias_strength as a starting
  point; raise/lower based on confluence/divergence you see.
- `grade` + `confidence`: A+>=0.80, A>=0.65, B>=0.50, C>=0.35, F<0.35.
- `halt_recommendation`: set true ONLY when conditions actively argue
  for not trading EUR/USD now (e.g. central-bank speakers in the next
  15 min and your composites disagree).
- `halt_reason`: one short sentence; required when halt_recommendation
  is true.
- `supporting_factors` / `opposing_factors`: short bullet phrases
  (e.g. "DXY +0.4% intraday", "10y yields flat — flow-driven move").
- `narrative`: 3-6 sentences. Tie the composites together; reference
  any active SmartNoteBook lesson you used.
""".strip()


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def think(*,
          mechanical_market_context: Any,
          injection_block_text: str = "",
          news_summary: str = "",
          client: LLMClient,
          cfg: Optional[LLMConfig] = None,
          now: Optional[datetime] = None,
          ) -> LLMMacroVerdict:
    """Reason over the MarketContext and return an augmented verdict."""
    mc = mechanical_market_context

    user_payload = {
        "mechanical_net_bias": getattr(mc, "net_bias", "neutral"),
        "mechanical_bias_strength": float(getattr(mc, "bias_strength", 0.0)),
        "halt_trading_mechanical": bool(getattr(mc, "halt_trading", False)),
        "halt_reason_mechanical": getattr(mc, "halt_reason", ""),
        "supporting_factors": list(getattr(mc, "supporting_factors", []) or []),
        "opposing_factors": list(getattr(mc, "opposing_factors", []) or []),
        "summary_one_liner": getattr(mc, "summary_one_liner", ""),
        "narrative_mechanical": getattr(mc, "narrative", ""),
        "usd_strength": _safe_to_dict(getattr(mc, "usd_strength", None)),
        "eur_strength": _safe_to_dict(getattr(mc, "eur_strength", None)),
        "dxy_synthetic": _safe_to_dict(getattr(mc, "dxy_synthetic", None)),
        "roro": _safe_to_dict(getattr(mc, "roro", None)),
        "news_summary": news_summary,
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
        return LLMMacroVerdict.from_failure(resp.error or "no data")
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


def _parse(resp: LLMResponse) -> LLMMacroVerdict:
    d = resp.data or {}
    nb = str(d.get("net_bias", "neutral")).lower()
    if nb not in ("long", "short", "neutral"):
        nb = "neutral"
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
    return LLMMacroVerdict(
        ok=True,
        net_bias=nb,
        bias_strength=bs,
        grade=grade,
        confidence=conf,
        halt_recommendation=bool(d.get("halt_recommendation", False)),
        halt_reason=str(d.get("halt_reason", "") or "")[:500],
        supporting_factors=[str(x)[:200] for x in
                            (d.get("supporting_factors") or [])][:8],
        opposing_factors=[str(x)[:200] for x in
                          (d.get("opposing_factors") or [])][:8],
        narrative=str(d.get("narrative", "") or "")[:2000],
        raw_response=resp,
    )
