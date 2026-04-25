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
}


def get_variant(name: str) -> VariantFilter:
    """Look up a variant by name, falling back to baseline."""
    return VARIANTS.get(name, VARIANTS["baseline"])
