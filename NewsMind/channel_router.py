# -*- coding: utf-8 -*-
"""Channel Router (Chandler three-of-four): rates/growth/safe_haven/flows."""
from __future__ import annotations
from dataclasses import dataclass
from NewsMind.event_classifier import EventRecord


@dataclass
class ChannelImpact:
    rates: float = 0.0
    growth: float = 0.0
    safe_haven: float = 0.0
    flows: float = 0.0

    def net(self) -> float:
        return (0.40 * self.rates + 0.30 * self.growth +
                0.20 * self.safe_haven + 0.10 * self.flows)

    def aligned_count(self) -> int:
        n = self.net()
        if abs(n) < 1e-6:
            return 0
        sign = 1.0 if n > 0 else -1.0
        cnt = 0
        for v in (self.rates, self.growth, self.safe_haven, self.flows):
            if abs(v) > 1e-6 and (v * sign) > 0:
                cnt += 1
        return cnt


def route_event_to_channel(event: EventRecord,
                              bias_sign: float,
                              impact_magnitude: float = 1.0
                              ) -> ChannelImpact:
    signed = bias_sign * impact_magnitude
    ci = ChannelImpact()
    ch = event.channel
    if ch == "rates":
        ci.rates = signed
    elif ch == "growth":
        ci.growth = signed
    elif ch == "safe_haven":
        ci.safe_haven = signed
    elif ch == "flows":
        ci.flows = signed
    else:
        q = signed * 0.25
        ci.rates = ci.growth = ci.safe_haven = ci.flows = q
    # Cross-channel leakage
    if event.event_id == "us.nfp":
        ci.rates += signed * 0.4
    elif event.event_id in ("us.cpi", "us.core_pce"):
        ci.growth += signed * 0.2
    elif event.event_id == "us.fomc_press":
        ci.growth += signed * 0.2
    elif event.event_id in ("eu.ecb_decision", "eu.ecb_press"):
        ci.growth += signed * 0.2
    elif event.channel == "safe_haven":
        ci.flows += signed * 0.3
    return ci
