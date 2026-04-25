# -*- coding: utf-8 -*-
"""GateMind — final-stage decision orchestrator for the EUR/USD pipeline.

Public API (everything a caller needs to wire GateMind into a runtime):

    from GateMind import (
        GateMind, GateMindContext, CycleResult,
        BrainGrade, GateConfig, GateDecision,
        KillSwitchConfig, KillSwitchInputs, KillSwitchVerdict,
        RiskConfig, SizedTrade, size_trade,
        Portfolio, Position,
        Broker, OandaBroker, PaperBroker, ExecutionRouter,
        OrderSpec, ExecutionReceipt, RouterConfig,
        Ledger,
    )
    from GateMind import narrative

Layered design: each submodule is independently importable for tests.
The `GateMind` class composes them into a working pipeline.
"""
from .decision import (
    BrainGrade,
    GateConfig,
    GateDecision,
    evaluate as evaluate_gate,
)
from .kill_switches import (
    KillSwitchConfig,
    KillSwitchInputs,
    KillSwitchVerdict,
    evaluate as evaluate_kill_switches,
)
from .risk import (
    RiskConfig,
    SizedTrade,
    size_trade,
    to_r_multiple,
    expectancy_r,
)
from .portfolio import (
    Portfolio,
    Position,
    PortfolioSnapshot,
)
from .execution_router import (
    Broker,
    OandaBroker,
    PaperBroker,
    ExecutionRouter,
    OrderSpec,
    ExecutionReceipt,
    RouterConfig,
)
from .ledger import (
    Ledger,
    LedgerRecord,
)
from . import narrative
from .GateMind import (
    GateMind,
    GateMindContext,
    CycleResult,
)

__all__ = [
    # Orchestrator
    "GateMind", "GateMindContext", "CycleResult",
    # Decision
    "BrainGrade", "GateConfig", "GateDecision", "evaluate_gate",
    # Kill switches
    "KillSwitchConfig", "KillSwitchInputs", "KillSwitchVerdict",
    "evaluate_kill_switches",
    # Risk
    "RiskConfig", "SizedTrade", "size_trade",
    "to_r_multiple", "expectancy_r",
    # Portfolio
    "Portfolio", "Position", "PortfolioSnapshot",
    # Execution
    "Broker", "OandaBroker", "PaperBroker",
    "ExecutionRouter", "OrderSpec", "ExecutionReceipt", "RouterConfig",
    # Ledger
    "Ledger", "LedgerRecord",
    # Narrative
    "narrative",
]

__version__ = "1.0.0"
