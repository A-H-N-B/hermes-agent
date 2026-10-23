"""Downloads behind the daily fundamentals, each source at its own pace (`poll_seconds`).

Only sources that are enabled and verified on this computer are fetched (the same gate as the
news collectors). A failed download keeps the last good data and is retried on the next round.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
import structlog

from gold_agent.config import Source, SourceRegistry, expand_url
from gold_agent.fundamentals.parsers import parse_cot, parse_curve, parse_gld
from gold_agent.fundamentals.snapshot import CURVE_LOOKBACK, Inputs
from gold_agent.ops.source_check import Redactor

log = structlog.get_logger(__name__)

NOMINAL, REAL = "treasury_yield_curve", "treasury_real_yield_curve"
COT, GLD = "cftc_cot_gold", "spdr_gld_holdings"


class FundamentalsFetcher:
    def __init__(self, sources: SourceRegistry, env: dict[str, str]) -> None:
        self._sources = {s.id: s for s in sources.production()}
        self._env = env
        self._redact = Redactor(env.values())
        self._fetched: dict[str, datetime] = {}
        self.inputs = Inputs()

    @property
    def available(self) -> set[str]:
        return {key for key in (NOMINAL, REAL, COT, GLD) if key in self._sources}

    async def refresh(self, client: httpx.AsyncClient, now: datetime) -> None:
        for source_id in sorted(self.available):
            source = self._sources[source_id]
            last = self._fetched.get(source_id)
            if last is not None and (now - last).total_seconds() < (source.poll_seconds or 3600):
                continue
            try:
                await self._load(client, source, now)
            except Exception as exc:
                log.warning(
                    "fundamentals.fetch_failed",
                    source=source_id,
                    error=self._redact(repr(exc))[:200],
                )
                continue
            self._fetched[source_id] = now

    async def _get(self, client: httpx.AsyncClient, source: Source, year: int | None = None) -> Any:
        response = await client.get(
            expand_url(source.url or "", self._env, year=year),
            timeout=source.timeout_seconds or client.timeout,
        )
        response.raise_for_status()
        return response

    async def _load(self, client: httpx.AsyncClient, source: Source, now: datetime) -> None:
        if source.id in (NOMINAL, REAL):
            curve = parse_curve((await self._get(client, source, now.year)).text)
            if len(curve) <= CURVE_LOOKBACK:  # early January: the year's file is still short
                curve = parse_curve((await self._get(client, source, now.year - 1)).text) | curve
            if not curve:
                raise ValueError("file yield Treasury kosong / formatnya berubah")
            if source.id == NOMINAL:
                self.inputs.nominal = curve
            else:
                self.inputs.real = curve
        elif source.id == COT:
            weeks = parse_cot((await self._get(client, source)).json())
            if not weeks:
                raise ValueError("data COT kosong / kolomnya berubah")
            self.inputs.cot = weeks
        elif source.id == GLD:
            points = parse_gld((await self._get(client, source)).text)
            if not points:
                raise ValueError("file GLD kosong / formatnya berubah")
            self.inputs.gld = points
