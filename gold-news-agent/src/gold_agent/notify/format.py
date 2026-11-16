"""Telegram HTML helpers. Every dynamic string goes through `esc`; tags never span lines, so a
message can be split on line boundaries without breaking markup."""

from __future__ import annotations

import html
import re
from datetime import datetime

from gold_agent.timeutils.sessions import to_wib

TELEGRAM_LIMIT = 4096
_TAG = re.compile(r"<[^>]+>")

DISCLAIMER = "<i>Bukan saran keuangan. Keputusan &amp; risiko di tanganmu.</i>"


def esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def strip_tags(text: str) -> str:
    return html.unescape(_TAG.sub("", text))


def signed(value: float, places: int = 2) -> str:
    return f"{value:+.{places}f}"


def price(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def wib_clock(moment: datetime, *, seconds: bool = False) -> str:
    return to_wib(moment).strftime("%H:%M:%S" if seconds else "%H:%M")


def duration_id(seconds: float) -> str:
    """'45 dtk', '12 mnt', '3 jam 5 mnt' (Bahasa Indonesia)."""
    seconds = max(0.0, seconds)
    if seconds < 90:
        return f"{seconds:.0f} dtk"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} mnt"
    hours, rest = divmod(minutes, 60)
    return f"{hours} jam {rest} mnt" if rest else f"{hours} jam"


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split on line boundaries into chunks Telegram accepts; an over-long single line is cut as
    plain text (its tags removed) so no chunk carries half a tag."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        if len(line) > limit:
            plain = strip_tags(line)
            while len(esc(plain)) > limit - 1:  # cut before escaping: never half an entity
                keep = int(len(plain) * (limit - 1) / len(esc(plain)))
                plain = plain[: min(keep, len(plain) - 1)]
            line = esc(plain) + "…"
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]
