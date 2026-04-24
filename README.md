# NewsMind Trading System

Multi-brain trading system for EUR/USD. Runs on a VPS alongside RSSHub.

## Architecture

```
ChartMind   - technical analysis (chart + candles + ICT + indicators)
MarketMind  - cross-asset macro (DXY, RORO, correlations, yields)
NewsMind    - news and narrative brain (releases, headlines, narratives)
Engine.py   - composes all three with halt-first precedence
main.py     - live polling loop (runs inside Docker)
```

## Deploy on Hostinger VPS

1. RSSHub must already be running on the same VPS (via Hostinger
   Docker Manager catalog template). Note its project name (e.g.,
   `rsshub-cs4k`) because `docker-compose.yml` references its
   external network.

2. In Hostinger Docker Manager, create a new Compose project from
   this repository's `docker-compose.yml`.

3. Hostinger builds the NewsMind image from the `Dockerfile` in this
   repo and starts the container. Logs in Hostinger UI show the
   every-60-seconds status line.

## Configuration

Everything is YAML:

- `NewsMind/config/events.yaml` - 70+ scheduled + 18 unscheduled events
- `NewsMind/config/sources.yaml` - 40+ free RSS/JSON sources + 20 Twitter groups
- `NewsMind/config/narratives.yaml` - 11 active macro narratives
- `NewsMind/config/keywords.yaml` - Tier-X keyword dictionaries

Edit YAML, push to GitHub, click Rebuild in Hostinger.

## Security

- Token-based APIs via environment variables (FRED_API_KEY, etc.).
- No credentials in code or config.
- State volume (`newsmind_state`) persists narrative stages + event
  precedents across container restarts.

## Status

See container logs for the live one-liner status every poll cycle.
