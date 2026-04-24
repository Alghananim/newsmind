# -*- coding: utf-8 -*-
"""Narrative Tracker - Shiller narratives + Soros reflexivity stages 1-8."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from NewsMind.config_loader import (
    NarrativesConfig, NarrativeDef, load_narratives_config,
)
from NewsMind.news_data import RawItem


@dataclass
class NarrativeState:
    narrative_id: str
    label: str
    reflexivity_stage: int
    conviction: float
    last_reinforced: Optional[datetime]
    last_undermined: Optional[datetime]
    reinforce_count: int = 0
    undermine_count: int = 0
    eur_usd_bias_when_strong: str = "neutral"

    def to_dict(self) -> dict:
        return {
            "narrative_id": self.narrative_id, "label": self.label,
            "reflexivity_stage": self.reflexivity_stage,
            "conviction": self.conviction,
            "last_reinforced": self.last_reinforced.isoformat() if self.last_reinforced else None,
            "last_undermined": self.last_undermined.isoformat() if self.last_undermined else None,
            "reinforce_count": self.reinforce_count,
            "undermine_count": self.undermine_count,
            "eur_usd_bias_when_strong": self.eur_usd_bias_when_strong,
        }


class NarrativeTracker:
    _REINFORCE_PER_STAGE = 5
    _UNDERMINE_PER_STAGE = 2

    def __init__(self, config: NarrativesConfig,
                  state_path: Optional[Path] = None):
        self.config = config
        self.state_path = state_path
        self._states: Dict[str, NarrativeState] = {}
        self._seed_from_config()
        if state_path and state_path.exists():
            self._load_state()

    @classmethod
    def from_yaml(cls, narratives_yaml: Path,
                    state_path: Optional[Path] = None) -> "NarrativeTracker":
        return cls(load_narratives_config(narratives_yaml), state_path=state_path)

    def _seed_from_config(self) -> None:
        for n in self.config.narratives:
            self._states[n.narrative_id] = NarrativeState(
                narrative_id=n.narrative_id, label=n.label,
                reflexivity_stage=int(n.initial_stage),
                conviction=0.3,
                last_reinforced=None, last_undermined=None,
                reinforce_count=0, undermine_count=0,
                eur_usd_bias_when_strong=n.eur_usd_bias_when_strong,
            )

    def _load_state(self) -> None:
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for key, val in (raw or {}).items():
            if key not in self._states:
                continue
            st = self._states[key]
            st.reflexivity_stage = int(val.get("reflexivity_stage", st.reflexivity_stage))
            st.conviction = float(val.get("conviction", st.conviction))
            st.reinforce_count = int(val.get("reinforce_count", 0))
            st.undermine_count = int(val.get("undermine_count", 0))
            lr = val.get("last_reinforced")
            lu = val.get("last_undermined")
            st.last_reinforced = datetime.fromisoformat(lr) if lr else None
            st.last_undermined = datetime.fromisoformat(lu) if lu else None

    def save_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v.to_dict() for k, v in self._states.items()}
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.state_path)

    def update_on_headline(self, item: RawItem, now_utc: datetime) -> None:
        text = f"{item.title} {item.body}".lower()
        for ndef in self.config.narratives:
            st = self._states.get(ndef.narrative_id)
            if st is None:
                continue
            reinforced = any(kw.lower() in text for kw in ndef.reinforce_keywords)
            undermined = any(kw.lower() in text for kw in ndef.undermine_keywords)
            if reinforced and not undermined:
                st.reinforce_count += 1
                st.last_reinforced = now_utc
                if st.reinforce_count >= self._REINFORCE_PER_STAGE:
                    st.reinforce_count = 0
                    st.reflexivity_stage = min(8, st.reflexivity_stage + 1)
                st.conviction = min(1.0, st.conviction + 0.05)
            elif undermined and not reinforced:
                st.undermine_count += 1
                st.last_undermined = now_utc
                if st.undermine_count >= self._UNDERMINE_PER_STAGE:
                    st.undermine_count = 0
                    st.reflexivity_stage = max(1, st.reflexivity_stage - 1)
                st.conviction = max(0.0, st.conviction - 0.1)

    def decay(self, now_utc: datetime) -> None:
        for ndef in self.config.narratives:
            st = self._states.get(ndef.narrative_id)
            if st is None:
                continue
            ref_ts = st.last_reinforced or st.last_undermined
            if ref_ts is None:
                continue
            age_days = (now_utc - ref_ts).days
            if age_days > ndef.max_age_days:
                st.reflexivity_stage = max(2, st.reflexivity_stage - 1)
                st.conviction = max(0.0, st.conviction - 0.1)

    def states(self) -> List[NarrativeState]:
        return list(self._states.values())

    def dominant(self) -> Optional[NarrativeState]:
        if not self._states:
            return None
        return max(self._states.values(),
                   key=lambda s: s.reflexivity_stage * s.conviction)

    def bias_sum(self) -> float:
        total = 0.0
        total_w = 0.0
        for s in self._states.values():
            w = s.reflexivity_stage * s.conviction
            if s.eur_usd_bias_when_strong == "long":
                total += w
            elif s.eur_usd_bias_when_strong == "short":
                total -= w
            total_w += w
        return total / total_w if total_w else 0.0
