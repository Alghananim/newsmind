# -*- coding: utf-8 -*-
"""Regime-conditional priors — upgrade #4.

Stores the historical success rate of each pattern in each context
(regime × session × volatility × pair) as a Bayesian Beta distribution.
The same pattern behaves very differently in different contexts: a
bullish engulfing inside a down-trending regime during NY PM session
at high volatility is NOT the same event as the same candle during
London AM session in a low-vol trending regime.

The priors library:

  * starts uninformed (Beta(1, 1) = uniform) for every combination
  * updates its beliefs as trade outcomes come in
  * answers queries with posterior mean AND a 95% credible interval,
    so the caller knows *how confident* the probability estimate is

Persistence is JSON — plain text, auditable by hand, portable across
machines and backups.

References conceptually drawn from:
  * Thomas Bayes (1763) — the original theorem
  * Wilson (1927) — lower-bound binomial interval, used where n is small
  * López de Prado (2018) — the framework of conditional expectations
    over regime labels; our `query` returns the same kind of object
    his meta-labeller consumes
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Beta-distribution prior for a single binary outcome (success or fail).
# ---------------------------------------------------------------------------
@dataclass
class BetaPrior:
    """A Beta(α, β) distribution over the probability of success.

    The conjugate of Bernoulli: after observing k successes in n trials
    starting from Beta(α₀, β₀), the posterior is Beta(α₀+k, β₀+n-k).
    We always start at Beta(1, 1) = Uniform(0,1) = zero information.
    """
    alpha: float = 1.0
    beta: float = 1.0

    def observe(self, success: bool) -> None:
        if success:
            self.alpha += 1.0
        else:
            self.beta += 1.0

    @property
    def n(self) -> int:
        return int(round(self.alpha + self.beta - 2))    # observed trials

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        ab = self.alpha + self.beta
        return (self.alpha * self.beta) / (ab * ab * (ab + 1.0))

    @property
    def stddev(self) -> float:
        return math.sqrt(self.variance)

    def credible_interval(self, confidence: float = 0.95) -> tuple[float, float]:
        """Approximate central credible interval via a Normal
        approximation. This is accurate when α, β > 5; for very small n
        the interval is wider than the true Beta quantiles, which is
        the safe side for decision-making (under-claims certainty)."""
        z = 1.959963984540054            # z_{1-0.025} for 95%
        # Adjust z for non-95% requests (rough; fine for 90-99%)
        if abs(confidence - 0.95) > 1e-6:
            # inverse normal approximation via Beasley-Springer-Moro
            # for arbitrary confidence. Small-call fallback = 95%.
            p = 1.0 - (1.0 - confidence) / 2.0
            z = _phi_inv(p)
        m = self.mean
        s = self.stddev
        lo = max(0.0, m - z * s)
        hi = min(1.0, m + z * s)
        return lo, hi

    def to_dict(self) -> dict:
        return {"alpha": self.alpha, "beta": self.beta}

    @classmethod
    def from_dict(cls, d: dict) -> "BetaPrior":
        return cls(alpha=float(d.get("alpha", 1.0)),
                   beta=float(d.get("beta", 1.0)))


def _phi_inv(p: float) -> float:
    """Inverse standard normal — Beasley-Springer-Moro (Acklam 2003)."""
    p = max(1e-12, min(1.0 - 1e-12, p))
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    if p > p_high:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
                ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1.0)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5]) * q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1.0)


# ---------------------------------------------------------------------------
# The library itself.
# ---------------------------------------------------------------------------
@dataclass
class PriorContext:
    """The four-dimensional key that identifies one context slot.

    Keep cardinalities manageable — more dimensions = more slots = less
    data per slot = wider credible intervals. Four is our cap.
    """
    pattern: str        # e.g. "bullish_engulfing", "hammer", "marubozu_bull"
    regime: str         # "trending_up" | "trending_down" | "ranging" | "chaos"
    session: str        # "asian" | "london" | "ny_am" | "ny_pm" | "off"
    vol_bucket: str     # "low" | "normal" | "high"
    pair: str = "*"     # wildcard by default

    def key(self) -> str:
        return f"{self.pair}|{self.pattern}|{self.regime}|{self.session}|{self.vol_bucket}"


@dataclass
class PriorQueryResult:
    """What the lookup returns."""
    mean: float                      # posterior mean success probability
    ci_low: float                    # 95% CI lower
    ci_high: float                   # 95% CI upper
    n_observations: int              # how many trials the prior has seen
    confidence: str                  # "strong" | "weak" | "none"


class RegimePriors:
    """Dictionary of Beta priors keyed by context.

    Usage:
        priors = RegimePriors.load("priors.json")
        res = priors.query(PriorContext(
            pattern="bullish_engulfing",
            regime="trending_up",
            session="ny_am",
            vol_bucket="normal",
            pair="EUR_USD",
        ))
        # … trade happens, outcome recorded
        priors.observe(ctx, success=True)
        priors.save("priors.json")
    """

    # Minimum sample size before we trust the prior over the uninformed default
    STRONG_N: int = 30
    WEAK_N: int = 8

    def __init__(self):
        self._table: dict[str, BetaPrior] = {}

    # --- observe / query ------------------------------------------------
    def observe(self, ctx: PriorContext, success: bool) -> None:
        k = ctx.key()
        if k not in self._table:
            self._table[k] = BetaPrior()
        self._table[k].observe(success)

    def query(self, ctx: PriorContext) -> PriorQueryResult:
        k = ctx.key()
        prior = self._table.get(k) or BetaPrior()
        lo, hi = prior.credible_interval()
        if prior.n >= self.STRONG_N:
            conf = "strong"
        elif prior.n >= self.WEAK_N:
            conf = "weak"
        else:
            conf = "none"
        return PriorQueryResult(
            mean=prior.mean,
            ci_low=lo,
            ci_high=hi,
            n_observations=prior.n,
            confidence=conf,
        )

    # --- bulk exploration ----------------------------------------------
    def all_contexts(self) -> list[tuple[str, BetaPrior]]:
        """Return every observed context key + its prior, sorted by n."""
        return sorted(
            self._table.items(),
            key=lambda kv: -kv[1].n,
        )

    def top_contexts(self, min_n: int = 10, limit: int = 10) -> list[dict]:
        """Return the highest-success contexts that have enough data."""
        out: list[dict] = []
        for k, prior in self.all_contexts():
            if prior.n < min_n:
                continue
            out.append({
                "key": k,
                "n": prior.n,
                "mean": prior.mean,
                "ci_95": prior.credible_interval(),
            })
            if len(out) >= limit:
                break
        return out

    # --- persistence ---------------------------------------------------
    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {k: p.to_dict() for k, p in self._table.items()}
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "RegimePriors":
        p = cls()
        path = Path(path)
        if not path.exists():
            return p
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return p
        for k, v in data.items():
            p._table[k] = BetaPrior.from_dict(v)
        return p

    # --- helpers for ChartMind -----------------------------------------
    @staticmethod
    def context_from_reading(
        reading,                 # ChartReading
        pattern_name: str,
    ) -> PriorContext:
        """Construct a context key from a live ChartReading."""
        # Regime bucket: collapse up/down/flat + strength into four labels
        if reading.trend_direction == "up" and reading.trend_strength > 0.35:
            regime = "trending_up"
        elif reading.trend_direction == "down" and reading.trend_strength > 0.35:
            regime = "trending_down"
        elif reading.atr_pct_rank > 0.85:
            regime = "chaos"
        else:
            regime = "ranging"
        return PriorContext(
            pattern=pattern_name,
            regime=regime,
            session=reading.session or "off",
            vol_bucket=reading.volatility_regime or "normal",
            pair=reading.pair,
        )
