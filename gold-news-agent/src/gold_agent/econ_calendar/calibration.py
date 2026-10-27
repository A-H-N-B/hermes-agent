"""Surprise s.d. per event (C1.9): the config prior until enough releases are recorded."""

from __future__ import annotations

import statistics
from typing import Literal

from gold_agent.econ_calendar.events import EventCatalog, EventSpec
from gold_agent.storage.news_repo import NewsRepo

MIN_CALIBRATION_SAMPLES = 12

ZBasis = Literal["calibrated", "prior"]


class SurpriseCalibration:
    def __init__(self, catalog: EventCatalog, news: NewsRepo) -> None:
        self._catalog = catalog
        self._news = news
        self._std: dict[str, float] = {}

    async def refresh(self) -> None:
        """Replace prior s.d. with measured ones once an event has enough recorded surprises."""
        for spec in self._catalog.events:
            readings = await self._news.releases_for(spec.key, limit=200)
            surprises = [r.surprise for r in readings if r.surprise is not None]
            if len(surprises) >= MIN_CALIBRATION_SAMPLES:
                std = statistics.pstdev(surprises)
                if std > 0:
                    self._std[spec.key] = std

    def std_for(self, spec: EventSpec) -> tuple[float, ZBasis]:
        if spec.key in self._std:
            return self._std[spec.key], "calibrated"
        return spec.surprise_std, "prior"
