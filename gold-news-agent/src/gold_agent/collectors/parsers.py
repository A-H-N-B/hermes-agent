"""Body parsers shared by the Phase 0 verifier and the production collectors."""

from __future__ import annotations

import calendar
import csv
import html
import io
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, cast

import feedparser  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, Field

from gold_agent.config import ParseSpec, Source, SourceType

_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


class FormatError(ValueError):
    """The body was fetched but is not the format the source claims."""


class FeedItem(BaseModel):
    id: str
    title: str | None = None
    published: datetime | None = None
    link: str | None = None
    summary: str | None = None


class ParsedBody(BaseModel):
    items: list[FeedItem]
    notes: list[str] = Field(default_factory=list)


def strip_html(text: str | None) -> str | None:
    if not text:
        return None
    cleaned = _SPACE.sub(" ", html.unescape(_TAG.sub(" ", text))).strip()
    return cleaned or None


def to_utc(moment: datetime) -> tuple[datetime, bool]:
    """Return (aware UTC datetime, whether the zone had to be assumed)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC), True
    return moment.astimezone(UTC), False


def parse_timestamp(value: object, fmt: str) -> datetime | None:
    """Parse a source timestamp; returns a naive datetime when the source gave no zone."""
    if value is None or value == "":
        return None
    text = str(value)
    if fmt == "unix":
        return datetime.fromtimestamp(float(text), UTC)
    if fmt == "unix_ms":
        return datetime.fromtimestamp(float(text) / 1000, UTC)
    if fmt == "iso":
        return datetime.fromisoformat(text)
    if fmt == "date":
        return datetime.strptime(text, "%Y-%m-%d")  # noqa: DTZ007 - zone resolved by caller
    return datetime.strptime(text, fmt)  # noqa: DTZ007 - zone resolved by caller


def _resolve(data: Any, path: str) -> Any:
    node = data
    for part in filter(None, path.split(".")):
        if not isinstance(node, dict):
            return None
        node = cast(dict[str, Any], node).get(part)
    return node


def _finish(raw: Iterable[FeedItem], naive: Iterable[bool]) -> ParsedBody:
    items: list[FeedItem] = []
    assumed = 0
    for item, was_naive in zip(raw, naive, strict=True):
        items.append(item)
        assumed += was_naive
    notes = [f"{assumed} timestamp tanpa zona waktu, diasumsikan UTC"] if assumed else []
    return ParsedBody(items=items, notes=notes)


def parse_rss(content: bytes) -> ParsedBody:
    parsed = cast(Any, feedparser.parse(content))
    entries = cast(list[Any], parsed.entries)
    if parsed.bozo and not entries:
        raise FormatError(f"bukan RSS/Atom yang valid: {parsed.bozo_exception}")
    items: list[FeedItem] = []
    for index, entry in enumerate(entries):
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(struct), UTC) if struct else None
        title = strip_html(entry.get("title"))
        link = entry.get("link")
        item_id = entry.get("id") or link or title or str(index)
        items.append(
            FeedItem(
                id=str(item_id),
                title=title,
                published=published,
                link=link,
                summary=strip_html(entry.get("summary")),
            )
        )
    notes = ["feed punya peringatan parser (bozo) tapi tetap terbaca"] if parsed.bozo else []
    return ParsedBody(items=items, notes=notes)


def _text(row: dict[str, Any], field: str | None) -> str | None:
    if not field:
        return None
    value = row.get(field)
    return strip_html(str(value)) if value not in (None, "") else None


def parse_json_items(content: bytes, spec: ParseSpec | None) -> ParsedBody:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise FormatError(f"bukan JSON yang valid: {exc}") from exc
    spec = spec or ParseSpec()
    node: Any = _resolve(data, spec.items)
    if isinstance(node, dict):
        entries: list[Any] = [node]
    elif isinstance(node, list):
        entries = cast(list[Any], node)
    else:
        raise FormatError(f"path item '{spec.items}' bukan list")
    raw: list[FeedItem] = []
    naive: list[bool] = []
    unparsable = 0
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        row = cast(dict[str, Any], entry)
        published: datetime | None = None
        was_naive = False
        if spec.time_field:
            try:
                moment = parse_timestamp(row.get(spec.time_field), spec.time_format)
            except (ValueError, OverflowError):
                moment = None
                unparsable += 1
            if moment is not None:
                published, was_naive = to_utc(moment)
        title = _text(row, spec.title_field)
        link = _text(row, spec.link_field)
        item_id = row.get(spec.id_field) if spec.id_field else None
        raw.append(
            FeedItem(
                id=str(item_id or link or title or index),
                title=title,
                published=published,
                link=link,
                summary=_text(row, spec.body_field),
            )
        )
        naive.append(was_naive)
    body = _finish(raw, naive)
    if unparsable:
        body.notes.append(f"{unparsable} timestamp tidak bisa dibaca ({spec.time_format})")
    return body


def parse_csv_items(content: bytes, spec: ParseSpec | None) -> ParsedBody:
    text = content.decode("utf-8-sig", errors="replace")
    rows = [row for row in csv.reader(io.StringIO(text)) if any(cell.strip() for cell in row)]
    if not rows:
        raise FormatError("CSV kosong")
    header, data_rows = rows[0], rows[1:]
    column = header.index(spec.time_field) if spec and spec.time_field in header else None
    raw: list[FeedItem] = []
    naive: list[bool] = []
    for index, row in enumerate(data_rows):
        published: datetime | None = None
        was_naive = False
        if spec and column is not None and column < len(row):
            try:
                moment = parse_timestamp(row[column].strip(), spec.time_format)
            except ValueError:
                moment = None
            if moment is not None:
                published, was_naive = to_utc(moment)
        raw.append(FeedItem(id=str(index), published=published))
        naive.append(was_naive)
    return _finish(raw, naive)


_ICS_EVENT = re.compile(r"BEGIN:VEVENT(?P<body>.*?)END:VEVENT", re.S)
_ICS_START = re.compile(r"^DTSTART(?P<params>;[^:]*)?:(?P<value>\d{8}(?:T\d{6}Z?)?)\s*$", re.M)
_ICS_SUMMARY = re.compile(r"^SUMMARY:(?P<value>.*)$", re.M)
_ICS_UID = re.compile(r"^UID:(?P<value>.*)$", re.M)


def parse_ics(content: bytes) -> ParsedBody:
    text = content.decode("utf-8", errors="replace").replace("\r\n ", "").replace("\r\n", "\n")
    if "BEGIN:VCALENDAR" not in text:
        raise FormatError("bukan iCalendar (tidak ada BEGIN:VCALENDAR)")
    raw: list[FeedItem] = []
    naive: list[bool] = []
    zoned = 0
    for index, event in enumerate(_ICS_EVENT.finditer(text)):
        body = event.group("body")
        start = _ICS_START.search(body)
        published: datetime | None = None
        was_naive = False
        if start:
            value, params = start.group("value"), start.group("params") or ""
            fmt = "%Y%m%dT%H%M%SZ" if value.endswith("Z") else "%Y%m%dT%H%M%S"
            if len(value) == 8:
                fmt = "%Y%m%d"
            moment = datetime.strptime(value, fmt)  # noqa: DTZ007 - zone resolved below
            if value.endswith("Z"):
                published = moment.replace(tzinfo=UTC)
            else:
                published, was_naive = to_utc(moment)
                zoned += "TZID" in params
        summary = _ICS_SUMMARY.search(body)
        uid = _ICS_UID.search(body)
        raw.append(
            FeedItem(
                id=uid.group("value").strip() if uid else str(index),
                title=summary.group("value").strip() if summary else None,
                published=published,
            )
        )
        naive.append(was_naive and "TZID" not in (start.group("params") or "") if start else False)
    body = _finish(raw, naive)
    if zoned:
        body.notes.append(f"{zoned} event memakai TZID; zona di-resolve oleh sinkronisasi kalender")
    return body


def parse_body(source: Source, content: bytes) -> ParsedBody:
    match source.type:
        case SourceType.RSS:
            return parse_rss(content)
        case SourceType.JSON:
            return parse_json_items(content, source.parse)
        case SourceType.CSV:
            return parse_csv_items(content, source.parse)
        case SourceType.ICS:
            return parse_ics(content)
        case _:
            return ParsedBody(items=[])
