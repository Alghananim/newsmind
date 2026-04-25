# -*- coding: utf-8 -*-
"""ChartMind LLM wrapper — turns the mechanical Analysis into reasoning.

ChartMind's mechanical pipeline already produces a `TradePlan` with
direction, entry, stop, target, RR, and confidence. The LLM wrapper's
job is to *think about that plan out loud* — the way an experienced
trader would walk through their own setup before submitting:

    "OK. Setup is breakout-pullback to the 50EMA, in a confirmed
     trend. The pullback is shallow (good — strong bid). But the
     spread is in p72 right now, and SmartNoteBook says 'avoid
     wide_spread cohort' with E -0.4R. So I want to A this, not A+.
     Also the pre-mortem flagged news_proximity 25min — I should
     veto rather than trade, because the breakout level is right
     where the algos will defend before the print."

The wrapper takes the mechanical Analysis, the SmartNoteBook injection
block, and (optionally) NewsContext + MarketContext, and returns a
structured `LLMVerdict` that Engine can use to *adjust* the BrainGrade
it builds for the gate.

Reasoning canon
---------------
The wrapper's system prompt asks the model to reason in the language
of these sources, which match ChartMind's mechanical priors:

    * Murphy — *Technical Analysis of the Financial Markets*
    * Brooks — *Trading Price Action*
    * Nison — Japanese Candlestick Charting
    * Wyckoff — accumulation/distribution phases
    * ICT/SMC — order blocks, FVGs, BSL/SSL, MSB
    * Aronson — *Evidence-Based TA* (forces the model to discount
      patterns without sample-size support)
    * Steenbarger — process discipline ("would I take this trade
      if I had no position and no PnL today?")

The model is instructed to set `confidence` LOW when:
    - the mechanical plan is contradicted by SmartNoteBook lessons
    - news/market injection blocks suggest deferring
    - the pre-mortem flagged a high-severity failure mode that the
      proposed plan does not address

Output is a single JSON object; LLMCore handles the parse + retry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
class LLMVerdict:
    """LLM-augmented chart verdict.

    Engine compares this against the mechanical TradePlan to:
        - downgrade BrainGrade if the LLM flags a problem
        - veto outright if `veto=True`
        - inherit the LLM's narrative for the gate ledger
    """
    ok: bool                          # False = LLM call failed; fall back
    direction: str                    # "long" | "short" | "neutral"
    grade: str                        # "A+" | "A" | "B" | "C" | "F"
    confidence: float                 # 0..1
    veto: bool
    veto_reason: str
    rationale: str
    addresses_pre_mortem: bool        # did the model engage with it?
    addresses_lessons: bool           # did the model engage with them?
    raw_response: Optional[LLMResponse] = None

    @classmethod
    def from_failure(cls, error: str) -> "LLMVerdict":
        return cls(
            ok=False, direction="neutral", grade="F",
            confidence=0.0, veto=False, veto_reason="",
            rationale=f"LLM unavailable: {error}",
            addresses_pre_mortem=False, addresses_lessons=False,
        )


# ----------------------------------------------------------------------
# Prompt pieces.
# ----------------------------------------------------------------------
_ROLE = (
    "You are ChartMind, the technical-analysis brain of a EUR/USD "
    "trading system. You reason like an experienced discretionary "
    "trader who has internalised price action, ICT/SMC, Wyckoff, and "
    "evidence-based technical analysis. You are NOT trying to predict "
    "the next bar — you are deciding whether the proposed plan from "
    "the mechanical pipeline is worth taking, in this exact context."
)

_PRINCIPLES = """
1. Trade structure, not opinion. Honor the price-action read; if the
   structural level the plan depends on is weak, lower confidence.
2. Apply Aronson's evidence bar: a setup without sample-size support
   is a story, not an edge. SmartNoteBook lessons override your prior
   when they speak to this exact cohort.
3. Honour pre-mortem warnings. If the pre-mortem flagged a high-
   severity failure mode (news, spread, regime), explicitly explain
   how the proposed plan does or does not address it.
4. Refuse to "result". Your grade reflects the *process* (Mark Douglas):
   even if the last 3 trades on this setup won, the right grade is
   based on whether *this* setup, *now*, fits the rules.
5. Never invent prices, levels, or numbers. If the mechanical plan
   gave entry/stop/target, work with those; don't propose new ones.
6. Default to caution. If anything is unclear, lower confidence and
   explain why in `rationale`. A B-grade with honest reasoning beats
   an A+ with hand-waving.
""".strip()

_SCHEMA = {
    "type": "object",
    "required": [
        "direction", "grade", "confidence",
        "veto", "veto_reason",
        "rationale",
        "addresses_pre_mortem", "addresses_lessons",
    ],
    "properties": {
        "direction": {
            "type": "string",
            "enum": ["long", "short", "neutral"],
        },
        "grade": {"type": "string", "enum": ["A+", "A", "B", "C", "F"]},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "veto": {"type": "boolean"},
        "veto_reason": {"type": "string"},
        "rationale": {"type": "string", "maxLength": 1500},
        "addresses_pre_mortem": {"type": "boolean"},
        "addresses_lessons": {"type": "boolean"},
    },
}

_SCHEMA_EXPLANATION = """
- `direction`: your verdict on the trade direction. "neutral" means do
  not take the proposed trade in either direction.
- `grade` + `confidence`: your A+/A/B/C/F grade and the equivalent
  numeric confidence in [0..1]. Use the mapping A+>=0.80, A>=0.65,
  B>=0.50, C>=0.35, F<0.35.
- `veto` + `veto_reason`: set veto=true ONLY if you would refuse the
  trade outright; veto_reason must be a single short sentence.
- `rationale`: 3-6 sentences. Walk through the read in the same order
  a careful trader would: structure → confluence → contraindications
  → final verdict. Reference SmartNoteBook lessons by name when you
  use them.
- `addresses_pre_mortem`: true if you engaged with any pre-mortem
  warning in the injection block. Honesty signal — set false if the
  injection had warnings you ignored.
- `addresses_lessons`: same, for the active lessons.
""".strip()


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def think(*,
          mechanical_analysis: Any,
          injection_block_text: str = "",
          news_summary: str = "",
          market_summary: str = "",
          pre_mortem_summary: str = "",
          client: LLMClient,
          cfg: Optional[LLMConfig] = None,
          now: Optional[datetime] = None,
          ) -> LLMVerdict:
    """Run the LLM over a mechanical Analysis and return a verdict.

    `mechanical_analysis` is the ChartMind.analyze() result; we read
    its `plan` (TradePlan), `clarity`, `confluence`, `directive`. The
    function tolerates duck typing — anything missing becomes "n/a"
    in the prompt.

    `injection_block_text` is the verbatim text from
    `SmartNoteBook.memory_injector.inject_into_brain('chartmind', ...)`
    — committed lessons, recent biases, warnings, and (optional)
    pre-mortem.
    """
    plan = getattr(mechanical_analysis, "plan", None)
    clarity = getattr(mechanical_analysis, "clarity", None)
    confluence = getattr(mechanical_analysis, "confluence", None)

    user_payload = {
        "mechanical_directive": getattr(mechanical_analysis, "directive", "n/a"),
        "mechanical_actionable": bool(getattr(mechanical_analysis, "actionable", False)),
        "plan": _summarise_plan(plan),
        "clarity": _summarise_named(clarity, ("verdict", "score", "reason")),
        "confluence": _summarise_named(
            confluence,
            ("score", "factors", "conflicts", "narrative"),
        ),
        "news_summary": news_summary,
        "market_summary": market_summary,
        "pre_mortem_summary": pre_mortem_summary,
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
        return LLMVerdict.from_failure(resp.error or "no data")
    return _parse(resp)


# ----------------------------------------------------------------------
# Internals.
# ----------------------------------------------------------------------
def _summarise_plan(plan: Any) -> dict:
    if plan is None:
        return {}
    fields = (
        "setup_type", "direction", "entry_price", "stop_price",
        "target_price", "rr_ratio", "time_budget_bars",
        "confidence", "rationale", "is_actionable", "reason_if_not",
    )
    return {f: getattr(plan, f, None) for f in fields}


def _summarise_named(obj: Any, fields: tuple[str, ...]) -> dict:
    if obj is None:
        return {}
    out: dict[str, Any] = {}
    for f in fields:
        v = getattr(obj, f, None)
        if v is None:
            continue
        # Stringify objects so JSON serialisation never fails.
        if isinstance(v, (str, int, float, bool, list, dict)):
            out[f] = v
        else:
            out[f] = str(v)
    return out


def _parse(resp: LLMResponse) -> LLMVerdict:
    d = resp.data or {}
    direction = str(d.get("direction", "neutral")).lower()
    if direction not in ("long", "short", "neutral"):
        direction = "neutral"
    grade = str(d.get("grade", "F"))
    if grade not in ("A+", "A", "B", "C", "F"):
        grade = "F"
    try:
        confidence = float(d.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return LLMVerdict(
        ok=True,
        direction=direction,
        grade=grade,
        confidence=confidence,
        veto=bool(d.get("veto", False)),
        veto_reason=str(d.get("veto_reason", "") or "")[:500],
        rationale=str(d.get("rationale", "") or "")[:2000],
        addresses_pre_mortem=bool(d.get("addresses_pre_mortem", False)),
        addresses_lessons=bool(d.get("addresses_lessons", False)),
        raw_response=resp,
    )
