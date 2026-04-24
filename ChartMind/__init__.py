# -*- coding: utf-8 -*-
"""ChartMind — the technical-analysis brain.

Usage:
    from ChartMind import ChartMind
    cm = ChartMind()
    reading = cm.read(df_m15, pair="EUR_USD")
    print(reading.summary)
"""
from ChartMind.ChartMind import (   # noqa: F401
    ChartMind, ChartReading, MultiTFReading, Microstructure,
    ConfluenceScore, ConfluenceFactor, Analysis,
)
from ChartMind.priors import (    # noqa: F401
    RegimePriors, PriorContext, PriorQueryResult, BetaPrior,
)
from ChartMind.calibration import (   # noqa: F401
    SelfCalibration, TradePrediction, CalibrationReport, CalibrationBucket,
)
from ChartMind.calibrated_confidence import (   # noqa: F401
    CalibratedConfidence, CalibratedProba,
)
from ChartMind.clarity import (   # noqa: F401
    ClarityScanner, ClarityReport, Conflict, AntiPattern,
)
from ChartMind.narrative import (   # noqa: F401
    NarrativeGenerator, Narrative,
)
from ChartMind.traps import (   # noqa: F401
    Trap, TrapConfig, detect_traps,
)
from ChartMind.wyckoff import (   # noqa: F401
    WyckoffPhase, WyckoffEvent, detect_wyckoff,
)
from ChartMind.price_action import (   # noqa: F401
    PriceActionContext, SignalBar, EntryBar, Pullback,
    StructuralFailure, TransitionBar, read_price_action,
)
from ChartMind.chart_patterns import (   # noqa: F401
    ChartPattern, detect_chart_patterns,
)
from ChartMind.planner import (   # noqa: F401
    TradePlan, PositionHealth, generate_plan, monitor_position,
)
from ChartMind.execution import (   # noqa: F401
    EntryPlan, ExecutionContext, decide_entry, price_to_pips,
)
from ChartMind.algo_awareness import (   # noqa: F401
    AlgoAwareness, VWAPContext, RoundNumberZone, AlgoFootprint,
    read_algo_awareness,
)
