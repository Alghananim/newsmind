# -*- coding: utf-8 -*-
"""Patterns — discover statistically-honest regularities in the journal.

The temptation when looking at trade history is to spot "patterns"
visually and trade them. This is exactly what Aronson (*Evidence-Based
Technical Analysis*, 2007) calls the data-mining bias: with enough
features and enough trades, *some* combination will look profitable
purely by chance. Lopez de Prado (*AFML*, ch.11) makes the same point
with the more brutal phrase "false discoveries are the rule, not the
exception".

Our defenses, in order of importance:

    1. **Minimum sample size**. We refuse to surface any pattern with
       fewer than `min_n` matching trades (default 10). Below that the
       confidence interval is wider than the effect itself.

    2. **Bonferroni correction**. When testing K hypotheses in
       parallel, we require p < alpha / K rather than p < alpha. This
       is conservative — it accepts more misses to avoid false alarms.
       For a notebook that *recommends action to a live system* this is
       the right trade-off (Lopez de Prado).

    3. **Effect size, not just p-value**. We additionally require that
       the difference in win-rate or expectancy between the cohort and
       its complement is at least `min_effect` (default 10 percentage
       points for win-rate, 0.2R for expectancy). A statistically
       significant 2pp edge in a journal of 5000 trades is real but not
       worth changing system behavior over.

    4. **Out-of-sample holdout**. Optional but recommended: callers can
       pass `holdout_fraction` to split chronologically and only
       surface patterns whose effect is preserved on the holdout. This
       is the cheapest defense against curve-fitting.

The output, `DiscoveredPattern`, is consumed by `lessons.py` which
adds the human narrative ("avoid setup X during high-spread sessions")
and `memory_injector.py` which feeds those lessons to the brains.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .journal import TradeRecord


# ----------------------------------------------------------------------
# Output type.
# ----------------------------------------------------------------------
@dataclass
class DiscoveredPattern:
    """One regularity surfaced over the journal.

    Fields are deliberately verbose — the lesson layer renders them
    into a single sentence, but the underlying numbers are kept so
    callers (or future evaluators) can audit the claim.
    """
    feature: str                    # e.g. "setup_type=breakout_pullback"
    direction: str                  # "favourable" | "adverse"
    cohort_n: int
    cohort_win_rate: float
    cohort_expectancy_r: float
    rest_n: int
    rest_win_rate: float
    rest_expectancy_r: float
    win_rate_delta: float           # cohort - rest, signed
    expectancy_delta_r: float       # cohort - rest, signed
    p_value: float                  # Fisher's exact (one-sided)
    bonferroni_threshold: float     # alpha / K
    passes_bonferroni: bool
    holdout_preserved: Optional[bool] = None    # None if no holdout requested
    notes: str = ""

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ----------------------------------------------------------------------
# Public entry point.
# ----------------------------------------------------------------------
def mine_patterns(trades: Iterable[TradeRecord],
                  alpha: float = 0.05,
                  min_n: int = 10,
                  min_winrate_delta: float = 0.10,
                  min_expectancy_delta_r: float = 0.20,
                  holdout_fraction: float = 0.0) -> list[DiscoveredPattern]:
    """Mine the journal for honest, statistically-significant cohorts.

    Hypotheses tested (one binary feature each):

        * setup_type == X for each setup with >= min_n occurrences
        * market_regime == X
        * news_state == X
        * spread_percentile_rank in [.66, 1.0]   ("wide_spread")
        * spread_percentile_rank in [0, .33]     ("tight_spread")
        * filled_price - requested_price > 1 pip ("high_slippage")
        * hour-of-day in 4h FX-session buckets

    For each, we compare the cohort (feature true) to the complement
    (feature false). We use Fisher's exact test on win/loss counts
    (one-sided in the direction of the observed effect) and a
    Bonferroni-corrected alpha.

    `holdout_fraction` (0.0 to disable, e.g. 0.3 to hold out the most-
    recent 30% chronologically): if positive, patterns are mined on the
    *training* portion and re-tested on the holdout. Patterns whose
    direction flips on holdout are demoted to `passes_bonferroni=False`
    (kept in the result with `holdout_preserved=False` so the lesson
    layer can flag them as unstable).
    """
    trades = list(trades)
    n = len(trades)
    if n < 2 * min_n:
        return []

    # ------ optional holdout split (chronological) ------------------
    if holdout_fraction and 0.0 < holdout_fraction < 1.0:
        trades_sorted = sorted(trades, key=lambda r: r.opened_at)
        split = int(n * (1.0 - holdout_fraction))
        train, hold = trades_sorted[:split], trades_sorted[split:]
    else:
        train, hold = trades, []

    # ------ enumerate hypotheses ------------------------------------
    hypotheses: list[tuple[str, Callable[[TradeRecord], bool]]] = []
    hypotheses.extend(_hypotheses_categorical(train, "setup_type", min_n))
    hypotheses.extend(_hypotheses_categorical(train, "market_regime", min_n))
    hypotheses.extend(_hypotheses_categorical(train, "news_state", min_n))
    hypotheses.append(("spread=wide(>=p66)",
                       lambda t: t.spread_percentile_rank >= 0.66))
    hypotheses.append(("spread=tight(<=p33)",
                       lambda t: t.spread_percentile_rank <= 0.33))
    hypotheses.append(("execution=high_slippage(>1.0pip)",
                       lambda t: abs(t.slippage_pips) > 1.0))
    hypotheses.append(("decision_quality_low(<=2)",
                       lambda t: t.decision_quality_grade in (1, 2)))
    hypotheses.append(("decision_quality_high(>=4)",
                       lambda t: t.decision_quality_grade in (4, 5)))
    for lo, hi in [(0,4),(4,8),(8,12),(12,16),(16,20),(20,24)]:
        rng = (lo, hi)
        hypotheses.append((
            f"hour_utc=[{lo:02d}-{hi:02d})",
            lambda t, r=rng: r[0] <= t.opened_at.hour < r[1],
        ))

    K = len(hypotheses)
    if K == 0:
        return []
    bonferroni_alpha = alpha / K

    # ------ test each hypothesis ------------------------------------
    out: list[DiscoveredPattern] = []
    for name, predicate in hypotheses:
        cohort = [t for t in train if predicate(t)]
        rest = [t for t in train if not predicate(t)]
        if len(cohort) < min_n or len(rest) < min_n:
            continue

        c_w, c_l = _wins_losses(cohort)
        r_w, r_l = _wins_losses(rest)
        if c_w + c_l == 0 or r_w + r_l == 0:
            continue

        c_wr = c_w / (c_w + c_l)
        r_wr = r_w / (r_w + r_l)
        c_xp = _expectancy_r(cohort)
        r_xp = _expectancy_r(rest)

        wr_delta = c_wr - r_wr
        xp_delta = c_xp - r_xp

        # Direction of the observed effect — Fisher's one-sided test
        # is taken in that direction.
        direction = "favourable" if (xp_delta >= 0 and wr_delta >= 0) else "adverse"
        if (xp_delta == 0) and (wr_delta == 0):
            continue

        # Effect-size threshold.
        if abs(wr_delta) < min_winrate_delta and abs(xp_delta) < min_expectancy_delta_r:
            continue

        # Fisher's exact, one-sided in the observed direction.
        p = _fisher_exact_one_sided(c_w, c_l, r_w, r_l,
                                    direction="greater" if direction == "favourable"
                                              else "less")
        passes = p < bonferroni_alpha

        holdout_preserved: Optional[bool] = None
        if hold:
            h_cohort = [t for t in hold if predicate(t)]
            h_rest = [t for t in hold if not predicate(t)]
            if len(h_cohort) >= max(3, min_n // 3) and len(h_rest) >= max(3, min_n // 3):
                h_xp_delta = _expectancy_r(h_cohort) - _expectancy_r(h_rest)
                holdout_preserved = (
                    (xp_delta >= 0 and h_xp_delta >= 0) or
                    (xp_delta < 0 and h_xp_delta < 0)
                )
                if not holdout_preserved:
                    passes = False

        out.append(DiscoveredPattern(
            feature=name,
            direction=direction,
            cohort_n=len(cohort),
            cohort_win_rate=c_wr,
            cohort_expectancy_r=c_xp,
            rest_n=len(rest),
            rest_win_rate=r_wr,
            rest_expectancy_r=r_xp,
            win_rate_delta=wr_delta,
            expectancy_delta_r=xp_delta,
            p_value=p,
            bonferroni_threshold=bonferroni_alpha,
            passes_bonferroni=passes,
            holdout_preserved=holdout_preserved,
        ))

    # Sort surviving patterns by absolute expectancy delta, strongest first.
    out.sort(key=lambda p: -abs(p.expectancy_delta_r))
    return out


# ----------------------------------------------------------------------
# Hypothesis enumeration helpers.
# ----------------------------------------------------------------------
def _hypotheses_categorical(trades: list[TradeRecord], field: str, min_n: int):
    counts: dict[Any, int] = defaultdict(int)
    for t in trades:
        counts[getattr(t, field, None)] += 1
    out = []
    for value, c in counts.items():
        if c < min_n or value in (None, "", "unknown"):
            continue
        out.append((f"{field}={value}",
                    lambda t, f=field, v=value: getattr(t, f, None) == v))
    return out


def _wins_losses(trades: list[TradeRecord]) -> tuple[int, int]:
    w = sum(1 for t in trades if t.outcome.r_multiple > 0.05)
    l = sum(1 for t in trades if t.outcome.r_multiple < -0.05)
    return w, l


def _expectancy_r(trades: list[TradeRecord]) -> float:
    if not trades:
        return 0.0
    return statistics.fmean(t.outcome.r_multiple for t in trades)


# ----------------------------------------------------------------------
# Fisher's exact (one-sided) — pure-Python implementation.
# ----------------------------------------------------------------------
def _fisher_exact_one_sided(a: int, b: int, c: int, d: int,
                            direction: str = "greater") -> float:
    """Compute the one-sided Fisher's exact p-value for the 2x2 table

            wins  losses
    cohort   a      b
    rest     c      d

    `direction='greater'` tests P(cohort win-rate >= observed | H0).
    `direction='less'`    tests P(cohort win-rate <= observed | H0).

    We sum the hypergeometric tail probabilities by hand. Avoids a
    SciPy dependency and is fast enough at journal scales (typically
    K ~ 20 hypotheses, N ~ thousands).
    """
    n = a + b + c + d
    row1 = a + b
    col1 = a + c
    if row1 == 0 or col1 == 0 or row1 == n or col1 == n:
        return 1.0
    a_min = max(0, row1 + col1 - n)
    a_max = min(row1, col1)

    log_total = (_log_factorial(row1) + _log_factorial(n - row1) +
                 _log_factorial(col1) + _log_factorial(n - col1) -
                 _log_factorial(n))

    def log_prob(a_val: int) -> float:
        b_val = row1 - a_val
        c_val = col1 - a_val
        d_val = n - row1 - c_val
        return (log_total -
                _log_factorial(a_val) - _log_factorial(b_val) -
                _log_factorial(c_val) - _log_factorial(d_val))

    if direction == "greater":
        ks = range(a, a_max + 1)
    else:
        ks = range(a_min, a + 1)
    # Sum exp(log_prob(k)) numerically stable using log-sum-exp:
    log_probs = [log_prob(k) for k in ks]
    if not log_probs:
        return 1.0
    m = max(log_probs)
    p = math.exp(m) * sum(math.exp(lp - m) for lp in log_probs)
    return min(1.0, max(0.0, p))


_LOG_FAC_CACHE: list[float] = [0.0, 0.0]   # log(0!) = log(1!) = 0


def _log_factorial(n: int) -> float:
    """Cached log(n!) — grows the cache as needed."""
    if n < 0:
        raise ValueError("n must be non-negative")
    while len(_LOG_FAC_CACHE) <= n:
        _LOG_FAC_CACHE.append(_LOG_FAC_CACHE[-1] + math.log(len(_LOG_FAC_CACHE)))
    return _LOG_FAC_CACHE[n]
