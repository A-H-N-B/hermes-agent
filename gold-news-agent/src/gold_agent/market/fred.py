"""US yields from FRED (daily, ~1 day behind): context only, labelled as such."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import httpx

from gold_agent.config import Source, expand_url


@dataclass(frozen=True)
class SeriesReading:
    series_id: str
    as_of: date
    value: float
    change_1d: float | None


def parse_observations(series_id: str, content: bytes) -> SeriesReading | None:
    """FRED JSON (newest first, '.' = missing) -> latest value and change vs previous value."""
    data = json.loads(content)
    values: list[tuple[date, float]] = []
    for row in data.get("observations", []):
        raw = row.get("value")
        if raw in (None, "", "."):
            continue
        values.append((date.fromisoformat(row["date"]), float(raw)))
    if not values:
        return None
    values.sort(reverse=True)
    latest_day, latest = values[0]
    change = latest - values[1][1] if len(values) > 1 else None
    return SeriesReading(series_id, latest_day, latest, change)


async def fetch_series(
    client: httpx.AsyncClient, source: Source, env: dict[str, str], series_id: str
) -> SeriesReading | None:
    response = await client.get(expand_url(source.url or "", env))
    response.raise_for_status()
    return parse_observations(series_id, response.content)
