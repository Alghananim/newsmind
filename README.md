# NewsMind Trading System

Five-brain trading system for EUR/USD with optional LLM reasoning on
top of every brain. Runs on a VPS alongside RSSHub.

## Architecture

The system is composed of five independently-testable brains plus an
orchestrator. Each brain owns one cognitive responsibility; none of
them imports another brain directly — they meet only inside `Engine`.

```
┌────────────┐   ┌────────────┐   ┌────────────┐
│ ChartMind  │   │ MarketMind │   │  NewsMind  │
│ (TA / SMC) │   │ (DXY/RORO) │   │ (events &  │
│            │   │            │   │ narratives)│
└─────┬──────┘   └─────┬──────┘   └─────┬──────┘
      │                │                │
      │   BrainGrade   │  BrainGrade    │  BrainGrade
      └────────────────┼────────────────┘
                       ▼
                 ┌──────────┐         ┌────────────────┐
                 │ GateMind │ ◀─────▶ │ SmartNoteBook  │
                 │ kill +   │         │ journal +      │
                 │ size +   │         │ post/pre-mortem│
                 │ route    │         │ + lessons +    │
                 └────┬─────┘         │ briefing +     │
                      │               │ memory inject  │
                      ▼               └────────────────┘
              ┌──────────────┐
              │  Engine.py   │  composes everything with
              │  main.py     │  halt-first + memory-first precedence
              └──────────────┘

LLM augmentation (optional, opt-in via OPENAI_API_KEY):
       │
       ▼ each brain's *_llm.py wrapper runs in parallel after
         its mechanical analysis; downgrades only, never upgrades.
         Senior-reviewer LLM sits above the gate pass.
         LLM grader rewrites the post-mortem narrative on close.
```

| Brain | Mechanical role | LLM role |
|---|---|---|
| **ChartMind** | technical analysis (price action, candles, ICT/SMC, indicators); produces a `TradePlan` | reasons over the plan against committed lessons + bias flags + pre-mortem; can downgrade confidence or veto |
| **MarketMind** | cross-asset macro (DXY synthetic, RORO, USD/EUR strength composites); produces `MarketContext` | interprets the macro picture as a trader would (Chandler/Laidi/Soros canon); can recommend halt |
| **NewsMind** | scheduled events + unscheduled headlines + narrative tracking; produces `NewsContext` | reads the *narrative state* (Shiller/Kahneman/Taleb canon); can mark do_not_trade |
| **GateMind** | composes brain grades, runs kill-switches, sizes the trade, routes to broker, ledgers everything | senior-reviewer over the gate pass; can approve as-is, cut size, or reject. Never upgrades. |
| **SmartNoteBook** | institutional memory: journal, post/pre-mortem, pattern mining, bias detection, lesson distillation, daily briefing, per-brain memory injection | LLM grader rewrites the post-mortem skeleton into a journal-quality narrative on every close |

`Engine.py` enforces three precedence rules:

1. **Halt-first** — if NewsMind says blackout, MarketMind says halt,
   or any GateMind kill-switch fires, no trade is taken even with a
   clean ChartMind setup. (Schwager's *Market Wizards* canon: refusing
   trades is the edge.)
2. **Memory-first** — SmartNoteBook is consulted *before* the brains
   form their grades. Each brain receives an injection block (committed
   lessons, recent bias flags, pre-mortem warnings) so yesterday's
   journal evidence becomes today's discipline (Steenbarger).
3. **LLM downgrades only** — when LLM mode is on, the brains' LLM
   wrappers can lower confidence or veto a trade, never upgrade or
   approve a trade the mechanical layer rejected. Asymmetric authority
   is the right design (Carver, *Systematic Trading*).

`main.py` runs the long-lived polling loop inside Docker.

## Deploy on Hostinger VPS

1. **RSSHub must already be running** on the same VPS (via Hostinger
   Docker Manager catalog template). Note its project name (e.g.,
   `rsshub-cs4k`) because `docker-compose.yml` references its
   external network.

2. **Provide secrets** in the Hostinger UI (Settings → Environment
   Variables). At minimum:
   - `OPENAI_API_KEY` — required for LLM thinking. Without it, the
     brains run mechanical-only and the loop logs `LLM enabled: False`.
   Optional:
   - `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` — Telegram fill notifications
   - `TWITTER_AUTH_TOKEN` — Twitter sources via RSSHub

3. In Hostinger Docker Manager, create a new Compose project from
   this repository's `docker-compose.yml`.

4. Hostinger builds the image from the `Dockerfile` and starts the
   container. Logs in the Hostinger UI show:
     - the boot-time SmartNoteBook briefing,
     - a one-line status every poll cycle (`items=… events=… | … |
       cost_today=$X.XX calls=N`),
     - a periodic morning-briefing block (default once per hour).

## Local development

```bash
cp .env.example .env       # then fill in OPENAI_API_KEY
pip install -r requirements.txt
python main.py
```

`.env` is gitignored — your real key never leaves your machine. The
loop reads `OPENAI_API_KEY` from the environment; with it set,
`ENABLE_LLM=auto` (the default) flips LLM mode on automatically.

## Configuration

### YAML (NewsMind)

* `NewsMind/config/events.yaml` — 70+ scheduled + 18 unscheduled events
* `NewsMind/config/sources.yaml` — 40+ free RSS/JSON sources + 20 Twitter groups
* `NewsMind/config/narratives.yaml` — 11 active macro narratives
* `NewsMind/config/keywords.yaml` — Tier-X keyword dictionaries

Edit YAML, push to GitHub, click Rebuild in Hostinger.

### Environment variables (main.py)

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY`           | unset    