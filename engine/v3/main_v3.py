# -*- coding: utf-8 -*-
"""main_v3.py — Engine V3 minimal live loop.

Runs the 5-brain orchestrator in paper-trading mode unless OANDA env vars
are set, in which case it uses the OANDA broker. NO LIVE TRADES until
explicitly enabled — this is a Live Validation entry point with hard caps.
"""
from __future__ import annotations
import os
import sys
import time
import logging
from datetime import datetime, timezone

# Setup logging FIRST
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)
log = logging.getLogger("main_v3")

log.info("=" * 80)
log.info("Engine V3 — 5-brain integrated trading system")
log.info("=" * 80)

# Imports — these come from v3 packages (lowercase folders)
sys.path.insert(0, "/app")
try:
    from engine.v3 import EngineV3, ValidationConfig
    log.info("[OK] Engine V3 imports successful")
except Exception as e:
    log.error(f"[FATAL] Engine V3 import failed: {e}")
    sys.exit(1)

# Config from env
cfg = ValidationConfig()
poll_interval = int(os.environ.get("POLL_INTERVAL_SEC", "60"))

# Show what's configured
log.info(f"Pair monitored: {cfg.pair_status}")
log.info(f"Risk: {cfg.risk_pct_per_trade}% (max cap: {cfg.absolute_max_risk_pct}%)")
log.info(f"Daily loss limit: {cfg.daily_loss_limit_pct}%")
log.info(f"Broker mode: {cfg.broker_env}")
log.info(f"OPENAI_API_KEY set: {bool(os.environ.get('OPENAI_API_KEY'))}")
log.info(f"OANDA_API_TOKEN set: {bool(os.environ.get('OANDA_API_TOKEN'))}")
log.info(f"OANDA_ACCOUNT_ID set: {bool(os.environ.get('OANDA_ACCOUNT_ID'))}")

if not (os.environ.get("OANDA_API_TOKEN") and os.environ.get("OANDA_ACCOUNT_ID")):
    log.warning("[WARN] OANDA credentials missing — running in IDLE mode (no broker)")
    log.warning("[WARN] Set OANDA_API_TOKEN + OANDA_ACCOUNT_ID via Hostinger Environment Variables")

log.info(f"Poll interval: {poll_interval}s")
log.info("=" * 80)

# Boot Engine V3 (just to verify it instantiates cleanly)
try:
    engine = EngineV3(cfg=cfg, broker=None, account_balance=10000.0)
    log.info("[OK] EngineV3 instantiated; SmartNoteBook ready")
except Exception as e:
    log.error(f"[FATAL] EngineV3 instantiation failed: {e}")
    sys.exit(1)

# Idle loop — heartbeat only. Real trade decisions need OANDA wired up first.
log.info("[loop] entering idle heartbeat loop. Add OANDA env vars to enable trading.")
cycle = 0
try:
    while True:
        cycle += 1
        log.info(f"[heartbeat] cycle={cycle} ts={datetime.now(timezone.utc).isoformat()}")
        # Future: pull OANDA candles, build NewsItem/MarketAssessment/ChartAssessment,
        # call engine.decide_and_maybe_trade(), log result.
        time.sleep(poll_interval)
except KeyboardInterrupt:
    log.info("[shutdown] received KeyboardInterrupt")
    engine.stop()
