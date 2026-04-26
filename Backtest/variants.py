# -*- coding: utf-8 -*-
"""VariantFilter — post-signal filters for systematic strategy improvement.

After the OANDA baseline showed every pair losing, we use real-data
breakdowns (by_setup, by_hour, monthly) to design targeted filters.
A `VariantFilter` is a thin layer between ChartMind's plan output
and the runner's `_pending` queue. Filters DO NOT change ChartMind
itself — they only decide whether to ACT on a plan.

Why filter rather than re-train ChartMind
-----------------------------------------
1. Speed: one parameter sweep takes minutes, not days of re-training.
2. Falsifiability: each filter is a single hypothesis we can defend.
3. Audit trail: the same ChartMind output is used; only acceptance
   changes. We can later compute "accept-rate by setup × hour" to
   diagnose the filter itself.

Variants we ship
----------------
    baseline                  # no filters — reproduces the original
    kill_asia                 # drop 04-08 UTC (universally bad)
    london_only               # only 08-12 UTC (universally good)
    london_overlap            # 08-16 UTC (London + NY morning)
    drop_double_top           # exclude pattern_double_top (loser)
    continuation_focus        # only signal_entry_continuation
    prime                     # combined: kill_asia + drop_double_top
                              # + min_confidence 0.55 + min_rr 1.8
    prime_no_halt             # prime but with halt disabled (diagnostic)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class VariantFilter:
    """Post-ChartMind acceptance filters.

    Empty/zero values mean "no filtering on this dimension" so a
    baseline VariantFilter() reproduces the original behaviour.
    """
    name: str = "baseline"

    # Hours-of-day acceptance, in UTC. Empty tuple = accept all.
    # Examples:
    #   ()                              → accept all hours
    #   (8, 9, 10, 11)                   → London only
    #   (8, 9, 10, 11, 12, 13, 14, 15)   → London + NY morning
    allowed_hours_utc: tuple = ()

    # Hours-of-day blacklist, in UTC. Applied after allow-list.
    # Examples:
    #   ()                              → no blocking
    #   (4, 5, 6, 7)                     → kill Asian session tail
    blocked_hours_utc: tuple = ()

    # Setup-type acceptance. Empty = accept all.
    # Set to e.g. ("signal_entry_continuation",) for continuation-only.
    allowed_setups: tuple = ()

    # Setup-type blacklist. Applied after allow-list.
    # Example: ("pattern_double_top",) to drop the worst loser.
    blocked_setups: tuple = ()

    # Minimum ChartMind confidence (0.0 to 1.0). 0 = accept all.
    min_confidence: float = 0.0

    # Minimum reward-to-risk ratio. 0 = accept all.
    min_rr: float = 0.0

    # Diagnostic: disable the runner's max-DD halt to see the full
    # 2-year curve even when the strategy bleeds. NEVER use in
    # production; only for offline parameter sweep.
    disable_max_dd_halt: bool = False

    # Trailing stop: once MFE reaches `trail_stop_after_r` × initial-risk,
    # move the stop to break-even. After that, every additional 0.5R of
    # MFE moves the stop another 0.5R closer (asymmetric trail).
    # 0.0 = disabled (no trailing).
    trail_stop_after_r: float = 0.0

    # Override the BacktestConfig.risk_per_trade_pct for THIS variant
    # only. None = inherit. Useful for testing higher-risk profiles
    # without touching the global config.
    risk_pct_override: float | None = None

    # Override the time-budget (bars to hold). None = inherit (12 bars).
    # Lower = forces faster exits; higher = lets trades breathe longer.
    time_budget_override: int | None = None

    # Pause-and-resume on DD halt. 0 = use the original kill switch
    # (halt forever once 15% DD breached). N > 0 = pause N days after
    # the halt, then RESET the risk state (peak = current equity) and
    # resume trading. Models a real operator who takes a forced break
    # but doesn't liquidate the account. Recommended N = 7 (one week).
    halt_pause_days: int = 0

    # ATR / volatility surge filter. If max(recent_atr) / mean(longer_atr)
    # > atr_surge_threshold, skip new entries this bar. Catches news
    # spikes, BoJ shocks, geopolitical event candles. 0 = disabled.
    # Recommended: 1.8 (skip when current vol >1.8x recent average).
    atr_surge_threshold: float = 0.0

    # Regime filter. Empty tuple = trade in any regime (legacy).
    # Otherwise, only trade when the RegimeDetector classifies the
    # current bar's regime as one of these labels:
    #   "TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE", "QUIET"
    # The walk-forward audit showed our pattern strategy collapses in
    # RANGING/QUIET regimes; default production should be ("TRENDING_UP",
    # "TRENDING_DOWN") to limit trading to genuinely-trending markets.
    allowed_regimes: tuple = ()

    # Minimum ADX threshold (Wilder). 0 = no ADX gate.
    # 25 = canonical strong trend; 20 = mild trend.
    # Used INSIDE allowed_regimes filter as an extra strength check.
    min_adx: float = 0.0

    # Maximum allowed spread multiplier (current_spread / recent_avg_spread).
    # 0 = no filter. 2.0 = reject when current spread is >2x normal —
    # catches news widening, low-liquidity periods (audit S3).
    max_spread_multiplier: float = 0.0

    # Maximum trades per UTC day. 0 = unlimited. Caps over-trading
    # which destroyed several variants in the diagnostic (audit F3).
    max_trades_per_day: int = 0

    # Cooling-off after N consecutive losses inside the same UTC day.
    # 0 = use the global RiskManager.max_consecutive_losses (default 3).
    # 2 = stop trading after 2 losses in a day (audit F4).
    max_daily_consecutive_losses: int = 0

    # Minimum grade for entry: "C", "B", "A", "A+".
    # "B" = require >=B (B and above).
    # "A" = require >=A (B no entry per audit Q3 commandment).
    # Default "C" = no grade gate. Recommended production: "B" or "A".
    min_grade: str = "C"

    # Per-grade risk multipliers. Production rule (audit R5/Q4):
    # A+ uses full risk, A uses 0.75x, B uses 0.5x or wait, C reject.
    # Map of grade -> risk fraction. Defaults preserve legacy behaviour.
    grade_risk_multipliers: tuple = ()

    # Use ChartMindV2 (rebuilt confluence-based) instead of v1.
    # Default False = legacy v1 pattern matcher.
    # True = v2 with multi-timeframe trend + structure + candles + momentum
    # + regime + 6-factor confluence + A+/A/B/C grade.
    use_chartmind_v2: bool = False

    # Minimum grade for v2 (only used when use_chartmind_v2=True).
    # "C" = no filter, "B" = require >=B, "A" = require >=A, etc.
    v2_min_grade: str = "B"

    # Minimum confluence score for v2 (0..6).
    v2_min_confluence: float = 4.0

    # ===============================================================
    # The decision function.
    # ===============================================================
    def accept(self, *, bar_time: datetime,
               setup_type: str, confidence: float,
               rr_ratio: float) -> tuple[bool, str]:
        """Return (accept, reason). reason is empty when accepted, or
        a short string identifying which filter rejected the plan.
        """
        # Hours
        h = bar_time.hour
        if self.allowed_hours_utc and h not in self.allowed_hours_utc:
            return False, f"hour_not_allowed:{h}"
        if h in self.blocked_hours_utc:
            return False, f"hour_blocked:{h}"

        # Setup
        if self.allowed_setups and setup_type not in self.allowed_setups:
            return False, f"setup_not_allowed:{setup_type}"
        if setup_type in self.blocked_setups:
            return False, f"setup_blocked:{setup_type}"

        # Confidence
        if confidence < self.min_confidence:
            return False, f"low_confidence:{confidence:.2f}"

        # R:R
        if self.min_rr > 0 and rr_ratio < self.min_rr:
            return False, f"low_rr:{rr_ratio:.2f}"

        return True, ""


# ----------------------------------------------------------------------
# The catalog of variants we will systematically test.
# ----------------------------------------------------------------------
VARIANTS: dict[str, VariantFilter] = {
    "baseline": VariantFilter(name="baseline"),

    "kill_asia": VariantFilter(
        name="kill_asia",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
    ),

    "london_only": VariantFilter(
        name="london_only",
        allowed_hours_utc=(8, 9, 10, 11),
    ),

    "london_overlap": VariantFilter(
        name="london_overlap",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
    ),

    "drop_double_top": VariantFilter(
        name="drop_double_top",
        blocked_setups=("pattern_double_top",),
    ),

    "continuation_focus": VariantFilter(
        name="continuation_focus",
        allowed_setups=("signal_entry_continuation",),
    ),

    "prime": VariantFilter(
        name="prime",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        blocked_setups=("pattern_double_top", "two_legged_pullback"),
        min_confidence=0.55,
        min_rr=1.8,
    ),

    "prime_no_halt": VariantFilter(
        name="prime_no_halt",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        blocked_setups=("pattern_double_top", "two_legged_pullback"),
        min_confidence=0.55,
        min_rr=1.8,
        disable_max_dd_halt=True,
    ),

    "ultra_quality": VariantFilter(
        name="ultra_quality",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
        allowed_setups=("signal_entry_continuation", "pattern_double_bottom"),
        min_confidence=0.6,
        min_rr=2.0,
    ),

    # ------------------------------------------------------------------
    # Round-2 variants: trailing stop, risk scaling, per-pair tuning.
    # ------------------------------------------------------------------

    # EUR/USD per-pair tuning. Best round-1 finding: kill_asia +5.34%.
    "eu_pro": VariantFilter(
        name="eu_pro",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
    ),
    "eu_pro_risk1": VariantFilter(
        name="eu_pro_risk1",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
        risk_pct_override=1.0,
    ),
    "eu_pro_risk15": VariantFilter(
        name="eu_pro_risk15",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
        risk_pct_override=1.5,
    ),

    # USD/JPY per-pair tuning. Best round-1 finding: london_only +7.31%.
    "jp_pro": VariantFilter(
        name="jp_pro",
        allowed_hours_utc=(8, 9, 10, 11),
        trail_stop_after_r=1.0,
    ),
    "jp_pro_risk1": VariantFilter(
        name="jp_pro_risk1",
        allowed_hours_utc=(8, 9, 10, 11),
        trail_stop_after_r=1.0,
        risk_pct_override=1.0,
    ),
    "jp_pro_risk15": VariantFilter(
        name="jp_pro_risk15",
        allowed_hours_utc=(8, 9, 10, 11),
        trail_stop_after_r=1.0,
        risk_pct_override=1.5,
    ),

    # GBP/USD per-pair tuning. Best round-1: ultra_quality +4.13% (no halt!)
    "gb_pro": VariantFilter(
        name="gb_pro",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
        allowed_setups=("signal_entry_continuation", "pattern_double_bottom"),
        min_confidence=0.6,
        min_rr=2.0,
        trail_stop_after_r=1.0,
    ),
    "gb_pro_risk1": VariantFilter(
        name="gb_pro_risk1",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
        allowed_setups=("signal_entry_continuation", "pattern_double_bottom"),
        min_confidence=0.6,
        min_rr=2.0,
        trail_stop_after_r=1.0,
        risk_pct_override=1.0,
    ),

    # Diagnostic full-window runs (halt disabled, baseline filters).
    "kill_asia_no_halt": VariantFilter(
        name="kill_asia_no_halt",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        disable_max_dd_halt=True,
    ),
    "london_only_no_halt": VariantFilter(
        name="london_only_no_halt",
        allowed_hours_utc=(8, 9, 10, 11),
        disable_max_dd_halt=True,
    ),

    # ------------------------------------------------------------------
    # Round-3: USD/JPY scale-up (the +51.72% champion variant). Test
    # how far it can go with higher risk and halt off.
    # ------------------------------------------------------------------
    "jp_champion_risk15": VariantFilter(
        name="jp_champion_risk15",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
        risk_pct_override=1.5,
    ),
    "jp_champion_risk2": VariantFilter(
        name="jp_champion_risk2",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
        risk_pct_override=2.0,
    ),
    "jp_champion_no_halt": VariantFilter(
        name="jp_champion_no_halt",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
        risk_pct_override=1.0,
        disable_max_dd_halt=True,
    ),
    "jp_champion_loose_trail": VariantFilter(
        name="jp_champion_loose_trail",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=0.5,             # earlier trail = catch more wins
        risk_pct_override=1.0,
    ),
    "jp_champion_tight_trail": VariantFilter(
        name="jp_champion_tight_trail",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,             # later trail = breathe more
        risk_pct_override=1.0,
    ),
    "jp_champion_short_budget": VariantFilter(
        name="jp_champion_short_budget",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
        risk_pct_override=1.0,
        time_budget_override=8,             # 2-hour cap
    ),
    "jp_champion_long_budget": VariantFilter(
        name="jp_champion_long_budget",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
        risk_pct_override=1.0,
        time_budget_override=48,            # 12-hour cap
    ),

    # EUR/USD: try +trail with risk 0.75 (between 0.5 and 1.0).
    "eu_kill_asia_trail_risk075": VariantFilter(
        name="eu_kill_asia_trail_risk075",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.0,
        risk_pct_override=0.75,
    ),
    "eu_kill_asia_no_halt": VariantFilter(
        name="eu_kill_asia_no_halt",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        disable_max_dd_halt=True,
    ),

    # GBP/USD: lock ultra_quality and test risk scaling without halt.
    "gb_ultra_risk1": VariantFilter(
        name="gb_ultra_risk1",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
        allowed_setups=("signal_entry_continuation", "pattern_double_bottom"),
        min_confidence=0.6,
        min_rr=2.0,
        risk_pct_override=1.0,
    ),
    "gb_ultra_risk15": VariantFilter(
        name="gb_ultra_risk15",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
        allowed_setups=("signal_entry_continuation", "pattern_double_bottom"),
        min_confidence=0.6,
        min_rr=2.0,
        risk_pct_override=1.5,
    ),

    # ------------------------------------------------------------------
    # Round-4: scale jp_champion_tight_trail further (it just did +105%
    # on USD/JPY, +18% EUR/USD, halted at 15.5% DD).
    # ------------------------------------------------------------------

    # Same recipe, push risk
    "tight_trail_risk15": VariantFilter(
        name="tight_trail_risk15",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.5,
    ),
    "tight_trail_risk2": VariantFilter(
        name="tight_trail_risk2",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=2.0,
    ),

    # Same recipe, halt off (full 2-year curve, see compounding)
    "tight_trail_no_halt": VariantFilter(
        name="tight_trail_no_halt",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.0,
        disable_max_dd_halt=True,
    ),
    "tight_trail_risk15_no_halt": VariantFilter(
        name="tight_trail_risk15_no_halt",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.5,
        disable_max_dd_halt=True,
    ),

    # Variations on trail R
    "trail_r2": VariantFilter(
        name="trail_r2",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=2.0,
        risk_pct_override=1.0,
    ),
    "trail_r25": VariantFilter(
        name="trail_r25",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=2.5,
        risk_pct_override=1.0,
    ),

    # Even longer time budget (full session)
    "tight_trail_budget48": VariantFilter(
        name="tight_trail_budget48",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.0,
        time_budget_override=48,
    ),

    # The "production" candidate: best discovered config with min_rr
    # gate to drop low-quality entries (filters out weak setups).
    "production_v1": VariantFilter(
        name="production_v1",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.0,
        min_rr=1.8,
        min_confidence=0.5,
    ),
    "production_v2": VariantFilter(
        name="production_v2",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.5,
        min_rr=1.8,
        min_confidence=0.5,
    ),

    # ------------------------------------------------------------------
    # Round-5: push EUR/USD trend riding further (trail_r25 was +44.93%)
    # ------------------------------------------------------------------
    "trail_r3": VariantFilter(
        name="trail_r3",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=3.0,
        risk_pct_override=1.0,
    ),
    "trail_r35": VariantFilter(
        name="trail_r35",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=3.5,
        risk_pct_override=1.0,
    ),
    "trail_r4": VariantFilter(
        name="trail_r4",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=4.0,
        risk_pct_override=1.0,
    ),
    "trail_r25_risk15": VariantFilter(
        name="trail_r25_risk15",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=2.5,
        risk_pct_override=1.5,
    ),
    "trail_r3_risk15": VariantFilter(
        name="trail_r3_risk15",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=3.0,
        risk_pct_override=1.5,
    ),

    # ------------------------------------------------------------------
    # Round-6: ROBUST production candidates with halt_pause + ATR filter.
    # These are the round-5 per-pair winners hardened with:
    #   - halt_pause_days=7 (resume after a week, instead of dying)
    #   - atr_surge_threshold=3.0 (skip news-spike candles)
    # Tested via walk_forward.py over rolling 90-day quarters.
    # ------------------------------------------------------------------
    "robust_eur": VariantFilter(
        name="robust_eur",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=2.5,
        risk_pct_override=1.5,
        halt_pause_days=7,
        atr_surge_threshold=3.0,
    ),
    "robust_jpy": VariantFilter(
        name="robust_jpy",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=3.0,
    ),
    "robust_gbp": VariantFilter(
        name="robust_gbp",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
        allowed_setups=("signal_entry_continuation", "pattern_double_bottom"),
        min_confidence=0.6,
        min_rr=2.0,
        risk_pct_override=1.5,
        halt_pause_days=7,
        atr_surge_threshold=3.0,
    ),

    # ------------------------------------------------------------------
    # Round-7: REGIME-AWARE production candidates (the missing piece).
    # Walk-forward audit showed our trend-following pattern detector
    # crushes Q1 2024 (USD/JPY uptrend) but bleeds Q3-Q7 2025 (chop
    # after BoJ intervention). Fix: only trade in TRENDING regimes,
    # confirmed by ADX >= 25.
    # ------------------------------------------------------------------
    "regime_eur": VariantFilter(
        name="regime_eur",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=2.5,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=1.8,
        allowed_regimes=("TRENDING_UP", "TRENDING_DOWN"),
        min_adx=25.0,
    ),
    "regime_jpy": VariantFilter(
        name="regime_jpy",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=1.8,
        allowed_regimes=("TRENDING_UP", "TRENDING_DOWN"),
        min_adx=25.0,
    ),
    "regime_gbp": VariantFilter(
        name="regime_gbp",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
        allowed_setups=("signal_entry_continuation", "pattern_double_bottom"),
        min_confidence=0.45,    # lowered to match recalibrated grades
        min_rr=2.0,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=1.8,
        allowed_regimes=("TRENDING_UP", "TRENDING_DOWN"),
        min_adx=20.0,           # GBP/USD ranges more — milder ADX gate
    ),

    # Conservative variant: trend regime + ADX strict + ATR filter
    "regime_strict": VariantFilter(
        name="regime_strict",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=2.0,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=1.5,
        allowed_regimes=("TRENDING_UP", "TRENDING_DOWN"),
        min_adx=30.0,
    ),

    # Aggressive: regime filter only, no halt limit
    "regime_aggressive": VariantFilter(
        name="regime_aggressive",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.5,
        halt_pause_days=14,
        atr_surge_threshold=2.0,
        allowed_regimes=("TRENDING_UP", "TRENDING_DOWN"),
        min_adx=20.0,
    ),

    # ------------------------------------------------------------------
    # ChartMindV2 — rebuilt confluence engine. Replaces v1 pattern
    # matching with multi-timeframe trend alignment + structure
    # confluence + candle confirmation at structure + momentum
    # agreement + regime gate.
    # ------------------------------------------------------------------
    "v2_balanced_eur": VariantFilter(
        name="v2_balanced_eur",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=2.0,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=1.8,
        use_chartmind_v2=True,
        v2_min_grade="B",
        v2_min_confluence=4.0,
    ),
    "v2_balanced_jpy": VariantFilter(
        name="v2_balanced_jpy",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=1.5,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=1.8,
        use_chartmind_v2=True,
        v2_min_grade="B",
        v2_min_confluence=4.0,
    ),
    "v2_balanced_gbp": VariantFilter(
        name="v2_balanced_gbp",
        allowed_hours_utc=(8, 9, 10, 11, 12, 13, 14, 15),
        trail_stop_after_r=2.0,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=1.8,
        use_chartmind_v2=True,
        v2_min_grade="B",
        v2_min_confluence=4.0,
    ),
    "v2_strict": VariantFilter(
        name="v2_strict",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        trail_stop_after_r=2.5,
        risk_pct_override=1.0,
        halt_pause_days=7,
        atr_surge_threshold=1.5,
        use_chartmind_v2=True,
        v2_min_grade="A",
        v2_min_confluence=5.0,
    ),

    # ------------------------------------------------------------------
    # PRODUCTION POLICY (user commandment):
    #   * C grade: NEVER enter
    #   * B grade: WAIT only — no entry
    #   * A grade: ENTER at 1.0x risk
    #   * A+ grade: ENTER at 1.5x risk
    # ------------------------------------------------------------------

    # PRODUCTION (canonical) — A-only entries per user policy.
    # All 12 audit-driven commandments + strict grade enforcement.
    "production": VariantFilter(
        name="production",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        max_spread_multiplier=2.0,
        max_trades_per_day=5,
        max_daily_consecutive_losses=2,
        atr_surge_threshold=1.8,
        min_grade="A",          # B = wait per user policy
        min_rr=2.0,
        # grade_risk_multipliers fall through to defaults: A=1.0, A+=1.5
    ),

    # PRODUCTION STRICT — even tighter (RR 2.5, max 3 trades/day).
    "production_strict": VariantFilter(
        name="production_strict",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        max_spread_multiplier=1.5,
        max_trades_per_day=3,
        max_daily_consecutive_losses=2,
        atr_surge_threshold=1.5,
        min_grade="A",
        min_rr=2.5,
        min_confidence=0.55,
    ),

    # production_safe kept under old name as alias for backward-compat,
    # but maps to the strict A-only policy (no more B entries).
    "production_safe": VariantFilter(
        name="production_safe",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        max_spread_multiplier=2.0,
        max_trades_per_day=5,
        max_daily_consecutive_losses=2,
        atr_surge_threshold=1.8,
        min_grade="A",          # FIXED per user policy (was "B")
        min_rr=2.0,
    ),

    # DIAGNOSTIC ONLY — tests if B is truly the best edge (synthetic
    # evidence shows B has +0.169R while A has -0.135R). Used to expose
    # ChartMind grade calibration inversion.
    "diag_b_only": VariantFilter(
        name="diag_b_only",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        min_grade="B",
        # Custom multipliers: only B enters, A/A+ also enter
        grade_risk_multipliers=(("A+", 1.0), ("A", 1.0), ("B", 1.0), ("C", 0.0)),
    ),

    "diag_b_exclusive": VariantFilter(
        name="diag_b_exclusive",
        blocked_hours_utc=(0, 1, 2, 3, 4, 5, 6, 7),
        # B exclusive — A/A+ rejected via custom multipliers
        grade_risk_multipliers=(("A+", 0.0), ("A", 0.0), ("B", 1.0), ("C", 0.0)),
    ),
}


def get_variant(name: str) -> VariantFilter:
    """Look up a variant by name, falling back to baseline."""
    return VARIANTS.get(name, VARIANTS["baseline"])
