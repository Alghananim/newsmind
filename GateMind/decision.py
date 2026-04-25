# -*- coding: utf-8 -*-
"""Decision aggregation — the gate at the top of the funnel.

Receives one BrainGrade from each of the upstream brains (NewsMind,
ChartMind, MarketMind) and answers a single binary question: should
GateMind ROUTE this to execution, or kill it?

Doctrine
--------
Three independent canon sources converge on the same rule, which we
adopt verbatim:

    * Schwager (Market Wizards interviews) — every champion in the book
      describes the same filter: "I only take trades where I see at
      least three independent reasons to be long, and zero reasons to
      be short." The number three is not arbitrary; it forces the
      filtering of noise without becoming so strict that you stop
      trading.

    * Mark Douglas (Trading in the Zone) — "edge does not come from
      prediction. It comes from refusing trades that don't meet your
      pre-defined criteria." The criteria must be public to yourself
      *before* the bar arrives, otherwise you discover them mid-trade
      and slowly relax them. We make the criteria machine-checkable.

    * Lopez de Prado (Advances in Financial Machine Learning, ch.10) —
      meta-labeling: a primary model proposes direction, a secondary
      model decides whether to take the bet. Our three brains are the
      primary models; the gate is the secondary model. Their grades are
      our meta-features.

Aggregation rules (in order of strictness)
------------------------------------------
1. **Direction unanimity** — all three brains must agree on a single
   direction (`long` or `short`). A `neutral` from any brain kills the
   trade. A disagreement (one long, one short) kills the trade.

2. **Grade floor** — every brain's grade must be at least A. Any single
   B is a hard veto. This is the "any one of them can stop the trade"
   rule, deliberately conservative.

3. **Confluence ceiling** — at most one A is allowed; the other two
   must be A+. Rationale: a wall of A+ across three brains is a rare
   event, and we want the rare event. Two A+ + one A is acceptable;
   one A+ + two A is not. Empirically, the latter setup has a much
   wider performance distribution. (You can relax this via config
   `require_two_aplus=False` if data later argues otherwise.)

4. **Veto flag** — any brain may set its `veto=True` flag regardless of
   grade. Examples: NewsMind detecting a tier-1 event in 10 minutes;
   MarketMind seeing volume drop to a fraction of average. A veto
   short-circuits the decision.

5. **Stale-data guard** — every brain stamps its reading with a
   timestamp; we refuse to act on a brain output older than
   `max_age_seconds` (default 90s for M15 trading).

Each rule produces a structured rejection reason. We *never* return a
silent "no" — the ledger needs to know which gate fired.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional


# --------------------------------------------------------------------
# Grade vocabulary — explicit ordering avoids string compares.
# --------------------------------------------------------------------
_GRADE_RANK = {"A+": 3, "A": 2, "B": 1, "C": 0, "F": -1}


def _rank(grade: str) -> int:
    """Return numeric rank of a grade. Unknown grades sort below F."""
    return _GRADE_RANK.get(grade, -2)


# --------------------------------------------------------------------
# Inputs.
# --------------------------------------------------------------------
@dataclass
class BrainGrade:
    """One brain's verdict on the current bar.

    `name`        — "NewsMind" | "ChartMind" | "MarketMind"
    `direction`   — "long" | "short" | "neutral"
    `grade`       — "A+" | "A" | "B" | "C" | "F"
    `confidence`  — 0..1 (the brain's own probability estimate, calibrated)
    `veto`        — True if this brain explicitly refuses any trade right now
    `veto_reason` — short string when veto is True
    `as_of`       — UTC timestamp the grade was produced
    `notes`       — free-form audit text
    """
    name: str
    direction: str
    grade: str
    confidence: float
    veto: bool = False
    veto_reason: str = ""
    as_of: Optional[datetime] = None
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if isinstance(self.as_of, datetime):
            d["as_of"] = self.as_of.isoformat()
        return d


# --------------------------------------------------------------------
# Output.
# --------------------------------------------------------------------
@dataclass
class GateDecision:
    """The gate's verdict.

    `pass_`       — True if the trade survives the gate
    `direction`   — "long" | "short" | "neutral" (set even on rejects)
    `combined_confidence`
                  — 0..1 aggregate confidence across the three brains
                    (geometric mean of confidences, only meaningful when
                    pass_=True)
    `reasons`     — list[str], every rule that fired (positive or
                    negative). At least one entry on every decision.
    `gates_failed`
                  — list[str] of named gate failures (empty when pass_=True)
    `inputs`      — the three BrainGrades, frozen for the audit trail
    """
    pass_: bool
    direction: str
    combined_confidence: float
    reasons: list[str]
    gates_failed: list[str] = field(default_factory=list)
    inputs: list[BrainGrade] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pass": self.pass_,
            "direction": self.direction,
            "combined_confidence": self.combined_confidence,
            "reasons": list(self.reasons),
            "gates_failed": list(self.gates_failed),
            "inputs": [g.to_dict() for g in self.inputs],
        }


# --------------------------------------------------------------------
# Config — sensible defaults; expose for backtest sweeps.
# --------------------------------------------------------------------
@dataclass
class GateConfig:
    min_grade: str = "A"               # any brain below this kills the trade
    require_two_aplus: bool = True     # 2+ of 3 must be A+; the third can be A
    require_unanimous_direction: bool = True
    max_age_seconds: int = 90          # any older reading => stale, reject
    min_confidence: float = 0.55       # geometric mean must clear this
    allow_neutral_brain: bool = False  # neutral from any brain kills trade
    # Future hooks (left as fields, not used by current logic):
    n_required: int = 3                # number of brains expected


# --------------------------------------------------------------------
# Pure functions — easy to unit-test, no I/O.
# --------------------------------------------------------------------
def _check_freshness(grades: list[BrainGrade], cfg: GateConfig,
                     now_utc: Optional[datetime] = None) -> list[str]:
    """Return list of stale-brain failure reasons (empty if all fresh)."""
    failures: list[str] = []
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    deadline = now_utc - timedelta(seconds=cfg.max_age_seconds)
    for g in grades:
        if g.as_of is None:
            failures.append(f"{g.name}: missing timestamp on grade")
            continue
        # Tolerate naive datetimes by assuming UTC.
        ts = g.as_of if g.as_of.tzinfo else g.as_of.replace(tzinfo=timezone.utc)
        if ts < deadline:
            age = (now_utc - ts).total_seconds()
            failures.append(
                f"{g.name}: stale grade ({age:.0f}s old, max {cfg.max_age_seconds}s)"
            )
    return failures


def _check_vetoes(grades: list[BrainGrade]) -> list[str]:
    out: list[str] = []
    for g in grades:
        if g.veto:
            reason = g.veto_reason or "unspecified"
            out.append(f"{g.name} veto: {reason}")
    return out


def _check_directions(grades: list[BrainGrade], cfg: GateConfig) -> tuple[str, list[str]]:
    """Return (consensus_direction, list_of_failure_reasons).

    consensus_direction is "long"/"short"/"neutral". When it's
    "neutral" the failure list explains why.
    """
    failures: list[str] = []
    dirs = [g.direction for g in grades]

    if cfg.allow_neutral_brain is False and "neutral" in dirs:
        neutral_brains = [g.name for g in grades if g.direction == "neutral"]
        failures.append(
            f"neutral grade from {', '.join(neutral_brains)} (allow_neutral_brain=False)"
        )

    non_neutral = [d for d in dirs if d != "neutral"]
    if not non_neutral:
        failures.append("all brains neutral")
        return "neutral", failures

    if cfg.require_unanimous_direction:
        unique = set(non_neutral)
        if len(unique) > 1:
            split = "/".join(
                f"{g.name}={g.direction}" for g in grades
            )
            failures.append(f"directions disagree: {split}")
            return "neutral", failures

    # Unanimous (or relaxed): use the majority direction.
    longs = sum(1 for d in non_neutral if d == "long")
    shorts = sum(1 for d in non_neutral if d == "short")
    if longs > shorts:
        return "long", failures
    if shorts > longs:
        return "short", failures
    failures.append("direction tie")
    return "neutral", failures


def _check_grades(grades: list[BrainGrade], cfg: GateConfig) -> list[str]:
    failures: list[str] = []
    floor_rank = _rank(cfg.min_grade)
    for g in grades:
        if _rank(g.grade) < floor_rank:
            failures.append(
                f"{g.name} grade {g.grade} below floor {cfg.min_grade}"
            )

    if cfg.require_two_aplus:
        n_aplus = sum(1 for g in grades if g.grade == "A+")
        n_a_or_better = sum(1 for g in grades if _rank(g.grade) >= _rank("A"))
        # We need: at least 2 A+, and the third must still be >= A.
        if n_aplus < 2 or n_a_or_better < len(grades):
            mix = "/".join(g.grade for g in grades)
            failures.append(
                f"grade mix {mix} fails 2-of-3-A+ rule"
            )
    return failures


def _combined_confidence(grades: list[BrainGrade]) -> float:
    """Geometric mean of brain confidences.

    Geometric mean penalises low-confidence outliers harder than
    arithmetic mean — a single 0.40 brain drags the aggregate below
    threshold. This matches Lopez de Prado's bet-sizing intuition that
    *any* model uncertainty should compound multiplicatively.
    """
    if not grades:
        return 0.0
    product = 1.0
    for g in grades:
        c = max(0.0, min(1.0, float(g.confidence)))
        # Floor at 1e-6 so a single zero doesn't nuke the geometric mean
        # (which would lose information about the others).
        product *= max(c, 1e-6)
    return product ** (1.0 / len(grades))


# --------------------------------------------------------------------
# The public API.
# --------------------------------------------------------------------
def evaluate(
    grades: list[BrainGrade],
    cfg: Optional[GateConfig] = None,
    now_utc: Optional[datetime] = None,
) -> GateDecision:
    """Run every gate, in order, and produce a GateDecision.

    The function never short-circuits — we collect all failures so the
    audit trail shows everything that was wrong, not just the first
    problem encountered. This costs a few extra checks per call but
    pays off massively at debug time.
    """
    if cfg is None:
        cfg = GateConfig()
    reasons: list[str] = []
    gates_failed: list[str] = []

    # Sanity: number of brains.
    if len(grades) != cfg.n_required:
        gates_failed.append(
            f"received {len(grades)} grades; expected {cfg.n_required}"
        )
        reasons.append(gates_failed[-1])

    # 1. Freshness.
    stale = _check_freshness(grades, cfg, now_utc)
    if stale:
        gates_failed.extend(stale)
        reasons.extend(stale)

    # 2. Vetoes.
    vetoes = _check_vetoes(grades)
    if vetoes:
        gates_failed.extend(vetoes)
        reasons.extend(vetoes)

    # 3. Direction.
    direction, dir_fail = _check_directions(grades, cfg)
    if dir_fail:
        gates_failed.extend(dir_fail)
        reasons.extend(dir_fail)

    # 4. Grades.
    grade_fail = _check_grades(grades, cfg)
    if grade_fail:
        gates_failed.extend(grade_fail)
        reasons.extend(grade_fail)

    # 5. Combined confidence.
    combined = _combined_confidence(grades)
    if combined < cfg.min_confidence:
        msg = (
            f"combined confidence {combined:.2f} below "
            f"min {cfg.min_confidence:.2f}"
        )
        gates_failed.append(msg)
        reasons.append(msg)

    pass_ = len(gates_failed) == 0
    if pass_:
        # Add the positive reason trail.
        reasons.append(
            f"all gates passed: direction={direction}, "
            f"grades={'/'.join(g.grade for g in grades)}, "
            f"confidence={combined:.2f}"
        )

    return GateDecision(
        pass_=pass_,
        direction=direction if pass_ else "neutral",
        combined_confidence=combined,
        reasons=reasons,
        gates_failed=gates_failed,
        inputs=list(grades),
    )
