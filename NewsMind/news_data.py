# -*- coding: utf-8 -*-
"""Source adapters for NewsMind. RSS/JSON/HTML/RSSHub/Telegram/Reddit."""
from __future__ import annotations
import html
import json
import os
import re
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

from NewsMind.config_loader import SourceDef, load_sources_config


_MAX_TITLE_CHARS = 500
_MAX_BODY_CHARS = 2000


@dataclass
class RawItem:
    source_id: str
    source_tier: str
    category: str
    title: str
    body: str = ""
    url: str = ""
    published_utc: Optional[datetime] = None
    fetched_utc: Optional[datetime] = None
    raw_payload: Optional[dict] = None
    item_id: str = ""

    def __post_init__(self):
        if self.title and len(self.title) > _MAX_TITLE_CHARS:
            self.title = self.title[:_MAX_TITLE_CHARS]
        if self.body and len(self.body) > _MAX_BODY_CHARS:
            self.body = self.body[:_MAX_BODY_CHARS]

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id, "source_tier": self.source_tier,
            "category": self.category, "title": self.title,
            "body": self.body, "url": self.url,
            "published_utc": self.published_utc.isoformat() if self.published_utc else None,
            "fetched_utc": self.fetched_utc.isoformat() if self.fetched_utc else None,
            "item_id": self.item_id,
        }


def http_get(url: str, timeout: float = 10.0,
              headers: Optional[Dict[str, str]] = None) -> bytes:
    req_headers = {
        "User-Agent": "NewsMind/1.0 (research; contact: operator)",
        "Accept": ("application/xml, application/rss+xml, application/json, "
                   "text/html;q=0.8, */*;q=0.1"),
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


class SourceAdapter(Protocol):
    source_id: str
    def poll(self, now_utc: datetime) -> List[RawItem]: ...
    def healthy(self) -> bool: ...


class BaseAdapter:
    def __init__(self, sdef: SourceDef,
                  http_fn: Optional[Callable] = None):
        self.source_def = sdef
        self.source_id = sdef.source_id
        self.http_fn = http_fn or http_get
        self.last_poll_ts: Optional[datetime] = None
        self.last_success_ts: Optional[datetime] = None
        self.failure_streak = 0
        self.total_failures = 0
        self.total_polls = 0
        self.total_items = 0
        self.last_error: Optional[str] = None

    def healthy(self) -> bool:
        if self.failure_streak >= 3:
            return False
        return True

    def due(self, now_utc: datetime) -> bool:
        if self.last_poll_ts is None:
            return True
        elapsed = (now_utc - self.last_poll_ts).total_seconds()
        return elapsed >= self.source_def.update_frequency_seconds

    def poll(self, now_utc: datetime) -> List[RawItem]:
        if not self.source_def.enabled:
            return []
        if not self.due(now_utc):
            return []
        self.last_poll_ts = now_utc
        self.total_polls += 1
        try:
            items = self._do_poll(now_utc)
            for item in items:
                item.fetched_utc = now_utc
                item.source_tier = self.source_def.tier
                item.category = self.source_def.category
            self.failure_streak = 0
            self.last_success_ts = now_utc
            self.total_items += len(items)
            return items
        except Exception as exc:  # noqa: BLE001
            self.failure_streak += 1
            self.total_failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            return []

    def _do_poll(self, now_utc: datetime) -> List[RawItem]:
        raise NotImplementedError


_RSS_ITEM_RE = re.compile(r"<item\b[^>]*>(.*?)</item>", re.IGNORECASE | re.DOTALL)
_ATOM_ENTRY_RE = re.compile(r"<entry\b[^>]*>(.*?)</entry>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _rss_extract(tag: str, block: str) -> str:
    pattern = re.compile(rf"<{tag}\b[^>]*>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    m = pattern.search(block)
    if not m:
        alt = re.compile(rf"<{tag}\b[^>]*\bhref=\"([^\"]+)\"[^>]*/?>", re.IGNORECASE)
        m2 = alt.search(block)
        return m2.group(1).strip() if m2 else ""
    raw = m.group(1)
    raw = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", raw, flags=re.DOTALL)
    raw = _TAG_RE.sub(" ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return html.unescape(raw)


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    try:
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    from email.utils import parsedate_to_datetime
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


class RSSAdapter(BaseAdapter):
    def _do_poll(self, now_utc: datetime) -> List[RawItem]:
        data = self.http_fn(self.source_def.url, 10.0,
                             self.source_def.headers or None)
        text = data.decode("utf-8", errors="replace")
        items: List[RawItem] = []
        blocks = _RSS_ITEM_RE.findall(text)
        is_atom = False
        if not blocks:
            blocks = _ATOM_ENTRY_RE.findall(text)
            is_atom = bool(blocks)
        for block in blocks:
            if is_atom:
                title = _rss_extract("title", block)
                body = _rss_extract("summary", block) or _rss_extract("content", block)
                link = _rss_extract("link", block)
                pub = _rss_extract("published", block) or _rss_extract("updated", block)
            else:
                title = _rss_extract("title", block)
                body = _rss_extract("description", block) or _rss_extract("content:encoded", block)
                link = _rss_extract("link", block)
                pub = _rss_extract("pubDate", block) or _rss_extract("dc:date", block)
            if not title:
                continue
            items.append(RawItem(
                source_id=self.source_id,
                source_tier=self.source_def.tier,
                category=self.source_def.category,
                title=title, body=body, url=link,
                published_utc=_parse_date(pub),
                item_id=link or f"{self.source_id}:{title[:120]}",
            ))
        return items


class JSONAdapter(BaseAdapter):
    def _do_poll(self, now_utc: datetime) -> List[RawItem]:
        data = self.http_fn(self.source_def.url, 10.0,
                             self.source_def.headers or None)
        payload = json.loads(data.decode("utf-8", errors="replace"))
        entries = payload if isinstance(payload, list) else payload.get("data") or []
        items: List[RawItem] = []
        for entry in entries:
            raw = self._map_entry(entry)
            if raw is not None:
                items.append(raw)
        return items

    def _map_entry(self, entry: dict) -> Optional[RawItem]:
        title = entry.get("title") or entry.get("event") or entry.get("name")
        if not title:
            return None
        date = entry.get("date") or entry.get("time") or entry.get("datetime")
        return RawItem(
            source_id=self.source_id,
            source_tier=self.source_def.tier,
            category=self.source_def.category,
            title=str(title),
            body=json.dumps(entry, ensure_ascii=False)[:500],
            url=entry.get("url", ""),
            published_utc=_parse_date(str(date)) if date else None,
            raw_payload=entry,
            item_id=f"{self.source_id}:{title}:{date}",
        )


class ForexFactoryAdapter(JSONAdapter):
    def _map_entry(self, entry: dict) -> Optional[RawItem]:
        title = entry.get("title") or entry.get("event")
        if not title:
            return None
        country = entry.get("country", "")
        date_str = entry.get("date", "")
        impact = entry.get("impact", "")
        return RawItem(
            source_id=self.source_id,
            source_tier=self.source_def.tier,
            category=self.source_def.category,
            title=f"{country}:{title}",
            body=(f"country={country} impact={impact} "
                  f"forecast={entry.get('forecast')} "
                  f"previous={entry.get('previous')} "
                  f"actual={entry.get('actual')}"),
            published_utc=_parse_date(date_str) if date_str else None,
            url=entry.get("url", ""),
            raw_payload=entry,
            item_id=f"{self.source_id}:{country}:{title}:{date_str}",
        )


class HTMLScrapeAdapter(BaseAdapter):
    def _do_poll(self, now_utc: datetime) -> List[RawItem]:
        data = self.http_fn(self.source_def.url, 15.0,
                             self.source_def.headers or None)
        text = data.decode("utf-8", errors="replace")
        cleaned = _TAG_RE.sub(" ", text)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return [RawItem(
            source_id=self.source_id,
            source_tier=self.source_def.tier,
            category=self.source_def.category,
            title=f"{self.source_id}: HTML snapshot",
            body=cleaned[:1000], url=self.source_def.url,
            published_utc=now_utc,
            item_id=f"{self.source_id}:{now_utc.isoformat(timespec='seconds')}",
        )]


class RSSHubTwitterAdapter(BaseAdapter):
    def _do_poll(self, now_utc: datetime) -> List[RawItem]:
        accounts_raw = (self.source_def.headers or {}).get("X-Accounts", "")
        accounts = [a.strip() for a in accounts_raw.split(",") if a.strip()]
        if not accounts:
            accounts = [
                "federalreserve", "ecb", "POTUS", "WhiteHouse",
                "SecYellen", "SecTreasury",
                "realDonaldTrump",
                "DeItaone", "LiveSquawk", "FirstSquawk",
                "Reuters", "BloombergTV", "WSJmarkets", "FT",
                "IMFNews", "OECD",
            ]
        items: List[RawItem] = []
        base = self.source_def.url.rstrip("/")
        for handle in accounts:
            url = f"{base}/{handle}"
            try:
                data = self.http_fn(url, 10.0, None)
                text = data.decode("utf-8", errors="replace")
                blocks = _RSS_ITEM_RE.findall(text)
                for block in blocks[:20]:
                    title = _rss_extract("title", block)
                    if not title:
                        continue
                    link = _rss_extract("link", block)
                    pub = _rss_extract("pubDate", block)
                    items.append(RawItem(
                        source_id=self.source_id,
                        source_tier=self.source_def.tier,
                        category=self.source_def.category,
                        title=f"@{handle}: {title}",
                        body="", url=link,
                        published_utc=_parse_date(pub),
                        item_id=link or f"{handle}:{title[:120]}",
                    ))
            except Exception:
                continue
        return items


class TelegramBotAdapter(BaseAdapter):
    def __init__(self, sdef: SourceDef,
                  http_fn: Optional[Callable] = None):
        super().__init__(sdef, http_fn=http_fn)
        self._queue: List[RawItem] = []

    def push(self, item: RawItem) -> None:
        self._queue.append(item)

    def _do_poll(self, now_utc: datetime) -> List[RawItem]:
        if self.source_def.auth_env_var:
            token = os.environ.get(self.source_def.auth_env_var)
            if not token:
                return []
        drained = list(self._queue)
        self._queue.clear()
        return drained


class RedditAdapter(BaseAdapter):
    def _do_poll(self, now_utc: datetime) -> List[RawItem]:
        if self.source_def.auth_env_var:
            cid = os.environ.get(self.source_def.auth_env_var)
            if not cid:
                return []
        return []


_ADAPTER_MAP = {
    "RSS": RSSAdapter, "JSON": JSONAdapter,
    "HTML": HTMLScrapeAdapter, "API": JSONAdapter,
    "BOT": TelegramBotAdapter, "RSSHUB": RSSHubTwitterAdapter,
}


def build_adapter(sdef: SourceDef,
                   http_fn: Optional[Callable] = None) -> SourceAdapter:
    if sdef.source_id == "ff_calendar":
        return ForexFactoryAdapter(sdef, http_fn=http_fn)
    if sdef.source_id == "reddit_api":
        return RedditAdapter(sdef, http_fn=http_fn)
    cls = _ADAPTER_MAP.get(sdef.access_method.upper(), RSSAdapter)
    return cls(sdef, http_fn=http_fn)


def load_adapters_from_yaml(config_path: Path,
                              http_fn: Optional[Callable] = None
                              ) -> List[SourceAdapter]:
    cfg = load_sources_config(config_path)
    return [build_adapter(s, http_fn=http_fn) for s in cfg.enabled_only()]


def ingest_all(adapters: List[SourceAdapter],
                now_utc: datetime) -> List[RawItem]:
    all_items: List[RawItem] = []
    for a in adapters:
        items = a.poll(now_utc)
        all_items.extend(items)
    return all_items
