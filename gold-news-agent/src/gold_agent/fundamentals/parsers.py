"""Parsers for the official files behind the daily fundamentals. Each one is tolerant of column
order and naming changes, and returns nothing rather than a guess when the file is not what it
should be (the snapshot then says the driver has no data)."""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

_TENOR = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(MO|MONTH|MONTHS|YR|YRS|YEAR|YEARS|Y)\b", re.I)


def tenor(name: str) -> str | None:
    """'10 Yr' / '10 YR' / '10 Year' -> '10Y'; '3 Mo' -> '3M'."""
    match = _TENOR.match(name)
    if match is None:
        return None
    number, unit = match.groups()
    return f"{number}{'M' if unit.upper().startswith('MO') else 'Y'}"


def parse_curve(text: str) -> dict[date, dict[str, float]]:
    """US Treasury daily rates CSV (par or real yield curve): day -> tenor -> percent."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    if not rows or not rows[0] or rows[0][0].strip().lower() != "date":
        return {}
    keys = [tenor(name) for name in rows[0][1:]]
    out: dict[date, dict[str, float]] = {}
    for row in rows[1:]:
        if not row:
            continue
        day = _day(row[0], ("%m/%d/%Y",))
        if day is None:
            continue
        values: dict[str, float] = {}
        for key, cell in zip(keys, row[1:], strict=False):
            number = _number(cell)
            if key is not None and number is not None:
                values[key] = number
        if values:
            out[day] = values
    return out


@dataclass(frozen=True)
class CotWeek:
    day: date
    long: float
    short: float
    open_interest: float

    @property
    def net(self) -> float:
        return self.long - self.short

    @property
    def net_share(self) -> float:
        """Net speculative position as a share of open interest."""
        return self.net / self.open_interest if self.open_interest else 0.0


_COT_FIELDS = (  # managed money (disaggregated report), else non-commercials (legacy report)
    ("m_money_positions_long_all", "m_money_positions_short_all"),
    ("noncomm_positions_long_all", "noncomm_positions_short_all"),
)


def parse_cot(rows: Iterable[Mapping[str, Any]]) -> list[CotWeek]:
    """CFTC public reporting rows (Socrata JSON) for one market, oldest first."""
    weeks: dict[date, CotWeek] = {}
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("report_date_as_yyyy_mm_dd", ""))[:10])
        except ValueError:
            continue
        interest = _number(row.get("open_interest_all"))
        for long_key, short_key in _COT_FIELDS:
            long, short = _number(row.get(long_key)), _number(row.get(short_key))
            if long is not None and short is not None and interest:
                weeks[day] = CotWeek(day, long, short, interest)
                break
    return [weeks[day] for day in sorted(weeks)]


def parse_gld(text: str) -> list[tuple[date, float]]:
    """SPDR Gold Shares archive CSV: (day, tonnes held), oldest first. The file has notes above
    its header, so the header is the first row naming both a date and tonnes."""
    rows = list(csv.reader(io.StringIO(text.lstrip("﻿"))))
    for index, row in enumerate(rows):
        cells = [cell.strip().lower() for cell in row]
        date_col = next((i for i, c in enumerate(cells) if c == "date"), None)
        tonnes_col = next((i for i, c in enumerate(cells) if "tonnes" in c), None)
        if date_col is not None and tonnes_col is not None:
            return _gld_rows(rows[index + 1 :], date_col, tonnes_col)
    return []


def _gld_rows(rows: list[list[str]], date_col: int, tonnes_col: int) -> list[tuple[date, float]]:
    out: dict[date, float] = {}
    for row in rows:
        if len(row) <= max(date_col, tonnes_col):
            continue
        day = _day(row[date_col])
        tonnes = _number(row[tonnes_col])
        if day is not None and tonnes is not None and tonnes > 0:
            out[day] = tonnes
    return sorted(out.items())


_GLD_DATES = ("%d-%b-%Y", "%d-%b-%y", "%m/%d/%Y", "%Y-%m-%d")


def _day(text: str, patterns: tuple[str, ...] = _GLD_DATES) -> date | None:
    for pattern in patterns:  # dates without a time: read as UTC calendar days
        try:
            return datetime.strptime(f"{text.strip()} +0000", f"{pattern} %z").date()
        except ValueError:
            continue
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.upper() in ("N/A", "NA", "HOLIDAY", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None
