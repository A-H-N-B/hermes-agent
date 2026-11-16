"""Pre-computed event playbooks (11.4): what each surprise bucket means, built at T-60."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime
from typing import Literal

from gold_agent.econ_calendar.events import EventSpec, parse_number, to_unit
from gold_agent.econ_calendar.release_parser import Bucket, bucket_for, direction_for
from gold_agent.models import CalendarEvent, Playbook, PlaybookBucket, ReleaseReading

# (bucket, hawkish_z lower bound, upper bound) — boundaries from section 11.4.
BUCKETS: list[tuple[Bucket, float | None, float | None]] = [
    ("strong_bearish", 1.5, None),
    ("mild_bearish", 0.5, 1.5),
    ("neutral", -0.5, 0.5),
    ("mild_bullish", -1.5, -0.5),
    ("strong_bullish", None, -1.5),
]
HINTS: dict[str, str] = {
    "strong_bearish": "bearish kuat → cari SELL setelah konfirmasi harga",
    "mild_bearish": "bearish ringan → condong SELL, tunggu konfirmasi",
    "neutral": "netral → lihat komponen inti/revisi, jangan kejar harga",
    "mild_bullish": "bullish ringan → condong BUY, tunggu konfirmasi",
    "strong_bullish": "bullish kuat → cari BUY setelah konfirmasi harga",
}


def _decimals(text: str | None) -> int:
    if not text:
        return 2
    digits = text.strip().rstrip("%KMBkmb ")
    return len(digits.split(".")[1]) if "." in digits else 0


def build_playbook(
    event: CalendarEvent,
    spec: EventSpec,
    *,
    surprise_std: float,
    z_basis: Literal["calibrated", "prior"],
    now: datetime,
) -> Playbook:
    forecast: float | None = None
    previous: float | None = None
    if event.forecast and (parsed := parse_number(event.forecast)):
        forecast = (
            to_unit(parsed[0], parsed[1], spec.unit) if parsed[1] in ("K", "M", "B") else parsed[0]
        )
    if event.previous and (parsed := parse_number(event.previous)):
        previous = (
            to_unit(parsed[0], parsed[1], spec.unit) if parsed[1] in ("K", "M", "B") else parsed[0]
        )
    sign = 1.0 if spec.higher_is == "hawkish" else -1.0
    places = _decimals(event.forecast) + 2
    buckets: list[PlaybookBucket] = []
    for name, z_low, z_high in BUCKETS:
        actual_min = actual_max = None
        if forecast is not None:
            bounds = [
                forecast + sign * z * surprise_std if z is not None else None
                for z in (z_low, z_high)
            ]
            low, high = (bounds[0], bounds[1]) if sign > 0 else (bounds[1], bounds[0])
            actual_min = round(low, places) if low is not None else None
            actual_max = round(high, places) if high is not None else None
        buckets.append(
            PlaybookBucket(
                name=name,
                hawkish_z_min=z_low,
                hawkish_z_max=z_high,
                actual_min=actual_min,
                actual_max=actual_max,
                direction_gold=direction_for(name),
                hint_id=HINTS[name],
            )
        )
    notes = []
    if z_basis == "prior":
        notes.append("z-score memakai simpangan baku awal (prior), belum dari data historis")
    if forecast is None:
        notes.append("forecast belum tersedia: ambang hanya dalam z-score")
    return Playbook(
        id=hashlib.sha1(f"{event.id}|{now:%Y%m%d}".encode()).hexdigest()[:24],  # noqa: S324
        event_id=event.id,
        event_key=spec.key,
        event_title=event.title,
        scheduled_at=event.scheduled_at,
        forecast=forecast,
        previous=previous,
        unit=spec.unit,
        surprise_std=surprise_std,
        z_basis=z_basis,
        buckets=buckets,
        created_at=now,
        notes=notes,
    )


def match_bucket(playbook: Playbook, reading: ReleaseReading) -> PlaybookBucket | None:
    if reading.hawkish_z is None or math.isnan(reading.hawkish_z):
        return None
    name = bucket_for(reading.hawkish_z)
    return next((b for b in playbook.buckets if b.name == name), None)
