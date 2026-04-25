# -*- coding: utf-8 -*-
"""main.py — live polling entry point for the five-brain trading system.

What runs on the VPS
--------------------
This is the long-lived process that Hostinger's Docker Manager
launches from `docker-compose.yml`. It owns:

    * an `Engine` instance composed of all five brains (NewsMind +
      MarketMind + ChartMind + GateMind + SmartNoteBook),
    * a polling loop that ticks every `POLL_INTERVAL_SEC`,
    * structured stdout logging so the Hostinger UI surfaces a single
      readable line per cycle,
    * graceful SIGTERM handling so state is checkpointed before the
      container stops,
    * a once-per-day morning briefing line (SmartNoteBook's institutional
      memory: yesterday's evidence becoming today's discipline).

What this loop does NOT do (by design)
--------------------------------------
    * It does **not** fetch live OHLC bars. ChartMind's analyse() is
      only invoked when an upstream feed supplies a bar; in this
      streaming-only mode bars are supplied by an external bar-builder
      (a separate process or a callback) — keeping main.py focused on
      news polling and brain-state maintenance.
    * It does **not** fetch live cross-asset bundles for MarketMind.
      Same reason — those feeds are pulled by their own scripts and
      passed in.
    * It does **not** submit live orders by default. GateMind defaults
      to `PaperBroker`. A real `OandaBroker` is wired only when the
      caller swaps it in (`Engine(..., broker=OandaBroker(...))`).

Result: on a stock VPS deploy, this process polls news, ingests it,
maintains narrative + regime + bias state, and writes a one-line
status every cycle. The other brains stay warm and ready; the moment
bar/bundle feeds are wired, the same loop will compose full decisions
without code changes.

Stop with SIGTERM (`docker stop`); state is flushed on exit.

Environment knobs
-----------------
    POLL_INTERVAL_SEC          (default 60)
    CHECKPOINT_EVERY_CYCLES    (default 5)
    BRIEFING_EVERY_CYCLES      (default 60   → one per hour at 60s/cycle)
    PAIR                       (default "EUR/USD")
    STATE_DIR                  (default "/app/NewsMind/state")
"""
from __future__ import annotations

import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Make sure our packages are importable from /app.
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    print(f"[{_now_iso()}] {msg}", flush=True)


def main() -> int:
    from Engine import Engine, EngineConfig

    interval = int(os.environ.get("POLL_INTERVAL_SEC", "60"))
    checkpoint_every = int(os.environ.get("CHECKPOINT_EVERY_CYCLES", "5"))
    briefing_every = int(os.environ.get("BRIEFING_EVERY_CYCLES", "60"))
    pair = os.environ.get("PAIR", "EUR/USD")
    state_dir = os.environ.get("STATE_DIR", "/app/NewsMind/state")

    enable_llm = (
        os.environ.get("ENABLE_LLM", "auto").lower() in ("1", "true", "yes")
        or (os.environ.get("ENABLE_LLM", "auto").lower() == "auto"
            and bool(os.environ.get("OPENAI_API_KEY")))
    )

    config = EngineConfig(
        pair=pair,
        state_dir=state_dir,
        injection_language=os.environ.get("INJECTION_LANGUAGE", "en"),
        enable_llm=enable_llm,
        llm_model=os.environ.get("LLM_MODEL", "gpt-5"),
    )
    config_dir = ROOT / "NewsMind" / "config"
    engine = Engine(
        config=config,
        newsmind_config_dir=config_dir,
    )

    versions = engine.version_summary()

    _log("Engine starting — five-brain trading system")
    _log(f"Brains: {versions}")
    _log(f"Pair: {pair}")
    _log(f"Poll interval: {interval}s")
    _log(f"State dir: {state_dir}")
    _log(f"Config dir: {config_dir}")
    _log(f"LLM enabled: {config.enable_llm} "
         f"(model={config.llm_model}, "
         f"client={'ON' if engine._llm_client else 'off'})")

    # ---- adapter bring-up: only NewsMind has live polling adapters --
    nm = engine.nm
    if nm is not None:
        try:
            nm.build_adapters()
            adapters = getattr(nm, "_adapters", [])
            _log(f"NewsMind adapters loaded: {len(adapters)}")
        except Exception as e:
            _log(f"NewsMind adapters bring-up error: {e}")
    else:
        _log("NewsMind disabled — running brain stack without news polling")

    # ---- morning briefing: print once at start so the deploy log is
    # immediately diagnostic.
    if engine.snb is not None:
        _log("--- SmartNoteBook briefing on boot ---")
        for line in engine.briefing_console_string().splitlines():
            _log(f"  {line}")

    # ---- shutdown handling -----------------------------------------
    stop_flag = {"stop": False}

    def _on_stop(sig, frame):
        stop_flag["stop"] = True
        _log(f"Shutdown signal received ({sig}). Saving state...")

    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)

    # ---- main loop -------------------------------------------------
    cycle = 0
    try:
        while not stop_flag["stop"]:
            now = datetime.now(timezone.utc)

            # 1) NewsMind: poll + ingest live items.
            items: list = []
            events: list = []
            if nm is not None:
                try:
                    items = nm.poll_once(now) or []
                except Exception as e:
                    _log(f"poll error: {e}")
                if items:
                    try:
                        events = nm.ingest_items(items, now_utc=now) or []
                    except Exception as e:
                        _log(f"ingest error: {e}")

            # 2) Engine.step() — composes the 5 brains over the *latest
            # available* state. Bar/bundle are None in this streaming
            # mode; ChartMind/MarketMind will skip without them.
            try:
                decision = engine.step(bar=None, bundle=None, now_utc=now)
                action = decision.action
                if decision.halt_sources:
                    halt = "+".join(decision.halt_sources)
                    line = f"halt[{halt}]: {decision.reason}"
                else:
                    nctx = decision.news_context
                    one = (getattr(nctx, "summary_one_liner", "")
                           if nctx is not None else "")
                    line = f"action={action} | {one}"
            except Exception as e:
                line = f"engine.step error: {e}"

            cost_part = ""
            if engine._llm_cost is not None:
                cost_part = f" | {engine._llm_cost.one_line_summary()}"
            _log(f"items={len(items):>3} events={len(events):>2} | {line}{cost_part}")

            cycle += 1

            # 3) periodic checkpoint.
            if cycle % checkpoint_every == 0:
                if nm is not None:
                    try:
                        nm.save_state()
                    except Exception:
                        pass

            # 4) periodic morning briefing — useful for long-running
            # deploys where the boot-time briefing scrolls off.
            if cycle % briefing_every == 0 and engine.snb is not None:
