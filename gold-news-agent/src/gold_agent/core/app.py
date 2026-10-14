"""Application context: config + secrets + DB + bus + clock, built once per process."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from gold_agent.config import (
    PROJECT_ROOT,
    Secrets,
    Settings,
    SourceRegistry,
    load_settings,
    load_sources,
)
from gold_agent.core.bus import Bus, MemoryBus, make_bus
from gold_agent.core.clock import Clock, SystemClock
from gold_agent.ops.redaction import Redactor
from gold_agent.storage.calendar_repo import CalendarRepo
from gold_agent.storage.db import DEFAULT_SQLITE_URL, Database
from gold_agent.storage.market_repo import MarketRepo
from gold_agent.storage.news_repo import NewsRepo
from gold_agent.storage.notify_repo import NotifyRepo
from gold_agent.storage.ops_repo import OpsRepo
from gold_agent.storage.signal_repo import SignalRepo

DATA_DIR = PROJECT_ROOT / "data"
TELEGRAM_SESSION = DATA_DIR / "telegram" / "gold_agent"


@dataclass
class AppContext:
    settings: Settings
    sources: SourceRegistry
    secrets: Secrets
    env: dict[str, str]
    db: Database
    bus: Bus
    clock: Clock
    redact: Redactor
    news: NewsRepo = field(init=False)
    ops: OpsRepo = field(init=False)
    market: MarketRepo = field(init=False)
    calendar: CalendarRepo = field(init=False)
    signals: SignalRepo = field(init=False)
    notify: NotifyRepo = field(init=False)

    def __post_init__(self) -> None:
        self.news = NewsRepo(self.db)
        self.ops = OpsRepo(self.db)
        self.market = MarketRepo(self.db)
        self.calendar = CalendarRepo(self.db)
        self.signals = SignalRepo(self.db)
        self.notify = NotifyRepo(self.db)

    async def close(self) -> None:
        await self.bus.close()
        await self.db.dispose()


def build_context(
    *,
    settings: Settings | None = None,
    sources: SourceRegistry | None = None,
    secrets: Secrets | None = None,
    database_url: str | None = None,
    bus: Bus | None = None,
    bus_kind: str | None = None,
    clock: Clock | None = None,
) -> AppContext:
    settings = settings or load_settings()
    sources = sources or load_sources()
    secrets = secrets or Secrets()
    env = secrets.env_values()
    url = database_url or env.get("DATABASE_URL") or DEFAULT_SQLITE_URL
    if bus is None:
        kind = bus_kind or settings.infra.bus
        bus = MemoryBus() if kind == "memory" else make_bus(kind, env.get("REDIS_URL"))
    return AppContext(
        settings=settings,
        sources=sources,
        secrets=secrets,
        env=env,
        db=Database(url),
        bus=bus,
        clock=clock or SystemClock(),
        redact=Redactor(env.values()),
    )


def ensure_data_dir(path: Path = DATA_DIR) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
