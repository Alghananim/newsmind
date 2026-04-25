# -*- coding: utf-8 -*-
"""GateMind LLM wrapper — meta-review of the composed decision.

GateMind's mechanical pipeline already does the hard work: it composes
the three brain grades, runs kill-switches, sizes the trade, and
routes to the broker. The LLM wrapper sits *one layer above* that
mechanical pipeline and asks a single question:

    "Looking at the full picture — three brain grades, the
     SmartNoteBook briefing, the kill-switch verdict, the proposed
     plan — would a senior risk officer let this trade go through?"

This is deliberately conservative. The LLM cannot *grant* a trade the
mechanical gate vetoed; it can only *block* a trade the mechanical
gate would otherwise pass. Asymmetric authority is the right design
here (Carver, *Systematic Trading*: meta-rules above mechanical rules
should add caution, not aggression).

Reasoning canon
---------------
    * Schwager — Market Wizards: refusing trades is the edge.
    * Mark Douglas — *Trading in the Zone*: define criteria *before*
      the bar; do not relax them mid-trade.
    * Lopez de Prado — *AFML* ch.10: meta-labelling. The gate IS the
      meta-label; the LLM provides a second meta-label on top.
    * Annie Duke — *Thinking in Bets*: separate decision quality from
      outcome quality. The wrapper grades the decision, never the
      result (which it has not yet seen).
    * Steenbarger — process discipline. The wrapper's first job is
      "would I take this trade with no PnL today?"

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
class LLMGateReview:
    """LLM second opinion on the composed gate decision.

    `final_action`:
        - "approve_as_is"     : let the mechanical gate proceed
        - "approve_with_size_cut" : approve but cut sizing factor
        - "reject"            : block the trade
    """
    ok: bool
    final_action: str                 # see above
    size_cut_factor: float            # 1.0 = no cut, 0.5 = halve, 0 = none
    rationale: str
    additional_warnings: list[str]
    confidence_in_gate: float         # 0..1 — how much the LLM agrees
    raw_response: Optional[LLMResponse] = None

    @classmethod
    def from_failure(cls, error: str) -> "LLMGateReview":
        # Fall back to "trust the mechanical gate" — no override.
        return cls(
            ok=False, final_action="approve_as_is",
            size_cut_factor=1.0,
            rationale=f"LLM unavailable: {error}; deferring to mechanical gate",
            additional_warnings=[], confidence_in_gate=0.5,
        )


# ----------------------------------------------------------------------
# Prompt pieces.
# ----------------------------------------------------------------------
_ROLE = (
    "You are GateMind's senior risk reviewer. The mechanical gate has "
    "already decided to PASS this trade. Your job is to look at the "
    "full picture — three brain grades, the SmartNoteBook briefing, "
    "the kill-switch verdict, the proposed sizing — and either: "
    "approve as-is, approve with a sizing cut, or reject. You do NOT "
    "have authority to upgrade a trade or override a mechanical veto."
)

_PRINCIPLES = """
1. Asymmetric authority. You can lower size or block, never raise.
2. Refuse-first. Defaulting to approval is what creates a sloppy
   risk culture (Schwager). When in doubt, cut size or reject and
   articulate why.
3. Process before outcome. Grade the *process* of this trade
   (Annie Duke). You are blind to the result; do not pretend
   otherwise.
4. Consider the full ledger of warnings: SmartNoteBook lessons +
   bias flags + the kill-switch verdict + the brain disagreement
   pattern (e.g. one A+, one A, one B is meaningfully different
   from three A+).
5. When the SmartNoteBook briefing shows the system is in a
   degraded state (negative expectancy, deep drawdown, active
   bias flags), default to size_cut. The gate's mechanical
   thresholds are calibrated for healthy conditions.
6. Cite the specific factor that drove your verdict. "Looks fine"
   is not a rationale.
""".strip()

_SCHEMA = {
    "type": "object",
    "required": [
        "final_action", "size_cut_factor",
        "rationale", "additional_warnings",
        "confidence_in_gate",
    ],
    "properties": {
        "final_action": {
            "type": "string",
            "enum": ["approve_as_is", "approve_with_size_cut", "reject"],
        },
        "size_cut_factor": {
            "type": "number", "minimum": 0.1, "maximum": 1.0,
        },
        "rationale": {"type": "string", "maxLength": 1500},
        "additional_warnings": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "confidence_in_gate": {
            "type": "number", "minimum": 0.0, "maximum": 1.0,
        },
    },
}

_SCHEMA_EXPLANATION = """
- `final_action`: your verdict. "approve_as_is" only when nothing in
  the picture suggests caution. "approve_with_size_cut" when the
  trade is sound but conditions argue for a smaller bet (e.g. recent
  drawdown, active bias flag). "reject" when the trade should not
  go through despite the mechanical pass.
- `size_cut_factor`: required if final_action="approve_with_size_cut".
  1.0 means no cut; 0.5 halves the lot; 0.25 quarters it. Never
  below 0.1 — if you would cut more, reject instead.
- `rationale`: 3-6 sentences. Walk through the picture as a senior
  reviewer would. Cite the SmartNoteBook lesson or bias flag by name
  if it influenced you.
- `additional_warnings`: short imperative phrases the orchestrator
  should log alongside this trade ("watch news at 14:30", "monitor
  for spread expansion at NY open").
- `confidence_in_gate`: how much you agree with the mechanical gate's
  pass verdict. 1.0 = "I would have approved this same trade".
""".strip()


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def think(*,
          gate_decision: Any,         # GateMind.GateDecision
          kill_verdict: Any,          # GateMind.KillSwitchVerdict
          brain_grades: list,         # list of BrainGrade
          plan: Any,                  # ChartMind.TradePlan
          sized_trade: Any,           # GateMind.SizedTrade
          briefing_summary: dict,     # SmartNoteBook briefing dict (compact)
          injection_block_text: str = "",
          client: LLMClient,
          cfg: Optional[LLMConfig] = None,
          now: Optional[datetime] = None,
          ) -> LLMGateReview:
    """Run the LLM as a senior risk reviewer over a passed gate decision."""
    user_payload = {
        "gate_decision": _safe_to_dict(gate_decision),
        "kill_switch_verdict": _safe_to_dict(kill_verdict),
        "brain_grades": [_safe_to_dict(g) for g in brain_grades],
        "plan": _safe_to_dict(plan),
        "sized_trade": _safe_to_dict(sized_trade),
        "briefing": briefing_summary,
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
        return LLMGateReview.from_failure(resp.error or "no data")
    return _parse(resp)


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


def _parse(resp: LLMResponse) -> LLMGateReview:
    d = resp.data or {}
    fa = str(d.get("final_action", "approve_as_is"))
    if fa not in ("approve_as_is", "approve_with_size_cut", "reject"):
        fa = "approve_as_is"
    try:
        cut = float(d.get("size_cut_factor", 1.0))
    except (TypeError, ValueError):
        cut = 1.0
    cut = max(0.1, min(1.0, cut))
    if fa == "approve_as_is":
        cut = 1.0       # enforce semantic consistency
    try:
        conf = max(0.0, min(1.0, float(d.get("confidence_in_gate", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    return LLMGateReview(
        ok=True,
        final_action=fa,
        size_cut_factor=cut,
        rationale=str(d.get("rationale", "") or "")[:2000],
        additional_warnings=[
            str(x)[:200] for x in (d.get("additional_warnings") or [])
        ][:5],
        confidence_in_gate=conf,
        raw_response=resp,
    )
