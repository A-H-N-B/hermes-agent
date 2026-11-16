"""Normalisation (7.2): canonical URL, cleaned/normalised titles, stable ids, language guess."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from gold_agent.collectors.parsers import FeedItem
from gold_agent.config import Source
from gold_agent.models import NewsItem

_TRACKING_PARAM = re.compile(
    r"^(utm_.*|fbclid|gclid|dclid|mc_cid|mc_eid|ref|ref_src|cmpid|ncid|taid|ocid|guccounter|"
    r"soc_src|soc_trk|smid|sr_share|__twitter_impression|igshid|cid)$",
    re.I,
)
_LEADING_NOISE = re.compile(
    r"^[\s*#•·▪►▶→\-–—|:]*"
    r"(?:(?:breaking(?: news)?|flash|just in|urgent|alert|exclusive|update|developing)\b"
    r"[\s:!\-–—|]*)?",
    re.I,
)
_SOURCE_TAG = re.compile(
    r"\s*[(\[](?:reuters|bloomberg|ap|afp|cnbc|wsj|ft|dow jones|dj|marketwatch)[)\]]\s*$", re.I
)
_CLOCK = re.compile(
    r"\b\d{1,2}[:.]\d{2}(?::\d{2})?\s*(?:am|pm|gmt|utc|et|est|edt|bst|cet|wib|jst)?\b", re.I
)
_NON_WORD = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")
_EMOJI = re.compile("[\U0001f300-\U0001faff\U00002600-\U000027bf\U0001f000-\U0001f2ff️‍⃣]+")
_NUMBER = re.compile(r"[-+−]?\d+(?:[.,]\d+)*\s*[%kmb]?", re.I)
_ID_STOPWORDS = frozenset(
    "yang dan di ke dari untuk dengan ini itu akan tidak pada adalah harga emas naik turun "
    "suku bunga juga karena oleh sebagai dalam setelah menjadi bank sentral".split()
)


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return url
    query = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not _TRACKING_PARAM.match(k)
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def clean_title(text: str) -> str:
    """Display title: drop emoji, BREAKING-style prefixes and trailing wire tags; keep case."""
    text = _EMOJI.sub(" ", text)
    text = _SPACE.sub(" ", text).strip()
    text = _LEADING_NOISE.sub("", text, count=1)
    text = _SOURCE_TAG.sub("", text)
    return text.strip(" -–—|:")


def normalize_title(text: str) -> str:
    """Dedup key text: lowercase, no punctuation, no clock times."""
    text = clean_title(text).lower()
    text = _CLOCK.sub(" ", text)
    text = _NON_WORD.sub(" ", text)
    return _SPACE.sub(" ", text).strip()


def title_numbers(text: str) -> tuple[str, ...]:
    """Numbers (with unit) in a title; two headlines with different numbers are different news."""
    cleaned = _CLOCK.sub(" ", clean_title(text))
    return tuple(
        m.group(0).replace(" ", "").replace("−", "-").lower() for m in _NUMBER.finditer(cleaned)
    )


def title_hash(text: str) -> str:
    return hashlib.sha1(normalize_title(text).encode("utf-8")).hexdigest()  # noqa: S324


def stable_id(source_id: str, external_id: str) -> str:
    digest = hashlib.sha1(external_id.encode("utf-8")).hexdigest()[:20]  # noqa: S324
    return f"{source_id}:{digest}"


def detect_lang(text: str) -> str:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return "und"
    hits = sum(1 for w in words if w in _ID_STOPWORDS)
    return "id" if hits >= 2 and hits / len(words) >= 0.1 else "en"


class Normalizer:
    def __init__(self, *, max_title_chars: int = 280) -> None:
        self._max_title = max_title_chars

    def _item(
        self,
        source: Source,
        *,
        external_id: str,
        title: str,
        body: str | None,
        url: str | None,
        published_at: datetime | None,
        first_seen_at: datetime,
        backfill: bool,
    ) -> NewsItem | None:
        title = clean_title(title)[: self._max_title].strip()
        if not title:
            return None
        if body is not None and clean_title(body) == title:
            body = None
        return NewsItem(
            id=stable_id(source.id, external_id),
            source_id=source.id,
            url_canonical=canonical_url(url),
            title=title,
            body=body,
            lang=detect_lang(f"{title} {body or ''}"),
            published_at=published_at,
            first_seen_at=first_seen_at,
            raw_hash=title_hash(title),
            tier=source.tier,
            reliability=source.reliability,
            official=source.official,
            backfill=backfill,
        )

    def from_feed_item(
        self, source: Source, item: FeedItem, *, first_seen_at: datetime, backfill: bool
    ) -> NewsItem | None:
        return self._item(
            source,
            external_id=item.id,
            title=item.title or item.summary or "",
            body=item.summary,
            url=item.link,
            published_at=item.published,
            first_seen_at=first_seen_at,
            backfill=backfill,
        )

    def from_text(
        self,
        source: Source,
        *,
        external_id: str,
        text: str,
        url: str | None,
        published_at: datetime | None,
        first_seen_at: datetime,
        backfill: bool = False,
    ) -> NewsItem | None:
        """Chat messages: first non-empty line is the headline, the full text the body."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None
        body = text.strip() if len(lines) > 1 else None
        return self._item(
            source,
            external_id=external_id,
            title=lines[0],
            body=body,
            url=url,
            published_at=published_at,
            first_seen_at=first_seen_at,
            backfill=backfill,
        )
