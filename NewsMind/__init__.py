# -*- coding: utf-8 -*-
"""NewsMind v1.0 - news and narrative brain for EUR/USD.

Watches scheduled macro events, unscheduled geopolitical headlines,
and narrative shifts. Emits a disciplined directive every bar:
trade now with what bias and conviction - or stand down.

Sources (authoritative, cite-worthy):
    Kathy Lien - Day Trading the Currency Market
    Robert Shiller - Narrative Economics
    Daniel Kahneman - Thinking, Fast and Slow
    Andrew Lo - Adaptive Markets
    Nassim Taleb - Black Swan / Antifragile
    George Soros - The Alchemy of Finance
    Marc Chandler - Making Sense of the Dollar
    Ashraf Laidi - Currency Trading and Intermarket Analysis
    Robert Carver - Systematic Trading
    Marcos Lopez de Prado - Advances in Financial ML
Academic papers:
    Andersen et al (2003) - Micro Effects of Macro Announcements
    Faust et al (2007) - HF Response of Exchange Rates
    BIS Quarterly Reviews
    ECB Working Papers on announcement effects

All code original Python. No copyrighted material reproduced.

Usage:
    from NewsMind import NewsMind
    nm = NewsMind()
    ctx = nm.context_now()
    print(ctx.summary_one_liner)
"""
__version__ = "1.0.0"

from NewsMind.NewsMind import (   # noqa: F401
    NewsMind, NewsContext,
    EventRecord, NewsSignal, EventWindowState,
    NewsRegimeState, NarrativeState, HaltSignal,
)
from NewsMind.news_data import (   # noqa: F401
    RawItem, SourceAdapter, BaseAdapter,
    RSSAdapter, JSONAdapter, HTMLScrapeAdapter,
    TelegramBotAdapter, RedditAdapter, RSSHubTwitterAdapter,
    load_adapters_from_yaml, ingest_all,
)
from NewsMind.event_calendar import EventCalendar   # noqa: F401
from NewsMind.event_classifier import (   # noqa: F401
    classify_raw_item, classify_scheduled, classify_unscheduled,
)
from NewsMind.surprise_engine import (   # noqa: F401
    compute_surprise_z, apply_asymmetry, direction_from_z,
)
from NewsMind.headline_scanner import (   # noqa: F401
    HeadlineScanner, KeywordConfig,
)
from NewsMind.event_windows import compute_window_state   # noqa: F401
from NewsMind.precedent_engine import (   # noqa: F401
    PrecedentEngine, PrecedentResult,
)
from NewsMind.narrative_tracker import NarrativeTracker   # noqa: F401
from NewsMind.news_regime import classify_regime   # noqa: F401
from NewsMind.channel_router import (   # noqa: F401
    route_event_to_channel, ChannelImpact,
)
from NewsMind.conviction import (   # noqa: F401
    compute_conviction, COTSnapshot,
)
from NewsMind.liquidity_session import (   # noqa: F401
    session_from_utc, liquidity_discount,
)
from NewsMind.news_narrative import build_narrative, one_liner   # noqa: F401
from NewsMind.integration import (   # noqa: F401
    make_news_factor, make_news_conflict,
    make_news_challenge, make_news_halt,
)
from NewsMind.config_loader import (   # noqa: F401
    load_events_config, load_sources_config,
    load_narratives_config, load_keywords_config,
    EventsConfig, SourcesConfig, NarrativesConfig, KeywordsConfig,
)
