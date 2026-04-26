# -*- coding: utf-8 -*-
"""ValidationConfig — HARD CAPS for Live Validation phase.

CRITICAL: These ceilings cannot be exceeded at runtime. Engine refuses
to even start if env vars try to override them above the absolute limit.
"""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Dict


# ABSOLUTE CEILINGS — hardcoded, env vars cannot exceed these
ABSOLUTE_MAX_RISK_PCT = 0.5            # 0.5% per trade — physical limit
ABSOLUTE_MAX_DAILY_LOSS_PCT = 3.0      # 3% daily max
ABSOLUTE_MAX_TRADES_PER_DAY = 10       # 10 trades/day max
ABSOLUTE_MAX_CONSECUTIVE_LOSSES = 3    # 3 losses then mandatory cooldown
MIN_RR_FOR_LIVE = 1.2                  # below this, refuse


@dataclass
class ValidationConfig:
    risk_pct_per_trade: float = 0.25       # default for validation phase
    max_risk_pct_per_trade: float = ABSOLUTE_MAX_RISK_PCT
    daily_loss_limit_pct: float = 2.0
    consecutive_losses_limit: int = 2
    daily_trade_limit: int = 5

    max_spread_pips: Dict[str, float] = field(default_factory=lambda: {
        "EUR/USD": 1.5, "USD/JPY": 2.0, "GBP/USD": 2.0
    })
    max_slippage_pips: float = 2.0
    min_rr_for_entry: float = 1.2

    pair_status: Dict[str, str] = field(default_factory=lambda: {
        "EUR/USD": "production",
        "USD/JPY": "monitoring",
        "GBP/USD": "disabled",
    })

    broker_env: str = "practice"            # practice|live
    smartnotebook_dir: str = "/tmp/newsmind_notebook"

    @classmethod
    def from_env(cls) -> "ValidationConfig":
        cfg = cls()
        # Load each from env with strict validation
        cfg.risk_pct_per_trade = float(os.environ.get("RISK_PCT_PER_TRADE", 0.25))
        cfg.max_risk_pct_per_trade = float(os.environ.get("MAX_RISK_PCT_PER_TRADE", ABSOLUTE_MAX_RISK_PCT))
        cfg.daily_loss_limit_pct = float(os.environ.get("DAILY_LOSS_LIMIT_PCT", 2.0))
        cfg.consecutive_losses_limit = int(os.environ.get("CONSECUTIVE_LOSSES_LIMIT", 2))
        cfg.daily_trade_limit = int(os.environ.get("DAILY_TRADE_LIMIT", 5))
        cfg.max_slippage_pips = float(os.environ.get("MAX_SLIPPAGE_PIPS", 2.0))
        cfg.broker_env = os.environ.get("OANDA_ENV", "practice")
        cfg.smartnotebook_dir = os.environ.get("SMARTNOTEBOOK_DIR", "/tmp/newsmind_notebook")
        if "MAX_SPREAD_PIPS_EURUSD" in os.environ:
            cfg.max_spread_pips["EUR/USD"] = float(os.environ["MAX_SPREAD_PIPS_EURUSD"])
        if "MAX_SPREAD_PIPS_USDJPY" in os.environ:
            cfg.max_spread_pips["USD/JPY"] = float(os.environ["MAX_SPREAD_PIPS_USDJPY"])
        cfg.validate_or_die()
        return cfg

    def validate_or_die(self):
        """Refuse to start if any cap exceeds absolute ceiling."""
        errors = []
        if self.risk_pct_per_trade > ABSOLUTE_MAX_RISK_PCT:
            errors.append(f"RISK_PCT_PER_TRADE={self.risk_pct_per_trade} > absolute max {ABSOLUTE_MAX_RISK_PCT}")
        if self.max_risk_pct_per_trade > ABSOLUTE_MAX_RISK_PCT:
            errors.append(f"MAX_RISK_PCT_PER_TRADE={self.max_risk_pct_per_trade} > absolute max {ABSOLUTE_MAX_RISK_PCT}")
        if self.risk_pct_per_trade > self.max_risk_pct_per_trade:
            errors.append(f"RISK_PCT > MAX_RISK_PCT")
        if self.daily_loss_limit_pct > ABSOLUTE_MAX_DAILY_LOSS_PCT:
            errors.append(f"DAILY_LOSS_LIMIT_PCT={self.daily_loss_limit_pct} > absolute max {ABSOLUTE_MAX_DAILY_LOSS_PCT}")
        if self.consecutive_losses_limit > ABSOLUTE_MAX_CONSECUTIVE_LOSSES:
            errors.append(f"CONSECUTIVE_LOSSES_LIMIT={self.consecutive_losses_limit} > absolute max {ABSOLUTE_MAX_CONSECUTIVE_LOSSES}")
        if self.daily_trade_limit > ABSOLUTE_MAX_TRADES_PER_DAY:
            errors.append(f"DAILY_TRADE_LIMIT={self.daily_trade_limit} > absolute max {ABSOLUTE_MAX_TRADES_PER_DAY}")
        if self.min_rr_for_entry < MIN_RR_FOR_LIVE:
            errors.append(f"MIN_RR_FOR_ENTRY={self.min_rr_for_entry} < required {MIN_RR_FOR_LIVE}")
        if self.broker_env not in ("practice", "live"):
            errors.append(f"OANDA_ENV={self.broker_env} not in (practice, live)")
        if errors:
            raise SystemExit(
                "FATAL: ValidationConfig refused to start.\n  "
                + "\n  ".join(errors)
                + "\nFix .env values before retrying.")
