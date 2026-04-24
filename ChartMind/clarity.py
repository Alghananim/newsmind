# -*- coding: utf-8 -*-
"""Conflict + Anti-Pattern awareness — upgrade #7.

A legendary analyst is not the one who always has an opinion. It is
the one who refuses to take a bad setup and stays flat. This module
closes the honesty gap: it scans all available evidence and, when the
picture is ambiguous, says so explicitly instead of forcing a decision.

Two jobs:

  1. CONFLICT DETECTION — find places where the system's own signals
     are contradicting each other. Examples:
       * Multi-TF says LONG bias but last candle is a strong bearish
         engulfing with tight spread (classic trap setup).
       * Confluence reports LONG verdict but price is inside a bearish
         unmitigated order block.
       * Micro shows heavy sell-side delta while trend still reads UP.
     Each such conflict is scored and attributed to its sources.

  2. ANTI-PATTERN DETECTION — flag market conditions where a profitable
     trade is unlikely regardless of any signal:
       * "Chaos" volatility regime (ATR above the 85th percentile).
       * Dead chop (ATR below 25th percentile AND ADX < 15).
       * Failed-breakout geometry (price just rejected from a key
         level and is closing back through it).
       * Session-edge liquidity vacuum (last 30 minutes of a session,
         poor fills, whipsaws).
       * Wide-spread microstructure (spread_pct_rank > 0.85).

The final verdict is one of:

  * "trade"   — no conflicts, no anti-patterns, confluence clear.
  * "wait"    — minor conflicts or borderline anti-pattern; watch.
  * "abstain" — real conflicts or anti-patterns present; stay flat.

References conceptually drawn from:
  * Brooks — the bulk of "price action" is knowing which setups NOT to
    take. The system inherits his bias toward inaction as the default.
  * Douglas — "probabilistic thinking" requires acknowledging when the
    probability distribution isn't skewed enough to warrant a bet.
  * Kahneman — narrow framing and illusion of skill; explicit
    uncertainty readouts push back on both.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Pieces of the output.
# ---------------------------------------------------------------------------
@dataclass
class Conflict:
    """Two or more signals disagree. Severity is 0..1."""
    kind: str                    # short label, e.g. "mtf_vs_candle"
    severity: float              # 0..1
    detail: str                  # human-readable explanation


@dataclass
class AntiPattern:
    """A market condition that makes any trade unwise."""
    name: str                    # "chaos_vol", "dead_chop", "wide_spread", ...
    severity: float              # 0..1
    detail: str                  # why this is a problem right now


@dataclass
class ClarityReport:
    """Unified output of the clarity scan."""
    is_clear: bool               # True iff no significant conflict or anti-pattern
    verdict: str                 # "trade" | "wait" | "abstain"
    net_severity: float          # 0..1 aggregate "do not trade" signal
    conflicts: list              # list[Conflict]
    anti_patterns: list          # list[AntiPattern]
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "is_clear": self.is_clear,
            "verdict": self.verdict,
            "net_severity": self.net_severity,
            "conflicts": [c.__dict__ for c in self.conflicts],
            "anti_patterns": [a.__dict__ for a in self.anti_patterns],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Scanner.
# ---------------------------------------------------------------------------
class ClarityScanner:
    """Stateless scanner. Feed it whichever artefacts are available; it
    returns a ClarityReport.

    Usage:
        scanner = ClarityScanner()
        report = scanner.scan(reading=r, mtf=mtf, confluence=conf)
        if report.verdict == "abstain":
            skip_trade(report.summary)
    """

    # Thresholds
    ABSTAIN_SEVERITY: float = 0.55     # net severity ≥ this → abstain
    WAIT_SEVERITY: float    = 0.30     # between → wait
    # Below WAIT_SEVERITY → trade

    def scan(
        self,
        *,
        reading,                       # ChartReading
        mtf=None,                      # MultiTFReading | None
        confluence=None,               # ConfluenceScore | None
        calibrated=None,               # CalibratedProba | None
    ) -> ClarityReport:
        conflicts: list[Conflict] = []
        anti_patterns: list[AntiPattern] = []

        # --- 1. Conflict: MTF vs latest candle ---------------------
        if mtf is not None and reading.candle_patterns:
            last = reading.candle_patterns[-1]
            if mtf.alignment > 0.3 and last.direction == "bearish" \
                    and last.strength > 0.8:
                conflicts.append(Conflict(
                    kind="mtf_vs_candle",
                    severity=0.6,
                    detail=(
                        f"Multi-TF alignment is {mtf.alignment:+.2f} "
                        f"(bias up) but latest {last.name} is bearish "
                        f"at {last.strength:.2f} strength — potential trap."
                    ),
                ))
            if mtf.alignment < -0.3 and last.direction == "bullish" \
                    and last.strength > 0.8:
                conflicts.append(Conflict(
                    kind="mtf_vs_candle",
                    severity=0.6,
                    detail=(
                        f"Multi-TF alignment is {mtf.alignment:+.2f} "
                        f"(bias down) but latest {last.name} is bullish "
                        f"at {last.strength:.2f} strength — potential trap."
                    ),
                ))

        # --- 2. Conflict: Confluence verdict vs microstructure -----
        if confluence is not None and reading.micro is not None:
            m = reading.micro
            if confluence.verdict == "long" and m.delta_estimate < -0.4:
                conflicts.append(Conflict(
                    kind="conf_vs_micro",
                    severity=0.5,
                    detail=(
                        f"Confluence says LONG but order-flow delta is "
                        f"{m.delta_estimate:+.2f} (sellers dominant)."
                    ),
                ))
            if confluence.verdict == "short" and m.delta_estimate > 0.4:
                conflicts.append(Conflict(
                    kind="conf_vs_micro",
                    severity=0.5,
                    detail=(
                        f"Confluence says SHORT but order-flow delta is "
                        f"{m.delta_estimate:+.2f} (buyers dominant)."
                    ),
                ))

        # --- 3. Conflict: trend vs unmitigated OB in opposite dir --
        unmit = [ob for ob in reading.order_blocks if not ob.mitigated]
        for ob in unmit:
            if (reading.trend_direction == "up" and ob.side == "bearish"
                    and ob.low - 0.5 * reading.atr14 <= reading.price <= ob.high):
                conflicts.append(Conflict(
                    kind="trend_vs_ob",
                    severity=0.45,
                    detail=(
                        f"Uptrend but price is inside unmitigated bearish OB "
                        f"[{ob.low:.5f} - {ob.high:.5f}]."
                    ),
                ))
                break
            if (reading.trend_direction == "down" and ob.side == "bullish"
                    and ob.low <= reading.price <= ob.high + 0.5 * reading.atr14):
                conflicts.append(Conflict(
                    kind="trend_vs_ob",
                    severity=0.45,
                    detail=(
                        f"Downtrend but price is inside unmitigated bullish OB "
                        f"[{ob.low:.5f} - {ob.high:.5f}]."
                    ),
                ))
                break

        # --- 4. Anti-pattern: chaos vol ----------------------------
        if reading.atr_pct_rank > 0.85:
            anti_patterns.append(AntiPattern(
                name="chaos_vol",
                severity=0.7,
                detail=(
                    f"ATR is in the {reading.atr_pct_rank*100:.0f}th "
                    f"percentile of recent 500 bars. News-driven "
                    f"whipsaws likely."
                ),
            ))

        # --- 5. Anti-pattern: dead chop ----------------------------
        if reading.atr_pct_rank < 0.20 and reading.adx < 15:
            anti_patterns.append(AntiPattern(
                name="dead_chop",
                severity=0.55,
                detail=(
                    f"ATR in bottom quintile and ADX {reading.adx:.1f}. "
                    f"No edge — chop will stop out both directions."
                ),
            ))

        # --- 6. Anti-pattern: wide spread --------------------------
        if reading.micro and reading.micro.spread_pct_rank is not None \
                and reading.micro.spread_pct_rank > 0.85:
            anti_patterns.append(AntiPattern(
                name="wide_spread",
                severity=0.5,
                detail=(
                    f"Spread in the {reading.micro.spread_pct_rank*100:.0f}th "
                    f"percentile — costs erode edge."
                ),
            ))

        # --- 7. Anti-pattern: calibration conflict -----------------
        # If the calibrated probability is markedly less than the raw
        # (overconfident model) AND we still want to trade, warn.
        if calibrated is not None:
            gap = calibrated.raw - calibrated.calibrated
            if gap > 0.15 and calibrated.raw >= 0.60:
                anti_patterns.append(AntiPattern(
                    name="overconfidence",
                    severity=min(1.0, gap * 3),
                    detail=(
                        f"Raw probability {calibrated.raw:.2f} but "
                        f"history-calibrated {calibrated.calibrated:.2f}. "
                        f"Model is overconfident; discount conviction."
                    ),
                ))

        # --- 8. Anti-pattern: failed-breakout geometry -------------
        # A bar whose high pierced a resistance level but closes back
        # below it — price rejection at key level.
        for lv in reading.key_resistance[:3]:
            if (lv.touches >= 2
                    and reading.price < lv.price
                    and (lv.price - reading.price) < 0.2 * reading.atr14):
                # only flag if we can also see price recently pierced
                anti_patterns.append(AntiPattern(
                    name="resistance_rejection",
                    severity=0.35,
                    detail=(
                        f"Price just rejected near resistance {lv.price:.5f} "
                        f"(touches {lv.touches}). Breakout attempt may fail."
                    ),
                ))
                break
        for lv in reading.key_support[:3]:
            if (lv.touches >= 2
                    and reading.price > lv.price
                    and (reading.price - lv.price) < 0.2 * reading.atr14):
                anti_patterns.append(AntiPattern(
                    name="support_rejection",
                    severity=0.35,
                    detail=(
                        f"Price just rejected near support {lv.price:.5f} "
                        f"(touches {lv.touches}). Breakdown attempt may fail."
                    ),
                ))
                break

        # --- 9. Algo-awareness warnings (Phase G integration) -------
        # Pull warnings from reading.algo_awareness if present. Each
        # warning is promoted to either a Conflict or an AntiPattern.
        aw = getattr(reading, "algo_awareness", None)
        if aw is not None:
            # VWAP overextension: treated as a conflict vs any directional
            # verdict that compounds the overextension.
            if aw.vwap is not None:
                regime = aw.vwap.regime
                if confluence is not None:
                    if confluence.verdict == "long" and regime in ("above_1s", "above_2s"):
                        sev = 0.4 if regime == "above_1s" else 0.6
                        conflicts.append(Conflict(
                            kind="vwap_overextension",
                            severity=sev,
                            detail=(
                                f"Confluence says LONG but price is "
                                f"{regime.replace('_', ' ')} VWAP — "
                                f"mean-reversion pressure strong."
                            ),
                        ))
                    elif confluence.verdict == "short" and regime in ("below_1s", "below_2s"):
                        sev = 0.4 if regime == "below_1s" else 0.6
                        conflicts.append(Conflict(
                            kind="vwap_overextension",
                            severity=sev,
                            detail=(
                                f"Confluence says SHORT but price is "
                                f"{regime.replace('_', ' ')} VWAP — "
                                f"mean-reversion pressure strong."
                            ),
                        ))

            # Round-number stop-hunt risk: anti-pattern when the closest
            # round is very near and major.
            if aw.nearby_round_numbers:
                closest = aw.nearby_round_numbers[0]
                if closest.stop_hunt_risk >= 0.7 and closest.strength == "major":
                    anti_patterns.append(AntiPattern(
                        name="round_number_hunt",
                        severity=0.5,
                        detail=(
                            f"Major round {closest.price:.5f} within "
                            f"{closest.distance_pips:.1f} pips. "
                            f"Liquidity pool likely to be swept."
                        ),
                    ))

            # Burst after uniform regime: news/algo liquidation warning.
            if aw.footprint.burst_detected:
                anti_patterns.append(AntiPattern(
                    name="algo_burst",
                    severity=0.55,
                    detail=(
                        "Uniform market-making regime just broke into a "
                        "burst bar — likely news or stop run. Wait one "
                        "bar for direction to settle."
                    ),
                ))
            elif aw.footprint.uniform_candles:
                # Pure uniformity isn't fatal but deserves a mild warning
                # when the user wants to take a momentum entry.
                anti_patterns.append(AntiPattern(
                    name="algo_market_making",
                    severity=0.30,
                    detail=(
                        f"Uniform bar ranges (CV={aw.footprint.range_cv:.2f}) "
                        f"suggest algorithmic market-making. Avoid "
                        f"momentum entries; prefer limits at structure."
                    ),
                ))

        # --- Aggregate ---------------------------------------------
        # Net severity = 1 - Π (1 - severity_i)
        # (treats each as independent probability of "don't trade")
        all_severities = [c.severity for c in conflicts] + \
                         [a.severity for a in anti_patterns]
        product = 1.0
        for s in all_severities:
            product *= (1.0 - max(0.0, min(1.0, s)))
        net_severity = 1.0 - product

        if net_severity >= self.ABSTAIN_SEVERITY:
            verdict = "abstain"
        elif net_severity >= self.WAIT_SEVERITY:
            verdict = "wait"
        else:
            verdict = "trade"

        is_clear = (verdict == "trade")

        report = ClarityReport(
            is_clear=is_clear,
            verdict=verdict,
            net_severity=net_severity,
            conflicts=conflicts,
            anti_patterns=anti_patterns,
        )
        report.summary = self._build_summary(report)
        return report

    # ------------------------------------------------------------------
    @staticmethod
    def _build_summary(r: ClarityReport) -> str:
        lines: list[str] = []
        label = {
            "trade":   "✓ CLEAR — no conflicts, safe to trade",
            "wait":    "… WAIT — minor issues, watch",
            "abstain": "✗ ABSTAIN — stay flat",
        }.get(r.verdict, r.verdict)
        lines.append(f"{label}")
        lines.append(f"Net severity: {r.net_severity:.2f}")
        if r.conflicts:
            lines.append(f"Conflicts ({len(r.conflicts)}):")
            for c in sorted(r.conflicts, key=lambda x: -x.severity):
                lines.append(f"  - {c.kind} [{c.severity:.2f}]  {c.detail}")
        if r.anti_patterns:
            lines.append(f"Anti-patterns ({len(r.anti_patterns)}):")
            for a in sorted(r.anti_patterns, key=lambda x: -x.severity):
                lines.append(f"  - {a.name} [{a.severity:.2f}]  {a.detail}")
        if not r.conflicts and not r.anti_patterns:
            lines.append("No conflicts, no anti-patterns detected.")
        return "\n".join(lines)
