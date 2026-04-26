# -*- coding: utf-8 -*-
"""main_v3.py — Engine V3 live loop with OpenAI integration."""
from __future__ import annotations
import os
import sys
import time
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%S',
)
log = logging.getLogger("main_v3")

log.info("=" * 80)
log.info("Engine V3 — 5-brain integrated trading system + OpenAI layer")
log.info("=" * 80)

sys.path.insert(0, "/app")

# Brain imports
try:
    from engine.v3 import EngineV3, ValidationConfig, ABSOLUTE_MAX_RISK_PCT
    log.info("[OK] Engine V3 imports successful")
except Exception as e:
    log.error(f"[FATAL] Engine V3 import failed: {e}")
    sys.exit(1)

# LLM layer (optional, default-deny on failure)
try:
    from llm import (
        review_brain_outputs, LLM_AVAILABLE, LLM_DISABLED_REASON,
    )
    from llm.openai_brain import health_check as llm_health
    log.info(f"[OK] LLM module imported (available={LLM_AVAILABLE})")
except Exception as e:
    log.warning(f"[WARN] LLM module not available: {e}")
    LLM_AVAILABLE = False
    review_brain_outputs = None
    llm_health = None

# Config
cfg = ValidationConfig()
poll_interval = int(os.environ.get("POLL_INTERVAL_SEC", "60"))

log.info(f"Risk per trade: {cfg.risk_pct_per_trade}% (absolute max: {ABSOLUTE_MAX_RISK_PCT}%)")
log.info(f"Daily loss limit: {cfg.daily_loss_limit_pct}%")
log.info(f"Broker mode: {cfg.broker_env}")
log.info(f"OPENAI_API_KEY set: {bool(os.environ.get('OPENAI_API_KEY'))}")
log.info(f"OANDA_API_TOKEN set: {bool(os.environ.get('OANDA_API_TOKEN'))}")
log.info(f"OANDA_ACCOUNT_ID set: {bool(os.environ.get('OANDA_ACCOUNT_ID'))}")

if not (os.environ.get("OANDA_API_TOKEN") and os.environ.get("OANDA_ACCOUNT_ID")):
    log.warning("[WARN] OANDA credentials missing — IDLE mode (no broker)")

log.info(f"Poll interval: {poll_interval}s")

# Engine V3 setup
try:
    engine = EngineV3(cfg=cfg, broker=None, account_balance=10000.0)
    log.info("[OK] EngineV3 instantiated; SmartNoteBook ready")
except Exception as e:
    log.error(f"[FATAL] EngineV3 instantiation failed: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

# LLM connectivity check at boot
if LLM_AVAILABLE and llm_health:
    log.info("[llm] running connectivity check...")
    try:
        h = llm_health()
        if h.get("available"):
            log.info(f"[llm] OK — model={h.get('model')} latency={h.get('sample_response','')[:60]}")
        else:
            log.warning(f"[llm] unavailable: {h.get('reason')}")
    except Exception as e:
        log.warning(f"[llm] health check threw: {e}")
else:
    log.info(f"[llm] disabled at boot (reason: {LLM_DISABLED_REASON if LLM_AVAILABLE is False else 'no_module'})")

log.info("=" * 80)
log.info("[loop] entering idle heartbeat loop")
log.info("=" * 80)

cycle = 0
try:
    while True:
        cycle += 1
        ts = datetime.now(timezone.utc).isoformat(timespec='seconds')
        log.info(f"[heartbeat] cycle={cycle} ts={ts}")
        # Future: pull OANDA candles → brain analysis → LLM review → GateMind → execute
        # For now: idle. LLM layer is wired and ready.
        time.sleep(poll_interval)
except KeyboardInterrupt:
    log.info("[shutdown]")
    engine.stop()
