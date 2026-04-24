# -*- coding: utf-8 -*-
"""Configuration loaders for NewsMind. Hot-reloadable YAML configs.
All configuration is YAML-driven. Four files:
    events.yaml, sources.yaml, narratives.yaml, keywords.yaml
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


def _read_yaml(path: Path) -> Any:
    """Prefer PyYAML; fall back to tiny subset parser."""
    try:
        import yaml  # type: ignore
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except ImportError:
        return _tiny_yaml_parse(path)


def _tiny_yaml_parse(path: Path) -> Any:
    """Minimal YAML subset parser (mappings, sequences, scalars)."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    tokens: List[tuple] = []
    for raw in lines:
        line = _strip_comment(raw)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        tokens.append((indent, line.strip()))

    pos = [0]

    def _val(s: str) -> Any:
        s = s.strip()
        if s == "" or s == "~" or s.lower() == "null":
            return None
        if s.lower() == "true":
            return True
        if s.lower() == "false":
            return False
        if (s.startswith('"') and s.endswith('"')) or \
           (s.startswith("'") and s.endswith("'")):
            return s[1:-1]
        try:
            if "." in s or "e" in s.lower():
                return float(s)
            return int(s)
        except ValueError:
            return s

    def _block(min_indent: int) -> Any:
        if pos[0] >= len(tokens):
            return None
        _, first = tokens[pos[0]]
        if first.startswith("- "):
            return _seq(min_indent)
        return _map(min_indent)

    def _map(min_indent: int) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        while pos[0] < len(tokens):
            indent, line = tokens[pos[0]]
            if indent != min_indent or ":" not in line:
                break
            key, _, rest = line.partition(":")
            key = key.strip()
            rest = rest.strip()
            pos[0] += 1
            if rest == "":
                if pos[0] < len(tokens) and tokens[pos[0]][0] > min_indent:
                    out[key] = _block(tokens[pos[0]][0])
                else:
                    out[key] = None
            else:
                out[key] = _val(rest)
        return out

    def _seq(min_indent: int) -> List[Any]:
        items: List[Any] = []
        while pos[0] < len(tokens):
            indent, line = tokens[pos[0]]
            if indent != min_indent or not line.startswith("- "):
                break
            rest = line[2:].strip()
            pos[0] += 1
            if rest == "":
                if pos[0] < len(tokens) and tokens[pos[0]][0] > min_indent:
                    items.append(_block(tokens[pos[0]][0]))
                else:
                    items.append(None)
            elif ":" in rest:
                key, _, val = rest.partition(":")
                sub: Dict[str, Any] = {}
                if val.strip() != "":
                    sub[key.strip()] = _val(val)
                if pos[0] < len(tokens) and tokens[pos[0]][0] > min_indent:
                    more = _map(tokens[pos[0]][0])
                    sub.update(more)
                items.append(sub)
            else:
                items.append(_val(rest))
        return items

    if not tokens:
        return None
    return _block(tokens[0][0])


def _strip_comment(line: str) -> str:
    out = []
    in_sq = in_dq = False
    for ch in line:
        if ch == "'" and not in_dq:
            in_sq = not in_sq
        elif ch == '"' and not in_sq:
            in_dq = not in_dq
        elif ch == "#" and not in_sq and not in_dq:
            break
        out.append(ch)
    return "".join(out)


# ---- events.yaml ----
@dataclass
class EventDef:
    id: str
    label: str
    country: str
    currency: str
    tier: int
    channel: str
    pip_per_sigma: float
    decay_hours: float
    halt_minus_min: int
    halt_plus_min: int
    direction_rule: str
    source_primary: Optional[str] = None
    source_fallback: Optional[str] = None
    min_sigma_halt: float = 0.5
    expected_range: Optional[List[float]] = None
    widen_stops_multiplier: float = 2.0


@dataclass
class UnscheduledEventDef:
    id: str
    label: str
    trigger: str
    keywords: List[str]
    exclude_keywords: List[str] = field(default_factory=list)
    and_keywords: List[List[str]] = field(default_factory=list)
    min_sources: int = 2
    source_tiers: List[str] = field(default_factory=lambda: ["S", "A"])
    tier: int = 1
    channel: str = "safe_haven"
    default_direction: str = "usd_bullish_risk_off"
    default_pip: float = 100.0


@dataclass
class EventsConfig:
    scheduled: List[EventDef] = field(default_factory=list)
    unscheduled: List[UnscheduledEventDef] = field(default_factory=list)

    def by_id(self, eid: str) -> Optional[EventDef]:
        return next((e for e in self.scheduled if e.id == eid), None)

    def unscheduled_by_id(self, eid: str) -> Optional[UnscheduledEventDef]:
        return next((e for e in self.unscheduled if e.id == eid), None)


def load_events_config(path: Path) -> EventsConfig:
    raw = _read_yaml(path) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"events.yaml root must be a mapping")
    out = EventsConfig()
    for e in raw.get("scheduled", []) or []:
        if not isinstance(e, dict):
            continue
        _require(e, ["id", "label", "country", "currency", "tier", "channel",
                     "pip_per_sigma", "decay_hours", "halt_minus_min",
                     "halt_plus_min", "direction_rule"], path)
        out.scheduled.append(EventDef(
            id=str(e["id"]), label=str(e["label"]),
            country=str(e["country"]), currency=str(e["currency"]),
            tier=int(e["tier"]), channel=str(e["channel"]),
            pip_per_sigma=float(e["pip_per_sigma"]),
            decay_hours=float(e["decay_hours"]),
            halt_minus_min=int(e["halt_minus_min"]),
            halt_plus_min=int(e["halt_plus_min"]),
            direction_rule=str(e["direction_rule"]),
            source_primary=e.get("source_primary"),
            source_fallback=e.get("source_fallback"),
            min_sigma_halt=float(e.get("min_sigma_halt", 0.5)),
            expected_range=e.get("expected_range"),
            widen_stops_multiplier=float(e.get("widen_stops_multiplier", 2.0)),
        ))
    for e in raw.get("unscheduled", []) or []:
        if not isinstance(e, dict):
            continue
        _require(e, ["id", "label", "trigger", "keywords"], path)
        and_raw = e.get("and_keywords") or []
        and_kw: List[List[str]] = []
        for g in and_raw:
            if isinstance(g, list):
                and_kw.append([str(w) for w in g])
        out.unscheduled.append(UnscheduledEventDef(
            id=str(e["id"]), label=str(e["label"]),
            trigger=str(e["trigger"]),
            keywords=list(e["keywords"] or []),
            exclude_keywords=list(e.get("exclude_keywords") or []),
            and_keywords=and_kw,
            min_sources=int(e.get("min_sources", 2)),
            source_tiers=list(e.get("source_tiers") or ["S", "A"]),
            tier=int(e.get("tier", 1)),
            channel=str(e.get("channel", "safe_haven")),
            default_direction=str(e.get("default_direction", "usd_bullish_risk_off")),
            default_pip=float(e.get("default_pip", 100.0)),
        ))
    return out


# ---- sources.yaml ----
@dataclass
class SourceDef:
    source_id: str
    tier: str
    category: str
    access_method: str
    url: str
    update_frequency_seconds: int = 300
    reliability_score: float = 8.0
    legal_notes: str = ""
    fallback_source: Optional[str] = None
    enabled: bool = True
    headers: Dict[str, str] = field(default_factory=dict)
    auth_env_var: Optional[str] = None


@dataclass
class SourcesConfig:
    sources: List[SourceDef] = field(default_factory=list)

    def enabled_only(self) -> List[SourceDef]:
        return [s for s in self.sources if s.enabled]

    def by_id(self, sid: str) -> Optional[SourceDef]:
        return next((s for s in self.sources if s.source_id == sid), None)


def load_sources_config(path: Path) -> SourcesConfig:
    raw = _read_yaml(path) or {}
    if not isinstance(raw, dict):
        raise ValueError("sources.yaml root must be a mapping")
    out = SourcesConfig()
    for e in raw.get("sources", []) or []:
        if not isinstance(e, dict):
            continue
        _require(e, ["source_id", "tier", "category", "access_method", "url"], path)
        out.sources.append(SourceDef(
            source_id=str(e["source_id"]), tier=str(e["tier"]),
            category=str(e["category"]),
            access_method=str(e["access_method"]), url=str(e["url"]),
            update_frequency_seconds=int(e.get("update_frequency_seconds", 300)),
            reliability_score=float(e.get("reliability_score", 8.0)),
            legal_notes=str(e.get("legal_notes", "")),
            fallback_source=e.get("fallback_source"),
            enabled=bool(e.get("enabled", True)),
            headers=dict(e.get("headers") or {}),
            auth_env_var=e.get("auth_env_var"),
        ))
    return out


# ---- narratives.yaml ----
@dataclass
class NarrativeDef:
    narrative_id: str
    label: str
    description: str = ""
    initial_stage: int = 2
    max_age_days: int = 180
    reinforce_keywords: List[str] = field(default_factory=list)
    undermine_keywords: List[str] = field(default_factory=list)
    eur_usd_bias_when_strong: str = "neutral"


@dataclass
class NarrativesConfig:
    narratives: List[NarrativeDef] = field(default_factory=list)

    def by_id(self, nid: str) -> Optional[NarrativeDef]:
        return next((n for n in self.narratives if n.narrative_id == nid), None)


def load_narratives_config(path: Path) -> NarrativesConfig:
    raw = _read_yaml(path) or {}
    if not isinstance(raw, dict):
        raise ValueError("narratives.yaml root must be a mapping")
    out = NarrativesConfig()
    for e in raw.get("narratives", []) or []:
        if not isinstance(e, dict):
            continue
        _require(e, ["narrative_id", "label"], path)
        out.narratives.append(NarrativeDef(
            narrative_id=str(e["narrative_id"]), label=str(e["label"]),
            description=str(e.get("description", "")),
            initial_stage=int(e.get("initial_stage", 2)),
            max_age_days=int(e.get("max_age_days", 180)),
            reinforce_keywords=list(e.get("reinforce_keywords") or []),
            undermine_keywords=list(e.get("undermine_keywords") or []),
            eur_usd_bias_when_strong=str(e.get("eur_usd_bias_when_strong", "neutral")),
        ))
    return out


# ---- keywords.yaml ----
@dataclass
class KeywordsConfig:
    categories: Dict[str, List[str]] = field(default_factory=dict)
    exclude: Dict[str, List[str]] = field(default_factory=dict)


def load_keywords_config(path: Path) -> KeywordsConfig:
    raw = _read_yaml(path) or {}
    if not isinstance(raw, dict):
        raise ValueError("keywords.yaml root must be a mapping")
    out = KeywordsConfig()
    for cat, kws in (raw.get("categories") or {}).items():
        out.categories[str(cat)] = [str(k) for k in (kws or [])]
    for cat, kws in (raw.get("exclude") or {}).items():
        out.exclude[str(cat)] = [str(k) for k in (kws or [])]
    return out


def _require(entry: dict, keys: List[str], path: Path) -> None:
    missing = [k for k in keys if k not in entry]
    if missing:
        raise ValueError(
            f"{path.name}: missing required keys {missing} in entry {entry!r}"
        )
