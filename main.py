# -*- coding: utf-8 -*-
"""main.py - live polling entry point for the trading brains on VPS.

Runs NewsMind in a continuous polling loop. Every 60 seconds:
  1. Polls all enabled adapters (RSS, JSON, RSSHub).
  2. Feeds raw items into the NewsMind pipeline.
  3. Builds a NewsContext snapshot.
  4. Prints a compact status line to stdout (visible in Docker logs).
  5. Saves narrative state every 5 cycles.

ChartMind and MarketMind are instantiated if available but only
consumed when bar/bundle data is supplied (not in this streaming mode).

Stop via SIGTERM (docker stop); state is checkpointed on exit.
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


def main() -> int:
    from NewsMind import NewsMind

    config_dir = ROOT / "NewsMind" / "config"
    interval = int(os.environ.get("POLL_INTERVAL_SEC", "60"))
    checkpoint_every = int(os.environ.get("CHECKPOINT_EVERY_CYCLES", "5"))

    nm = NewsMind(config_dir=config_dir)
    nm.build_adapters()

    print(f"[{_now_iso()}] NewsMind live loop starting", flush=True)
    print(f"[{_now_iso()}] Adapters loaded: {len(nm._adapters)}", flush=True)
    print(f"[{_now_iso()}] Poll interval: {interval}s", flush=True)
    print(f"[{_now_iso()}] Config dir: {config_dir}", flush=True)

    stop_flag = {"stop": False}

    def _on_stop(sig, frame):
        stop_flag["stop"] = True
        print(f"[{_now_iso()}] Shutdown requested. Saving state...",
              flush=True)

    signal.signal(signal.SIGINT, _on_stop)
    signal.signal(signal.SIGTERM, _on_stop)

    cycle = 0
    try:
        while not stop_flag["stop"]:
            now = datetime.now(timezone.utc)
            try:
                items = nm.poll_once(now)
            except Exception as e:
                print(f"[{_now_iso()}] poll error: {e}", flush=True)
                items = []
            events = []
            if items:
                try:
                    events = nm.ingest_items(items, now_utc=now)
                except Exception as e:
                    print(f"[{_now_iso()}] ingest error: {e}", flush=True)
            try:
                ctx = nm.context_now(now)
                line = ctx.summary_one_liner
            except Exception as e:
                line = f"context error: {e}"
            print(f"[{_now_iso()}] items={len(items):>3} "
                  f"events={len(events):>2} | {line}",
                  flush=True)
            cycle += 1
            if cycle % checkpoint_every == 0:
                try:
                    nm.save_state()
                except Exception:
                    pass
            # Sleep in 1s slices so SIGTERM is responsive.
            for _ in range(interval):
                if stop_flag["stop"]:
                    break
                time.sleep(1)
    finally:
        try:
            nm.close()
        except Exception:
            pass
        print(f"[{_now_iso()}] State saved. Goodbye.", flush=True)
    return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    sys.exit(main())
