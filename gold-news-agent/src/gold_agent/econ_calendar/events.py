"""Event specs (config/events.yaml) and value parsing for calendar strings like '150K' or '0.3%'."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from gold_agent.config import CONFIG_DIR

_VALUE = re.compile(r"(?P<num>[-+−]?\d+(?:[.,]\d+)*)\s*(?P<unit>%|[kmbt])?", re.I)
_UNIT_SCALE = {"": 1.0, "K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


class EventSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    name: str
    unit: Literal["%", "K", "M", "B", ""]
    higher_is: Literal["hawkish", "dovish"]
    surprise_std: float = Field(gt=0)
    aliases: tuple[str, ...]
    exclude: tuple[str, ...] = ()
    ff_titles: tuple[str, ...] = ()


class EventCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    events: tuple[EventSpec, ...]

    def by_key(self, key: str) -> EventSpec | None:
        return next((e for e in self.events if e.key == key), None)

    def match_ff_title(self, title: str, country: str) -> EventSpec | None:
        if country.upper() != "USD":
            return None
        clean = title.strip().lower()
        return next((e for e in self.events if clean in (t.lower() for t in e.ff_titles)), None)

    def match_text(self, text: str) -> tuple[EventSpec, int] | None:
        """Best alias hit in a headline -> (spec, end index of the alias). Longest alias wins."""
        lowered = text.lower()
        best: tuple[EventSpec, int, int] | None = None
        for spec in self.events:
            if any(word in lowered for word in spec.exclude):
                continue
            for alias in spec.aliases:
                pattern = r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])"
                match = re.search(pattern, lowered)
                if match and (best is None or len(alias) > best[2]):
                    best = (spec, match.end(), len(alias))
        return (best[0], best[1]) if best else None


def load_events(path: Path = CONFIG_DIR / "events.yaml") -> EventCatalog:
    with path.open(encoding="utf-8") as handle:
        data: Any = yaml.safe_load(handle)
    return EventCatalog.model_validate(data)


def parse_number(text: str) -> tuple[float, str] | None:
    """'150K' -> (150.0, 'K'); '-0.1%' -> (-0.1, '%'); '1,234' -> (1234.0, ''); None if absent."""
    match = _VALUE.search(text.replace("−", "-"))
    if not match:
        return None
    raw = match.group("num").replace("−", "-")
    if raw.count(",") and not raw.count("."):
        parts = raw.split(",")
        raw = raw.replace(",", "") if all(len(p) == 3 for p in parts[1:]) else raw.replace(",", ".")
    else:
        raw = raw.replace(",", "")
    unit = (match.group("unit") or "").upper()
    if unit == "%":
        return float(raw), "%"
    return float(raw), unit


def to_unit(value: float, unit: str, target: str) -> float:
    """Convert between K/M/B scales; '%' and unit-less values pass through unchanged."""
    if unit == target or unit == "%" or target == "%":
        return value
    return value * _UNIT_SCALE.get(unit, 1.0) / _UNIT_SCALE.get(target, 1.0)
