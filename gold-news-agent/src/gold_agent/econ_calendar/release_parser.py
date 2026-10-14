"""Fast path (7.5): turn data-release headlines into numbers, surprise and a playbook bucket.

Formats seen on squawk feeds, e.g.
  "US CPI MoM 0.4% vs 0.3% exp; prior 0.2%"
  "US Initial Jobless Claims Actual 227K (Forecast 230K, Previous 233K)"
  "U.S. Nonfarm Payrolls 254K vs. Est. 150K (Prev. 159K, Rev. 142K)"
No LLM involved: this must stay well under a second.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from gold_agent.econ_calendar.events import EventCatalog, EventSpec, parse_number, to_unit
from gold_agent.models import CalendarEvent, ReleaseReading

Bucket = Literal["strong_bearish", "mild_bearish", "neutral", "mild_bullish", "strong_bullish"]

_NON_US = re.compile(
    r"\b(uk|u\.k\.|britain|british|gbp|euro ?zone|euro area|eur|ecb|german[y]?|france|french|"
    r"ital(?:y|ian)|spain|spanish|canad(?:a|ian)|cad|japan(?:ese)?|jpy|china|chinese|cny|"
    r"australia(?:n)?|aud|new zealand|nzd|swiss|switzerland|chf|sweden|swedish|norway|india|"
    r"mexico|brazil|korea|singapore|indonesia|turkey|russia|south africa)\b",
    re.I,
)
_US = re.compile(r"(?<![a-z])(us|u\.s\.|usd|united states|american?)(?![a-z])", re.I)
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:am|pm|et|gmt|utc)?\b", re.I)
_NUMBER = re.compile(
    r"(?<![\w.])(?P<num>[-+]?\d+(?:[.,]\d+)*)\s*(?P<unit>%|bps?|[kmb](?![a-z]))?", re.I
)
_FORECAST = re.compile(
    r"\b(exp(?:ected|ectation|ect)?|est(?:imate[ds]?)?|f(?:or)?cast|forecast|f'cast|consensus|"
    r"cons|survey|median|poll)\b\.?",
    re.I,
)
_PREVIOUS = re.compile(r"\b(prior|prev(?:ious)?|last|was)\b\.?", re.I)
_REVISED = re.compile(r"\b(rev(?:ised)?|revision)\b\.?", re.I)
_ACTUAL = re.compile(r"\b(actual|act)\b\.?", re.I)
_VS = re.compile(r"\bvs\.?\s*$|\bversus\s*$", re.I)


@dataclass(frozen=True)
class _Number:
    value: float
    unit: str
    start: int
    end: int


def bucket_for(hawkish_z: float) -> Bucket:
    if hawkish_z >= 1.5:
        return "strong_bearish"
    if hawkish_z >= 0.5:
        return "mild_bearish"
    if hawkish_z > -0.5:
        return "neutral"
    if hawkish_z > -1.5:
        return "mild_bullish"
    return "strong_bullish"


def direction_for(bucket: str) -> Literal["bullish", "bearish", "neutral"]:
    if bucket.endswith("bearish"):
        return "bearish"
    if bucket.endswith("bullish"):
        return "bullish"
    return "neutral"


def _numbers(text: str) -> list[_Number]:
    found: list[_Number] = []
    for match in _NUMBER.finditer(text):
        parsed = parse_number(match.group(0))
        if parsed is None:
            continue
        value, unit = parsed
        raw_unit = (match.group("unit") or "").lower()
        if raw_unit.startswith("bp"):
            unit = "bp"
        # Plain years ("Sep 2026") and quarter labels are not data points.
        if not unit and 1990 <= value <= 2100 and float(value).is_integer():
            continue
        found.append(_Number(value, unit, match.start(), match.end()))
    return found


def _role(text: str, number: _Number, previous_end: int) -> str | None:
    before = text[previous_end : number.start]
    after = text[number.end : number.end + 14]
    if _REVISED.search(before):
        return "revised"
    if _PREVIOUS.search(before):
        return "previous"
    if _FORECAST.search(before):
        return "forecast"
    if _ACTUAL.search(before):
        return "actual"
    # "0.3% exp" labels the number before it; "0.4%, EST. 0.3%" labels the next number instead.
    postfix = (
        r"\s*(?:[,;)]\s*)?(exp(?:ected)?|est(?:imate[ds]?)?|consensus|forecast)\b(?!\.?\s*[-+]?\d)"
    )
    if re.match(postfix, after, re.I):
        return "forecast"
    if _VS.search(before.rstrip(" (")):
        return "vs"
    return None


def _in_unit(number: _Number, spec: EventSpec) -> float:
    if number.unit in ("", "bp") and spec.unit in ("K", "M", "B") and abs(number.value) >= 1000:
        return to_unit(number.value, "", spec.unit)  # raw count, e.g. 254,000 -> 254K
    if number.unit in ("K", "M", "B") and spec.unit in ("K", "M", "B"):
        return to_unit(number.value, number.unit, spec.unit)
    return number.value


StdProvider = Callable[[EventSpec], tuple[float, Literal["calibrated", "prior"]]]


def prior_std(spec: EventSpec) -> tuple[float, Literal["calibrated", "prior"]]:
    return spec.surprise_std, "prior"


class ReleaseParser:
    def __init__(self, catalog: EventCatalog, *, window_minutes: int = 30) -> None:
        self._catalog = catalog
        self._window = timedelta(minutes=window_minutes)

    def parse(
        self,
        headline: str,
        *,
        now: datetime,
        first_seen_at: datetime,
        calendar: Sequence[CalendarEvent] = (),
        cluster_id: str | None = None,
        source_id: str | None = None,
        std_provider: StdProvider = prior_std,
    ) -> ReleaseReading | None:
        text = _CLOCK.sub(" ", headline.replace("−", "-").replace("–", "-"))
        match = self._catalog.match_text(text)
        if match is None:
            return None
        spec, alias_end = match
        head = text[:alias_end]
        if _NON_US.search(head):
            return None
        scheduled = next(
            (
                event
                for event in calendar
                if event.event_key == spec.key and abs(event.scheduled_at - now) <= self._window
            ),
            None,
        )
        if not _US.search(head) and scheduled is None:
            return None  # no country marker and nothing scheduled: too risky to assume US data

        tail = text[alias_end:]
        roles: dict[str, float] = {}
        previous_end = 0
        for number in _numbers(tail):
            role = _role(tail, number, previous_end) or (
                "actual" if "actual" not in roles else None
            )
            previous_end = number.end
            if role == "vs":
                role = "forecast" if "actual" in roles else "actual"
            if role is None or role in roles:
                continue
            roles[role] = _in_unit(number, spec)
        if "actual" not in roles:
            return None

        forecast = roles.get("forecast")
        forecast_source: Literal["headline", "calendar", "none"] = "headline"
        if forecast is None and scheduled is not None and scheduled.forecast:
            parsed = parse_number(scheduled.forecast)
            if parsed is not None:
                value, unit = parsed
                forecast = to_unit(value, unit, spec.unit) if unit in ("K", "M", "B") else value
                forecast_source = "calendar"
        if forecast is None:
            forecast_source = "none"

        reading = ReleaseReading(
            event_key=spec.key,
            event_name=spec.name,
            headline=headline,
            cluster_id=cluster_id,
            source_id=source_id,
            actual=roles["actual"],
            forecast=forecast,
            previous=roles.get("previous"),
            previous_revised=roles.get("revised"),
            forecast_source=forecast_source,
            unit=spec.unit,
            first_seen_at=first_seen_at,
            parsed_at=now,
        )
        if forecast is None:
            return reading
        std, basis = std_provider(spec)
        surprise = roles["actual"] - forecast
        z = surprise / std
        hawkish_z = z if spec.higher_is == "hawkish" else -z
        bucket = bucket_for(hawkish_z)
        return reading.model_copy(
            update={
                "surprise": round(surprise, 6),
                "z": round(z, 3),
                "hawkish_z": round(hawkish_z, 3),
                "z_basis": basis,
                "bucket": bucket,
                "direction_gold": direction_for(bucket),
            }
        )
