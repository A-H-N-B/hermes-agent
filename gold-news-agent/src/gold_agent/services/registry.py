"""Service name -> factory. `gold-agent run all` starts every service in one process."""

from __future__ import annotations

from collections.abc import Callable

from gold_agent.core.app import AppContext
from gold_agent.core.service import Service


def _collector(ctx: AppContext) -> Service:
    from gold_agent.services.collector import CollectorService

    return CollectorService(ctx)


SERVICE_FACTORIES: dict[str, Callable[[AppContext], Service]] = {
    "collector": _collector,
}
