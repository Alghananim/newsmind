# -*- coding: utf-8 -*-
"""Prompt construction helpers shared by every brain wrapper.

Each brain has its own *role description* and *output schema*; this
module gives them a uniform way to:

    1. Build a system prompt by composing
        - the brain's role + reasoning principles
        - the SmartNoteBook injection block (committed lessons + biases
          + warnings + optional pre-mortem)
        - a strict JSON output schema
        - a final reminder to "respond with one JSON object — nothing
          else"

    2. Build a user prompt that bundles the structured input data the
       brain needs to reason over (price action, news context, etc.)
       in a deterministic, schema-stable way.

The result is one `BrainPrompt` ready to feed into LLMClient.

Why a separate module
---------------------
Three reasons:

    * **Consistency.** All five brains attend to the SmartNoteBook
      injection block in the same way; if we change the wording in one
      place every brain benefits. Steenbarger's discipline rule: rules
      that vary by brain become rules in name only.

    * **Auditability.** Every prompt actually sent to the LLM is the
      output of one function; logging that function captures the
      complete decision context. Critical when post-mortems look back
      and ask "why did the system want to take this trade?".

    * **Testability.** Prompt builders are pure — same inputs, same
      output. The brain wrappers then become I/O-only and can be
      tested with mock LLM clients.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


# ----------------------------------------------------------------------
# Container.
# ----------------------------------------------------------------------
@dataclass
class BrainPrompt:
    """A fully-rendered prompt pair ready for LLMClient.

    `system` is the role + principles + injection + schema. `user` is
    the per-call data payload. We keep them separate for two reasons:

        1. The OpenAI chat schema requires distinct system/user roles.
        2. System prompts are stable across the day; logging only
           changing user prompts keeps audit logs from exploding.
    """
    system: str
    user: str
    schema: dict           # the expected JSON output schema (for the
                           # caller's parse-and-validate step)
    metadata: dict = field(default_factory=dict)

    def char_counts(self) -> dict:
        return {
            "system_chars": len(self.system),
            "user_chars": len(self.user),
            "total_chars": len(self.system) + len(self.user),
        }


# ----------------------------------------------------------------------
# Public builder.
# ----------------------------------------------------------------------
def build_prompt(*,
                 role: str,
                 principles: str,
                 schema: dict,
                 schema_explanation: str,
                 user_payload: dict,
                 injection_block_text: str = "",
                 generated_at: Optional[datetime] = None,
                 ) -> BrainPrompt:
    """Compose a `BrainPrompt` from per-brain pieces.

    Parameters
    ----------
    role : str
        One- to two-sentence description of who this brain is.
        Example: "You are ChartMind, the technical-analysis brain of
        a EUR/USD trading system."
    principles : str
        The brain's reasoning principles — the named canon it follows
        (e.g. "Apply ICT/SMC, Wyckoff, Brooks; honour Aronson's
        evidence-based bar."). Should be ≤ 2KB.
    schema : dict
        Pure JSON schema (Python dict) the model is asked to produce.
        Used both in the prompt (rendered as JSON) and returned for the
        caller's parse-and-validate step.
    schema_explanation : str
        One paragraph explaining each field meaning + valid value range
        — schemas alone are not enough to elicit good output.
    user_payload : dict
        The bar / context / market state the brain reasons over. Must
        be JSON-serialisable.
    injection_block_text : str, optional
        Verbatim text from `SmartNoteBook.memory_injector.inject_into_brain`.
        Empty string means no journal context (cold start).
    generated_at : datetime, optional
        Stamp used in the prompt for the brain's "as of" awareness.
    """
    ts = (generated_at or datetime.now(timezone.utc)).isoformat()

    # ---- system prompt ------------------------------------------------
    parts: list[str] = []
    parts.append(role.strip())
    parts.append("")
    parts.append("REASONING PRINCIPLES")
    parts.append("--------------------")
    parts.append(principles.strip())
    parts.append("")

    if injection_block_text.strip():
        parts.append("INSTITUTIONAL MEMORY (SmartNoteBook)")
        parts.append("------------------------------------")
        parts.append(injection_block_text.strip())
        parts.append("")
        parts.append(
            "Treat the lessons above as committed knowledge. Where a "
            "lesson says AVOID a cohort, you must explicitly note in "
            "your rationale why this trade is or is not in that cohort. "
            "Where a behavioral flag is active, the rationale must "
            "explain how this proposal does not repeat the flagged "
            "behavior."
        )
        parts.append("")

    parts.append("OUTPUT SCHEMA")
    parts.append("-------------")
    parts.append(
        "Respond with EXACTLY ONE JSON object that conforms to this "
        "schema. No surrounding text, no markdown fences."
    )
    parts.append("")
    parts.append(json.dumps(schema, indent=2, ensure_ascii=False))
    parts.append("")
    parts.append("FIELD MEANINGS")
    parts.append("--------------")
    parts.append(schema_explanation.strip())
    parts.append("")
    parts.append(
        "If you cannot produce a confident answer, set `confidence` low "
        "and explain why in `rationale`. Never guess. Never invent "
        "numbers. If a field would be a hallucination, set it to a "
        "neutral default and lower the confidence."
    )
    parts.append("")
    parts.append(f"You are reasoning at {ts} (UTC).")

    system = "\n".join(parts)

    # ---- user prompt -------------------------------------------------
    user_lines: list[str] = []
    user_lines.append("INPUT DATA")
    user_lines.append("----------")
    user_lines.append(
        json.dumps(user_payload, indent=2, ensure_ascii=False,
                   default=_json_default)
    )
    user_lines.append("")
    user_lines.append(
        "Now produce your single JSON response per the schema."
    )
    user = "\n".join(user_lines)

    return BrainPrompt(
        system=system, user=user, schema=schema,
        metadata={"generated_at": ts},
    )


# ----------------------------------------------------------------------
# JSON helper.
# ----------------------------------------------------------------------
def _json_default(o: Any) -> Any:
    """Coerce common non-JSON types (datetime, sets, dataclasses) to
    JSON-friendly forms. Reuse this in user_payload pre-processing.
    """
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (set, frozenset)):
        return sorted(list(o))
    if hasattr(o, "to_dict"):
        try:
            return o.to_dict()
        except Exception:
            pass
    if hasattr(o, "__dict__"):
        return {k: v for k, v in o.__dict__.items()
                if not k.startswith("_")}
    return str(o)
