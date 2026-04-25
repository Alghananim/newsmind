# -*- coding: utf-8 -*-
"""SmartNoteBook LLM grader — turns the post-mortem skeleton into a journal entry.

The mechanical post_mortem produces a `PostMortemReport` with a
quantitative skeleton (decision_quality_grade, outcome_quality_grade,
bullet lists, a one-sentence seed). The LLM grader takes that skeleton
plus the raw `TradeRecord` and produces *the journal entry the trader
would actually write* — concrete, prescriptive, and forced into one
sentence at the end.

Why a separate LLM step
-----------------------
Steenbarger's rule: "the journal must read like a person wrote it,
not a robot — otherwise it gets ignored within a week". The
mechanical skeleton is correct but flat; the LLM rewrite is what makes
the journal a tool the trader (and the future brains) will actually
read.

The grader is forbidden from changing the *grades* (decision_quality
and outcome_quality). Those are quantitative and must not drift to
fit a nicer narrative. The grader rewrites only the *prose* fields.

Reasoning canon
---------------
    * Steenbarger — *Daily Trading Coach*, lesson 1: "the journal is
      the trader's mirror; if the mirror lies, training fails."
    * Annie Duke — *Thinking in Bets*: separate decision quality from
      outcome quality in the prose, not just in the numbers.
    * Klein — pre-mortem calibration: name explicitly whether the
      pre-mortem's predicted failure was the one that fired.

Output is a single JSON object enforced via JSON-mode in LLMCore.
The orchestrator's `attach_llm_review()` consumes the parsed result.
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
class LLMReview:
    """Journal-quality narrative the orchestrator writes back to disk.

    Note: `decision_quality_grade` and `outcome_quality_grade` mirror
    the mechanical values verbatim — the grader is not allowed to
    change them.
    """
    ok: bool
    decision_quality_grade: int
    outcome_quality_grade: int
    what_went_right: str
    what_went_wrong: str
    what_id_change: str
    one_sentence_lesson: str
    tags: list[str]
    pre_mortem_calibration: str        # "correct" | "wrong" | "n/a"
    raw_response: Optional[LLMResponse] = None

    @classmethod
    def from_failure(cls, mech_dq: int, mech_oq: int,
                     mech_right: str, mech_wrong: str, mech_change: str,
                     mech_seed: str, mech_tags: list[str],
                     error: str) -> "LLMReview":
        # Fall back to the mechanical skeleton verbatim.
        return cls(
            ok=False,
            decision_quality_grade=mech_dq,
            outcome_quality_grade=mech_oq,
            what_went_right=mech_right,
            what_went_wrong=mech_wrong,
            what_id_change=mech_change,
            one_sentence_lesson=mech_seed,
            tags=list(mech_tags),
            pre_mortem_calibration="n/a",
        )


# ----------------------------------------------------------------------
# Prompt pieces.
# ----------------------------------------------------------------------
_ROLE = (
    "You are SmartNoteBook's review writer. A trade just closed. The "
    "mechanical post-mortem produced a quantitative skeleton; your "
    "job is to turn it into the journal entry the trader would "
    "actually write — concrete, prescriptive, and ending in one "
    "sentence. You may NOT change the decision-quality or outcome-"
    "quality grades; those are mechanical and final."
)

_PRINCIPLES = """
1. Honesty first (Steenbarger). Do not soften a sloppy trade just
   because the result was good — that is the resulting fallacy
   (Annie Duke). Conversely, do not over-criticise a sound process
   that had a bad outcome.
2. Prescriptive, not descriptive. Every "what went wrong" item
   should map to a concrete "what I'd change". Avoid generic advice
   like "be more patient"; demand specific rule changes.
3. One-sentence rule. The final lesson must be readable in one
   breath. If you cannot say it in one sentence you do not yet know
   what the lesson is.
4. Cite the pre-mortem. State explicitly whether the pre-mortem
   correctly named the failure mode that fired (or whether the trade
   succeeded as planned). Calibration of self-awareness over time
   depends on this.
5. Use the trader's vocabulary. The plan referenced specific
   structures (setup, regime, news state); reuse those terms rather
   than inventing new ones.
6. No clichés. Phrases like "live to fight another day" or "the
   market giveth" are signals you have run out of insight; rewrite.
""".strip()

_SCHEMA = {
    "type": "object",
    "required": [
        "decision_quality_grade", "outcome_quality_grade",
        "what_went_right", "what_went_wrong", "what_id_change",
        "one_sentence_lesson", "tags", "pre_mortem_calibration",
    ],
    "properties": {
        "decision_quality_grade": {"type": "integer", "minimum": 1, "maximum": 5},
        "outcome_quality_grade": {"type": "integer", "minimum": 1, "maximum": 5},
        "what_went_right": {"type": "string", "maxLength": 1500},
        "what_went_wrong": {"type": "string", "maxLength": 1500},
        "what_id_change": {"type": "string", "maxLength": 1500},
        "one_sentence_lesson": {"type": "string", "maxLength": 280},
        "tags": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 12,
        },
        "pre_mortem_calibration": {
            "type": "string",
            "enum": ["correct", "wrong", "n/a"],
        },
    },
}

_SCHEMA_EXPLANATION = """
- `decision_quality_grade`: 1..5; MUST equal the mechanical value
  shown in the input — do not change it.
- `outcome_quality_grade`: 1..5; MUST equal the mechanical value
  shown in the input — do not change it.
- `what_went_right`: 2-5 sentences. Specific to this trade.
- `what_went_wrong`: 2-5 sentences. If nothing went wrong, say
  exactly that — do not invent flaws.
- `what_id_change`: 2-4 sentences with concrete, action-bearing
  prescriptions (a rule change, a parameter tweak, a checklist
  addition). One per line.
- `one_sentence_lesson`: ≤ 25 words. The headline lesson, written so
  it could be screenshotted and pinned to a wall.
- `tags`: short labels for later cohort-mining (e.g.
  "setup:breakout_pullback", "regime:trend_up", "news:calm",
  "warning:lucky_bad_process"). Inherit the mechanical tags and add
  any narrative-specific ones.
- `pre_mortem_calibration`: "correct" if the pre-mortem named the
  failure mode that fired; "wrong" if the actual failure was a
  different mode; "n/a" if the trade succeeded or no pre-mortem ran.
""".strip()


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def grade(*,
          trade_record: Any,         # SmartNoteBook.TradeRecord
          post_mortem: Any,          # SmartNoteBook.PostMortemReport
          client: LLMClient,
          cfg: Optional[LLMConfig] = None,
          now: Optional[datetime] = None,
          ) -> LLMReview:
    """Run the LLM grader over a closed trade + its mechanical post-mortem.

    Returns an `LLMReview` ready for the orchestrator's
    `attach_llm_review()`. On any failure, returns a `LLMReview` whose
    fields mirror the mechanical skeleton — never blocks the journal.
    """
    mech_dq = int(getattr(post_mortem, "decision_quality_grade", 0) or 0)
    mech_oq = int(getattr(post_mortem, "outcome_quality_grade", 0) or 0)
    mech_right = "\n".join(getattr(post_mortem, "skeleton_what_went_right", []))
    mech_wrong = "\n".join(getattr(post_mortem, "skeleton_what_went_wrong", []))
    mech_change = "\n".join(getattr(post_mortem, "skeleton_what_id_change", []))
    mech_seed = str(getattr(post_mortem, "one_sentence_lesson_seed", "") or "")
    mech_tags = list(getattr(post_mortem, "suggested_tags", []) or [])

    user_payload = {
        "trade_record": _safe_to_dict(trade_record),
        "post_mortem_skeleton": {
            "decision_quality_grade": mech_dq,
            "outcome_quality_grade": mech_oq,
            "went_as_planned": getattr(post_mortem, "went_as_planned", None),
            "delta_from_plan_pips": getattr(post_mortem, "delta_from_plan_pips", None),
            "pre_mortem_was_correct": getattr(post_mortem, "pre_mortem_was_correct", None),
            "pre_mortem_top_risk_fired": getattr(post_mortem, "pre_mortem_top_risk_fired", None),
            "skeleton_what_went_right": getattr(post_mortem, "skeleton_what_went_right", []),
            "skeleton_what_went_wrong": getattr(post_mortem, "skeleton_what_went_wrong", []),
            "skeleton_what_id_change": getattr(post_mortem, "skeleton_what_id_change", []),
            "one_sentence_lesson_seed": mech_seed,
            "suggested_tags": mech_tags,
            "rationale": getattr(post_mortem, "rationale", ""),
        },
    }

    prompt: BrainPrompt = build_prompt(
        role=_ROLE,
        principles=_PRINCIPLES,
        schema=_SCHEMA,
        schema_explanation=_SCHEMA_EXPLANATION,
        user_payload=user_payload,
        injection_block_text="",       # no per-brain injection here
        generated_at=now or datetime.now(timezone.utc),
    )

    resp = client.complete_json(
        system=prompt.system, user=prompt.user, cfg=cfg,
    )
    if not resp.ok or resp.data is None:
        return LLMReview.from_failure(
            mech_dq, mech_oq, mech_right, mech_wrong,
            mech_change, mech_seed, mech_tags,
            resp.error or "no data",
        )

    return _parse(resp, mech_dq=mech_dq, mech_oq=mech_oq)


# ----------------------------------------------------------------------
# Internals.
# ----------------------------------------------------------------------
def _safe_to_dict(o: Any) -> Any:
    if o is None:
        return None
    if isinstance(o, (dict, list, str, int, float, bool)):
        return o
    if hasattr(o, "to_dict"):
        try:
            return o.to_dict()
        except Exception:
            pass
    if hasattr(o, "__dict__"):
        return {k: v for k, v in o.__dict__.items() if not k.startswith("_")}
    return str(o)


def _parse(resp: LLMResponse, *, mech_dq: int, mech_oq: int) -> LLMReview:
    d = resp.data or {}
    # Enforce: grader cannot change quantitative grades.
    dq = int(d.get("decision_quality_grade", mech_dq) or mech_dq)
    oq = int(d.get("outcome_quality_grade", mech_oq) or mech_oq)
    if dq != mech_dq:
        dq = mech_dq
    if oq != mech_oq:
        oq = mech_oq
    cal = str(d.get("pre_mortem_calibration", "n/a")).lower()
    if cal not in ("correct", "wrong", "n/a"):
        cal = "n/a"
    return LLMReview(
        ok=True,
        decision_quality_grade=dq,
        outcome_quality_grade=oq,
        what_went_right=str(d.get("what_went_right", "") or "")[:2000],
        what_went_wrong=str(d.get("what_went_wrong", "") or "")[:2000],
        what_id_change=str(d.get("what_id_change", "") or "")[:2000],
        one_sentence_lesson=str(d.get("one_sentence_lesson", "") or "")[:400],
        tags=[str(t)[:80] for t in (d.get("tags") or [])][:12],
        pre_mortem_calibration=cal,
        raw_response=resp,
    )
