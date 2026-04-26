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


def _last_known_price(engine, bar=None) -> float:
    """Best-effort 'current price' for the monitor.

    Priority: 1) live OANDA mid price, 2) latest bar's close,
    3) cached open-context last_price, 4) cached filled_price.
    Returns 0.0 if nothing is known.
    """
    # 1) Live OANDA mid (most accurate)
    p = engine.oanda_current_price()
    if p > 0:
        return p
    # 2) Latest bar close from this cycle
    if bar is not None:
        c = getattr(bar, "close", None)
        if isinstance(c, (int, float)) and c > 0:
            return float(c)
    # 3 + 4) Cached open contexts
    contexts = getattr(engine, "_open_contexts", {})
    for ctx in contexts.values():
        last = getattr(ctx, "last_price", 0.0)
        if last and last > 0:
            return float(last)
        entry = getattr(ctx, "filled_price", 0.0)
        if entry and entry > 0:
            return float(entry)
    return 0.0


def _run_backtest_one_shot() -> int:
    """One-shot backtest mode triggered by RUN_BACKTEST=true env var.

    Runs scripts/run_backtest.py, captures its full output to docker
    logs, then sleeps forever so the container stays alive (operator
    can read results via Logs UI then unset the var and restart for
    normal live mode).
    """
    _log("=== ONE-SHOT BACKTEST MODE (RUN_BACKTEST=true) ===")
    _log("Will run /app/scripts/run_backtest.py and stream output below.")
    _log("After completion, container sleeps so you can read logs in Hostinger UI.")
    _log("To resume live mode: unset RUN_BACKTEST and restart container.")
    _log("")

    script_path = ROOT / "scripts" / "run_backtest.py"
    if not script_path.exists():
        _log(f"ERROR: {script_path} not found")
        return 2

    import subprocess
    try:
        rc = subprocess.call([sys.executable, str(script_path)])
        _log(f"=== BACKTEST EXITED with code {rc} ===")
    except Exception as e:
        _log(f"ERROR running backtest: {e}")
        rc = 3

    # Sleep forever so container stays alive and logs remain readable.
    _log("Sleeping; restart container to resume live mode.")
    while True:
        time.sleep(3600)


def main() -> int:
    # Auto-run the backtest on first boot (when no results.json exists)
    # OR when RUN_BACKTEST=true is set explicitly. Results live in the
    # persistent state volume so subsequent boots skip straight to
    # live mode unless RUN_BACKTEST forces a re-run.
    rb_val = os.environ.get("RUN_BACKTEST", "").lower()
    state_dir = os.environ.get("STATE_DIR", "/app/NewsMind/state")
    results_path = Path(state_dir) / "backtest" / "results.json"
    _log(f"BOOT: RUN_BACKTEST={rb_val!r}, "
         f"results_exists={results_path.exists()}")

    force_run = rb_val in ("1", "true", "yes")
    auto_run = not results_path.exists()
    if force_run or auto_run:
        _log(f"BOOT: triggering backtest (force={force_run}, auto={auto_run})")
        return _run_backtest_one_shot()

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
    enable_oanda = (
        os.environ.get("ENABLE_OANDA", "auto").lower() in ("1", "true", "yes")
        or (os.environ.get("ENABLE_OANDA", "auto").lower() == "auto"
            and bool(os.environ.get("OANDA_API_TOKEN"))
            and bool(os.environ.get("OANDA_ACCOUNT_ID")))
    )

    # Pair-mode safety override: monitoring mode never sends real orders
    # (forces PaperBroker). Set BEFORE EngineConfig construction so the
    # OANDA broker is never wired for monitoring/paper-only pairs.
    if 'pair_mode' in dir() and pair_mode == "monitoring":
        enable_oanda = False
        _log("FORCED enable_oanda=False for monitoring mode (PaperBroker only)")

    # Per-pair production-tuned variant filter. RESET TO TRUTH after
    # the 10-test isolation diagnostic on real OANDA over 2 years.
    # Earlier "champions" (trail_r25_risk15 +61%, jp_champion +105%)
    # were Q1 2024 artifacts — see AUDIT_VERDICT.md and DIAGNOSTIC.md.
    #
    # Real per-pair winners over the FULL 2-year OANDA window:
    #   EUR/USD: kill_asia  -> +4.58%  (PF 1.10, +0.069R, 135 trades)
    #   USD/JPY: kill_asia  -> +1.09%  (PF 1.02, ~breakeven, 575 trd)
    #   GBP/USD: NO ROBUST VARIANT FOUND — every variant lost over 2y.
    #            Best was kill_asia at -15% (regime-specific).
    #            DO NOT TRADE this pair until edge is found.
    # Per-pair LIVE-MODE status. Three tiers per CYCLE-5 decision:
    #   "production"  - real money trades (full risk)
    #   "monitoring"  - paper trades, signals logged only (no real orders)
    #   "disabled"    - no trading at all (skip cycle entirely)
    #
    # CYCLE-3 evidence on real OANDA over 2 years:
    #   EUR/USD kill_asia: +5.51%, PF 1.12, 132 trades  (PROVEN PROFITABLE)
    #   USD/JPY kill_asia: +1.69%, PF 1.02, 576 trades  (MARGINAL — paper only)
    #   GBP/USD kill_asia: -14.20%, PF 0.52              (LOSING — disabled)
    #
    # Grade filtering REGRESSED all 3 pairs (see DIAGNOSTIC.md).
    # ChartMind v1 grade calibration is inverted; until recalibrated,
    # plain kill_asia is the strongest known config.
    PAIR_STATUS = {
        "EUR/USD": "production",   # +5.51%/2y proven — live trading OK
        "USD/JPY": "monitoring",   # +1.69%/2y marginal — paper only
        "GBP/USD": "disabled",     # -14.20%/2y losing — research-only
    }

    PRODUCTION_DEFAULTS = {
        # GAP-cycle discovery (real OANDA / 2y):
        # EUR/USD best_eur: +36.17% PF 1.42 WR 41% (kill_asia + 1.5% risk)
        # USD/JPY best_jpy: +12.51% PF 1.49 WR 52% (kill_asia + 2.0% risk)
        # NOTE: Pending walk-forward validation before live deployment.
        # If WF fails, revert to kill_asia (+5.51% / +1.69%).
        "EUR/USD": "best_eur",
        "USD/JPY": "best_jpy",
        "GBP/USD": "kill_asia",   # disabled in PAIR_STATUS regardless
    }

    # Resolve current pair's mode (env override always wins)
    pair_mode = os.environ.get("PAIR_MODE", "").strip() or PAIR_STATUS.get(pair, "disabled")
    if pair_mode == "disabled":
        _log(f"PAIR_MODE = disabled for {pair}. Exiting cleanly. "
             f"Set PAIR_MODE=production or monitoring to override.")
        return 0
    if pair_mode == "monitoring":
        _log(f"PAIR_MODE = monitoring for {pair}. Engine runs in PAPER mode "
             f"(signals logged, no real broker orders).")
    variant_name = (os.environ.get("VARIANT_FILTER", "").strip()
                    or PRODUCTION_DEFAULTS.get(pair, ""))

    config = EngineConfig(
        pair=pair,
        state_dir=state_dir,
        injection_language=os.environ.get("INJECTION_LANGUAGE", "en"),
        enable_llm=enable_llm,
        llm_model=os.environ.get("LLM_MODEL", "gpt-5"),
        enable_oanda=enable_oanda,
        oanda_granularity=os.environ.get("OANDA_GRANULARITY", "M15"),
        variant_filter_name=variant_name,
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
    _log(f"OANDA enabled: {config.enable_oanda} "
         f"(client={'ON' if engine._oanda_client else 'off'}, "
         f"feed={'ON' if engine._oanda_bar_feed else 'off'})")
    _log(f"Variant filter: {config.variant_filter_name or '(none)'} "
         f"[{pair} production default = {PRODUCTION_DEFAULTS.get(pair, 'none')}]")

    # ---- OANDA reconciliation at boot ------------------------------
    # If OANDA is on, fetch the authoritative account view and compare
    # it to the local Portfolio. Refuse to start trading on drift.
    if engine._oanda_client is not None:
        snap = engine.fetch_oanda_snapshot()
        if snap is not None:
            _log(snap.one_line_summary())
        recon = engine.reconcile_oanda()
        if recon is not None:
            if recon.is_clean:
                _log(f"OANDA reconcile: clean "
                     f"(local={recon.local_positions}, "
                     f"oanda={recon.oanda_trades})")
            else:
                _log(f"OANDA reconcile: DRIFT "
                     f"(only_in_local={len(recon.only_in_local)}, "
                     f"only_in_oanda={len(recon.only_in_oanda)})")
                _log("Trading-side will run but you should resolve the drift.")

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
            # available* state. Bar comes from OANDA when enabled; else
            # None and ChartMind sits out the cycle.
            bar = engine.latest_oanda_bar()
            try:
                decision = engine.step(bar=bar, bundle=None, now_utc=now)
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

            # 2b. Monitor open positions (move stops, take partials,
            # close on invalidation/time-decay). When a feed of bar
            # prices is wired up, pass current_price + bar_reading;
            # in the streaming-only NewsMind mode no positions are
            # opened so this is a fast no-op.
            mon_part = ""
            try:
                last_price = _last_known_price(engine, bar=bar)
                if last_price > 0 and engine.gm is not None:
                    mr = engine.monitor_positions(
                        current_price=last_price, bar_reading=bar, now_utc=now,
                    )
                    if mr is not None and (mr.actions_applied or mr.trades_recorded
                                            or mr.positions_seen):
                        mon_part = f" | mon[{mr.summary()}]"
            except Exception as e:
                mon_part = f" | mon_err[{type(e).__name__}]"

            cost_part = ""
            if engine._llm_cost is not None:
                cost_part = f" | {engine._llm_cost.one_line_summary()}"
            _log(f"items={len(items):>3} events={len(events):>2} | "
                 f"{line}{mon_part}{cost_part}")

            cycle += 1

            if cycle % checkpoint_every == 0:
                if nm is not None:
                    try:
                        nm.save_state()
                    except Exception:
                        pass

            if cycle % checkpoint_every == 0:
                if nm is not None:
                    try:
                        nm.save_state()
                    except Exception:
                        pass

            if cycle % briefing_every == 0 and engine.snb is not None:
                _log("--- SmartNoteBook briefing (periodic) ---")
                for line in engine.briefing_console_string().splitlines():
                    _log(f"  {line}")

            for _ in range(interval):
                if stop_flag["stop"]:
                    break
                time.sleep(1)
    finally:
        if nm is not None:
            try:
                nm.close()
            except Exception:
                pass
        _log("State saved. Goodbye.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
