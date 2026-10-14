"""Service name -> factory. `gold-agent run all` starts every service in one process."""

from __future__ import annotations

from collections.abc import Callable

from gold_agent.core.app import AppContext
from gold_agent.core.service import Service


def _collector(ctx: AppContext) -> Service:
    from gold_agent.services.collector import CollectorService

    return CollectorService(ctx)


def _market(ctx: AppContext) -> Service:
    from gold_agent.services.market import MarketService

    return MarketService(ctx)


def _calendar(ctx: AppContext) -> Service:
    from gold_agent.services.calendar import CalendarService

    return CalendarService(ctx)


def _analyzer(ctx: AppContext) -> Service:
    from gold_agent.services.analyzer import AnalyzerService

    return AnalyzerService(ctx)


def _signal(ctx: AppContext) -> Service:
    from gold_agent.services.signal import SignalService

    return SignalService(ctx)


SERVICE_FACTORIES: dict[str, Callable[[AppContext], Service]] = {
    "collector": _collector,
    "market": _market,
    "calendar": _calendar,
    "analyzer": _analyzer,
    "signal": _signal,
}
