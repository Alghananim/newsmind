# -*- coding: utf-8 -*-
"""Post-mortem — Steenbarger's structured after-action review.

In *The Daily Trading Coach* (lessons 1-10, "Self-coaching with the
journal") Brett Steenbarger argues the morning briefing is half the
journal's value; the other half is the closing review. The structure he
recommends, distilled:

    1. **Did it go as planned?** — answered against the *recorded* plan,
       not against memory. We preserve the plan in the journal exactly
       to make this question objective.

    2. **Decision quality** (1-5). Was the *process* sound, regardless
       of result? Annie Duke (*Thinking in Bets*) is the discipline:
       outcomes can be lucky or unlucky; processes are the variable
       you control.

    3. **Outcome quality** (1-5). Was the *result* good? Separated from
       process so we can tell luck from skill over many trades. The
       metrics module then correlates the two: a healthy system shows
       a positive but imperfect correlation.

    4. **What went right** — surface the working pieces so we don't
       fix what isn't broken.

    5. **What went wrong** — the kind of forensic the system can later
       cluster (patterns.py) and rule on (lessons.py).

    6. **What I'd do differently** — Steenbarger calls this "the
       single highest-leverage line in the journal". Prescriptive,
       short, action-bearing.

    7. **One-sentence lesson** — forced brevity. If you can't say it
       in one sentence, you don't yet know it.

    8. **Pre-mortem calibration** — compare the pre-mortem's predicted
       outcome and top failure mode against what actually happened.
       Over time this calibrates the system's self-awareness: are our
       imagined failures the ones that fire?

This module produces a structured `PostMortemReport`. The actual
narrative text is normally written by a downstream LLM grading layer
(ChatGPT) that has access to the report and the trade record. Here we
generate the *skeleton* and the *quantitative grades* that the LLM
should respect rather than override.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .journal import TradeRecord


# ----------------------------------------------------------------------
# Output type.
# ----------------------------------------------------------------------
@dataclass
class PostMortemReport:
    trade_id: str
    pair: str
    closed_at: datetime
    decision_quality_grade: int          # 1-5, computed
    outcome_quality_grade: int           # 1-5, computed
    went_as_planned: bool
    delta_from_plan_pips: float
    pre_mortem_was_correct: Optional[bool]   # None if no pre-mortem
    pre_mortem_top_risk_fired: Optional[bool]
    skeleton_what_went_right: list[str]
    skeleton_what_went_wrong: list[str]
    skeleton_what_id_change: list[str]
    one_sentence_lesson_seed: str
    suggested_tags: list[str]
    rationale: str

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["closed_at"] = self.closed_at.isoformat()
        return d


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def run_post_mortem(record: TradeRecord) -> PostMortemReport:
    """Generate the structured after-action skeleton for a closed trade.

    Returns a PostMortemReport but does **not** write it back to the
    journal — that is the orchestrator's job (SmartNoteBook.review()),
    which will normally also pass the report through the LLM grader for
    the final narrative fields before calling Journal.attach_review().
    """
    o = record.outcome

    # ---- 1. Did it go as planned? -----------------------------------
    if record.direction == "long":
        delta_to_target = (o.exit_price - record.target_price) / 0.0001
        delta_to_stop = (o.exit_price - record.stop_price) / 0.0001
    else:
        delta_to_target = (record.target_price - o.exit_price) / 0.0001
        delta_to_stop = (record.stop_price - o.exit_price) / 0.0001

    went_as_planned = (
        o.exit_reason == "target"
        or (o.exit_reason == "stop" and abs(delta_to_stop) < 1.5)
    )
    delta_from_plan = (
        delta_to_target if o.exit_reason in ("target", "partial")
        else delta_to_stop
    )

    # ---- 2. Decision quality (process) ------------------------------
    dq = _decision_quality_grade(record)

    # ---- 3. Outcome quality (result) --------------------------------
    oq = _outcome_quality_grade(record)

    # ---- 4. Pre-mortem calibration ----------------------------------
    pre_correct, pre_top_fired = _pre_mortem_calibration(record)

    # ---- 5. Skeleton bullets ----------------------------------------
    right = _what_went_right(record)
    wrong = _what_went_wrong(record)
    id_change = _what_id_change(record)
    seed = _one_sentence_seed(record, dq, oq)
    tags = _suggested_tags(record, dq, oq)

    rationale = (
        f"DQ={dq} (process), OQ={oq} (result). "
        f"R={o.r_multiple:+.2f}, exit_reason={o.exit_reason}, "
        f"bars_held={o.bars_held}. "
        f"plan_followed={'yes' if went_as_planned else 'no'} "
        f"(delta_from_plan={delta_from_plan:+.1f}pips)."
    )

    return PostMortemReport(
        trade_id=record.trade_id,
        pair=record.pair,
        closed_at=o.closed_at,
        decision_quality_grade=dq,
        outcome_quality_grade=oq,
        went_as_planned=went_as_planned,
        delta_from_plan_pips=delta_from_plan,
        pre_mortem_was_correct=pre_correct,
        pre_mortem_top_risk_fired=pre_top_fired,
        skeleton_what_went_right=right,
        skeleton_what_went_wrong=wrong,
        skeleton_what_id_change=id_change,
        one_sentence_lesson_seed=seed,
        suggested_tags=tags,
        rationale=rationale,
    )


# ----------------------------------------------------------------------
# Decision quality (1-5).
# ----------------------------------------------------------------------
def _decision_quality_grade(r: TradeRecord) -> int:
    """Grade the *process* on a 1-5 scale.

    The grader weighs only inputs that were available *at decision
    time*. Outcome must NOT be allowed to leak into this grade — that
    is the resulting fallacy.

    Components (each 0 or 1):
        + plan was actionable (rr_planned >= 1.0)
        + at least 2 brains were >= A
        + gate combined confidence >= 0.55
        + entry slippage <= 1 pip (suggests patient execution)
        + spread percentile <= 0.66 (entered in liquid conditions)

    Map sum -> grade: 0->1, 1->2, 2->3, 3->4, 4-5 -> 5.
    """
    pts = 0
    if r.rr_planned >= 1.0:
        pts += 1
    grades_above_A = sum(
        1 for g in r.brain_grades if g.grade in ("A", "A+")
    )
    if grades_above_A >= 2:
        pts += 1
    if r.gate_combined_confidence >= 0.55:
        pts += 1
    if abs(r.slippage_pips) <= 1.0:
        pts += 1
    if r.spread_percentile_rank <= 0.66:
        pts += 1
    return min(5, max(1, pts + 1))


# ----------------------------------------------------------------------
# Outcome quality (1-5).
# ----------------------------------------------------------------------
def _outcome_quality_grade(r: TradeRecord) -> int:
    """Grade the *result* on a 1-5 scale, R-multiple driven.

        R <= -1.5  -> 1   (worse than planned loss)
        -1.5 < R <= -0.5 -> 2
        -0.5 < R <= 0.3  -> 3
        0.3 < R <= 1.0   -> 4
        R > 1.0          -> 5

    Note: a trade hitting target gets 5 even if equally well-executed
    as a stopped-out trade. That asymmetry is intentional — outcome
    quality is *result-focused* by definition.
    """
    rm = r.outcome.r_multiple
    if rm <= -1.5:
        return 1
    if rm <= -0.5:
        return 2
    if rm <= 0.3:
        return 3
    if rm <= 1.0:
        return 4
    return 5


# ----------------------------------------------------------------------
# Pre-mortem calibration.
# ----------------------------------------------------------------------
def _pre_mortem_calibration(r: TradeRecord) -> tuple[Optional[bool], Optional[bool]]:
    """Compare the pre-mortem prediction to actual outcome.

    Returns (predicted_correct, top_risk_fired).
    Both None if no pre-mortem was recorded.
    """
    pred = r.pre_mortem_predicted_outcome
    top = r.pre_mortem_top_risk
    if not pred:
        return (None, None)
    rm = r.outcome.r_multiple
    actual = "win" if rm > 0.3 else "loss" if rm < -0.3 else "scratch"
    pred_correct = pred == actual
    # We cannot programmatically tell if the *exact* top risk fired;
    # heuristics: if top mentioned 'news' and exit_reason mentions
    # news/blackout, we credit it. The LLM grader can refine.
    fired = None
    if top:
        t = top.lower()
        er = (r.outcome.exit_reason or "").lower()
        if "news" in t and ("news" in er or "blackout" in er):
            fired = True
        elif "spread" in t and abs(r.slippage_pips) > 1.0:
            fired = True
        elif "revenge" in t and r.decision_quality_grade <= 2:
            fired = True
        elif "invalidation" in t and "invalidat" in er:
            fired = True
        elif "time" in t and er == "time_decay":
            fired = True
        else:
            fired = False
    return (pred_correct, fired)


# ----------------------------------------------------------------------
# Skeleton bullets.
# ----------------------------------------------------------------------
def _what_went_right(r: TradeRecord) -> list[str]:
    out: list[str] = []
    if r.outcome.exit_reason == "target":
        out.append("plan respected — target hit at the planned level")
    if abs(r.slippage_pips) <= 0.5:
        out.append(f"clean execution, slippage {r.slippage_pips:+.1f} pip")
    if r.gate_combined_confidence >= 0.65 and r.outcome.r_multiple > 0:
        out.append("high gate confidence translated into a profitable result")
    grades_A_plus = sum(1 for g in r.brain_grades if g.grade == "A+")
    if grades_A_plus >= 2 and r.outcome.r_multiple > 0:
        out.append(f"{grades_A_plus} brains graded A+ pre-trade and the trade paid")
    if r.outcome.exit_reason == "stop" and r.decision_quality_grade >= 4:
        out.append("losing trade with sound process — kept loss to planned 1R")
    if not out:
        out.append("no specific positive flagged programmatically; review qualitatively")
    return out


def _what_went_wrong(r: TradeRecord) -> list[str]:
    out: list[str] = []
    if abs(r.slippage_pips) > 1.5:
        out.append(
            f"entry slippage {r.slippage_pips:+.1f} pip materially eroded R"
        )
    if r.outcome.exit_reason in ("setup_invalidated", "time_decay"):
        out.append(
            f"exit reason '{r.outcome.exit_reason}' means the setup didn't develop "
            "as expected — was the read correct or just lucky in the past?"
        )
    if r.outcome.r_multiple < -1.05:
        out.append(
            f"loss exceeded planned 1R ({r.outcome.r_multiple:+.2f}R) — slippage "
            "or stop placement allowed a worse-than-budgeted outcome"
        )
    if r.outcome.max_adverse_excursion_pips > 0 and r.outcome.r_multiple > 0:
        # MAE on a winning trade tells us how close we came to a stop
        out.append(
            f"trade went {r.outcome.max_adverse_excursion_pips:.1f} pips against "
            "us before recovering — entry timing could be tighter"
        )
    if r.spread_percentile_rank > 0.85 and r.outcome.r_multiple < 0:
        out.append(
            "entered while spread was in top 15% — execution conditions were poor"
        )
    if r.decision_quality_grade <= 2:
        out.append(
            "decision-quality grade is low; review the inputs that fell short"
        )
    if not out:
        out.append("no specific negative flagged programmatically")
    return out


def _what_id_change(r: TradeRecord) -> list[str]:
    """Prescriptive — Steenbarger emphasises this as the highest-leverage
    section. Each item is one short, action-bearing sentence.
    """
    out: list[str] = []
    if abs(r.slippage_pips) > 1.5:
        out.append("use limit orders rather than market when planned entry "
                   "is within 1 pip of price")
    if r.outcome.exit_reason == "time_decay":
        out.append("set a tighter time budget for this setup type, or skip "
                   "when conditions don't materialise within N bars")
    if r.outcome.r_multiple < -1.05:
        out.append("verify stop placement accounts for typical noise on "
                   "this pair / regime; consider widening risk_amount buffer")
    if r.spread_percentile_rank > 0.85:
        out.append("add a hard veto on entries when spread > p85 for this pair")
    if r.outcome.max_adverse_excursion_pips > 0 and r.outcome.r_multiple > 0:
        out.append("look for pullback entries closer to the structural level "
                   "rather than chasing the breakout")
    if not out:
        out.append("no programmatic prescription — let the LLM grader "
                   "propose qualitative adjustments")
    return out


def _one_sentence_seed(r: TradeRecord, dq: int, oq: int) -> str:
    """A short seed the LLM grader can refine. The seed is deliberately
    blunt so the grader is forced to either confirm or reframe.
    """
    rm = r.outcome.r_multiple
    if dq >= 4 and oq >= 4:
        return (f"good process delivered a good result on {r.setup_type}: "
                f"keep doing this exactly.")
    if dq >= 4 and oq <= 2:
        return (f"sound process, unlucky outcome on {r.setup_type}: do "
                f"not punish the system, do not change the rule.")
    if dq <= 2 and oq >= 4:
        return (f"weak process, lucky outcome on {r.setup_type}: this is "
                f"the most dangerous quadrant — fix the process anyway.")
    if dq <= 2 and oq <= 2:
        return (f"weak process and weak outcome on {r.setup_type}: "
                f"trade should not have been taken; identify the gate "
                f"that should have vetoed it.")
    return (f"middling process, {rm:+.2f}R outcome on {r.setup_type}: "
            f"single trade is signal-poor — review across the cohort.")


def _suggested_tags(r: TradeRecord, dq: int, oq: int) -> list[str]:
    tags = [f"setup:{r.setup_type}", f"regime:{r.market_regime}",
            f"news:{r.news_state}", f"direction:{r.direction}"]
    if dq <= 2:
        tags.append("process:weak")
    elif dq >= 4:
        tags.append("process:strong")
    if oq <= 2:
        tags.append("outcome:loss")
    elif oq >= 4:
        tags.append("outcome:win")
    if dq <= 2 and oq >= 4:
        tags.append("warning:lucky_bad_process")
    if dq >= 4 and oq <= 2:
        tags.append("warning:unlucky_good_process")
    if r.outcome.r_multiple < -1.05:
        tags.append("breach:over_1R_loss")
    if abs(r.slippage_pips) > 1.5:
        tags.append("execution:high_slippage")
    return tags
