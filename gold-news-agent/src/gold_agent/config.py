"""Typed loading of config/*.yaml (behaviour) and .env (secrets only)."""

from __future__ import annotations

import re
from datetime import time
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from gold_agent.models.common import Timeframe

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"

_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_zone(name: str) -> str:
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA time zone: {name}") from exc
    return name


def _check_hhmm(value: str) -> str:
    time.fromisoformat(value)
    return value


Zone = Annotated[str, AfterValidator(_check_zone)]
HHMM = Annotated[str, Field(pattern=r"^\d{2}:\d{2}$"), AfterValidator(_check_hhmm)]


# --------------------------------------------------------------------------- sources.yaml


class SourceKind(StrEnum):
    NEWS = "news"
    CALENDAR = "calendar"
    MARKET = "market"
    MACRO = "macro"
    INFRA = "infra"


class SourceType(StrEnum):
    RSS = "rss"
    JSON = "json"
    CSV = "csv"
    ICS = "ics"
    HTML = "html"
    WEBSOCKET = "websocket"
    TELEGRAM = "telegram"


class SourceStatus(StrEnum):
    CANDIDATE = "candidate"
    VERIFIED = "verified"
    REJECTED = "rejected"


class ParseSpec(StrictModel):
    items: str = ""
    time_field: str | None = None
    time_format: str = "iso"
    title_field: str | None = None
    id_field: str | None = None
    link_field: str | None = None
    body_field: str | None = None


class Source(StrictModel):
    id: Annotated[str, Field(pattern=r"^[a-z0-9_]+$")]
    name: str
    kind: SourceKind
    type: SourceType
    url: str | None = None
    channels: tuple[str, ...] = ()
    parse: ParseSpec | None = None
    tier: Literal[1, 2, 3]
    poll_seconds: float | None = Field(default=None, gt=0)
    reliability: float = Field(ge=0, le=1)
    enabled: bool
    status: SourceStatus
    requires_env: tuple[str, ...] = ()
    official: bool = False
    silence_alert_minutes: int | None = None
    tos_url: str | None = None
    tos_note: str

    @model_validator(mode="after")
    def _url_matches_type(self) -> Self:
        if self.type is SourceType.TELEGRAM:
            if self.url is not None:
                raise ValueError(f"{self.id}: telegram sources use channels, not url")
            return self
        if self.url is None:
            raise ValueError(f"{self.id}: url is required for type {self.type}")
        scheme = "wss://" if self.type is SourceType.WEBSOCKET else "https://"
        if not self.url.startswith(scheme):
            raise ValueError(f"{self.id}: url must start with {scheme}")
        undeclared = self.placeholders() - set(self.requires_env)
        if undeclared:
            raise ValueError(f"{self.id}: url uses undeclared env vars {sorted(undeclared)}")
        return self

    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER.findall(self.url or ""))


class SourceRegistry(StrictModel):
    sources: tuple[Source, ...]

    @field_validator("sources")
    @classmethod
    def _unique_ids(cls, sources: tuple[Source, ...]) -> tuple[Source, ...]:
        seen: set[str] = set()
        for source in sources:
            if source.id in seen:
                raise ValueError(f"duplicate source id: {source.id}")
            seen.add(source.id)
        return sources

    def production(self) -> list[Source]:
        """Sources collectors may use: enabled and verified. Unverified URLs never run."""
        return [s for s in self.sources if s.enabled and s.status is SourceStatus.VERIFIED]

    def get(self, source_id: str) -> Source:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(source_id)


def expand_url(template: str, env: dict[str, str]) -> str:
    """Fill ${VAR} placeholders; raises KeyError naming the first missing variable."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        value = env.get(name)
        if not value:
            raise KeyError(name)
        return value

    return _PLACEHOLDER.sub(_sub, template)


# --------------------------------------------------------------------------- settings.yaml


class TimeSettings(StrictModel):
    storage_tz: Literal["UTC"]
    display_tz: Zone


class HttpSettings(StrictModel):
    user_agent: str
    contact_email: str | None
    timeout_seconds: float = Field(gt=0)
    min_poll_seconds: float = Field(ge=1)

    def full_user_agent(self) -> str:
        if self.contact_email:
            return f"{self.user_agent} contact: {self.contact_email}"
        return self.user_agent


class Timeframes(StrictModel):
    entry: Timeframe
    signal: Timeframe
    trend: tuple[Timeframe, ...]


class TradingSettings(StrictModel):
    symbol: str
    style: Literal["scalping", "intraday", "swing"]
    timeframes: Timeframes
    news_trading: bool
    post_release_confirm_seconds: int = Field(ge=0, le=600)


PriceFeedName = Literal["mt5", "mt5_bridge", "twelvedata", "binance_paxg"]


class PriceFeedSettings(StrictModel):
    primary: PriceFeedName
    fallbacks: tuple[PriceFeedName, ...]
    mt5_symbol: str | None
    mt5_server_utc_offset: Literal["auto"] | int
    stale_tick_seconds: int = Field(gt=0)


class RiskSettings(StrictModel):
    account_equity: float | None = Field(default=None, gt=0)
    account_currency: str
    risk_per_trade_pct: float = Field(gt=0, le=2)
    max_daily_loss_pct: float = Field(gt=0, le=10)
    max_open_positions: int = Field(ge=0)
    max_signals_per_day: int = Field(ge=1)
    max_spread_usd: float = Field(gt=0)
    contract_size: float | None = Field(default=None, gt=0)
    volume_step: float = Field(gt=0)


class BudgetSettings(StrictModel):
    llm_daily_usd: float = Field(ge=0)
    paid_data_monthly_usd: float = Field(ge=0)


class QuietHours(StrictModel):
    start: HHMM
    end: HHMM
    allow_priorities: tuple[Literal["high", "normal", "digest"], ...]


class NotificationSettings(StrictModel):
    quiet_hours_wib: QuietHours
    digest_interval_minutes: int = Field(gt=0)
    max_messages_per_minute: int = Field(gt=0)
    dedupe_minutes: int = Field(gt=0)
    news_min_impact: int = Field(ge=0, le=5)
    news_high_impact: int = Field(ge=0, le=5)
    outlook_triggers: tuple[str, ...]
    max_age_minutes: dict[Literal["flash", "news", "alert", "outlook"], int]


class DeploymentSettings(StrictModel):
    host: Literal["linux_vps", "windows_vps", "home_pc"]


class Weights(StrictModel):
    news: float = Field(ge=0)
    macro: float = Field(ge=0)
    technical: float = Field(ge=0)
    price_confirmation: float = Field(ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> Self:
        total = self.news + self.macro + self.technical + self.price_confirmation
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"signal weights must sum to 1.0, got {total}")
        return self


class LevelSettings(StrictModel):
    sl_atr_multiple: float = Field(gt=0)
    sl_atr_timeframe: Timeframe
    tp_r_multiples: tuple[float, ...]
    move_sl_to_breakeven_after_tp1: bool


class SignalEngineSettings(StrictModel):
    weights: Weights
    entry_threshold: float = Field(gt=0, lt=1)
    min_confidence: int = Field(ge=0, le=100)
    reversal_threshold: float = Field(gt=0, le=1)
    reversal_min_minutes: int = Field(ge=0)
    cooldown_minutes: int = Field(ge=0)
    event_blackout_minutes: int = Field(ge=0)
    conflict_threshold: float = Field(gt=0, le=1)
    validity_minutes: dict[Literal["scalping", "intraday", "swing"], int]
    publish_state_seconds: float = Field(gt=0)
    evaluate_seconds: float = Field(gt=0)
    levels: LevelSettings

    @model_validator(mode="after")
    def _hysteresis(self) -> Self:
        if self.reversal_threshold < self.entry_threshold:
            raise ValueError("reversal_threshold must be >= entry_threshold (hysteresis)")
        return self


class DedupSettings(StrictModel):
    fuzzy_ratio: int = Field(ge=50, le=100)
    window_minutes: int = Field(gt=0)
    embedding_model: str
    embedding_cosine: float = Field(gt=0, le=1)


class NewsSettings(StrictModel):
    dedup: DedupSettings
    unconfirmed_weight: float = Field(ge=0, le=1)


class PriceLedSettings(StrictModel):
    atr_multiple_k: float = Field(gt=0)
    news_lookback_minutes: int = Field(gt=0)


Effort = Literal["low", "medium", "high", "xhigh", "max"]


class LlmRoute(StrictModel):
    model: str
    effort: Effort
    thinking: Literal["adaptive", "disabled"] = "adaptive"
    timeout_seconds: float = Field(gt=0)
    max_tokens: int = Field(gt=0)


class CircuitBreaker(StrictModel):
    failures: int = Field(gt=0)
    cooldown_seconds: int = Field(gt=0)


class ModelPrice(StrictModel):
    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cache_write: float = Field(ge=0)
    cache_read: float = Field(ge=0)


class LlmSettings(StrictModel):
    provider: Literal["anthropic"]
    enabled: bool
    deep_min_impact: int = Field(ge=0, le=5)
    triage: LlmRoute
    deep: LlmRoute
    outlook: LlmRoute
    brief: LlmRoute
    circuit_breaker: CircuitBreaker
    pricing_per_mtok: dict[str, ModelPrice]


class AnalysisSettings(StrictModel):
    triage_max_age_minutes: int = Field(gt=0)
    big_claim_categories: tuple[str, ...]
    deep_on_scheduled_release: bool
    release_calendar_window_minutes: int = Field(gt=0)
    max_body_chars: int = Field(gt=0)
    volatility_cooldown_minutes: int = Field(gt=0)
    spread_spike_multiple: float = Field(gt=1)
    targeted_search_sources: tuple[str, ...]


class SessionMoment(StrictModel):
    id: str
    zone: Zone
    local: HHMM


class DailyWindow(StrictModel):
    zone: Zone
    start: HHMM
    end: HHMM


class WeeklyMoment(StrictModel):
    zone: Zone
    weekday: Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    local: HHMM


class SessionSettings(StrictModel):
    moments: tuple[SessionMoment, ...]
    daily_break: DailyWindow
    weekly_open: WeeklyMoment
    weekly_close: WeeklyMoment
    liquidity: dict[Literal["asia", "london", "overlap", "new_york"], float]
    holiday_factor: float = Field(gt=0, le=1)


class OutlookSettings(StrictModel):
    conditional_beyond_hours: int = Field(gt=0)
    refresh_minute: int = Field(ge=0, le=59)
    refresh_on_moments: tuple[str, ...]
    refresh_debounce_minutes: int = Field(ge=0)
    use_llm: bool
    weekend_outlook: WeeklyMoment
    near_confidence_decay_per_hour: float = Field(ge=0, le=1)
    conditional_confidence_cap: int = Field(ge=0, le=100)
    event_range_multiplier: float = Field(ge=1)
    profile_min_samples: int = Field(gt=0)
    profile_days: int = Field(gt=0)
    playbook_build_minutes: int = Field(gt=0)
    playbook_send_minutes: int = Field(gt=0)

    @model_validator(mode="after")
    def _playbook_order(self) -> OutlookSettings:
        if self.playbook_send_minutes > self.playbook_build_minutes:
            raise ValueError("playbook_send_minutes must not exceed playbook_build_minutes")
        return self


class MarketSettings(StrictModel):
    candle_buffer: int = Field(gt=50)
    history_bars: dict[Timeframe, int]
    tick_store_min_interval_ms: int = Field(ge=0)
    tick_publish_min_interval_ms: int = Field(ge=0)
    context_store_seconds: float = Field(gt=0)
    spread_window_minutes: int = Field(gt=0)
    round_levels: tuple[float, ...]
    swing_half_width: int = Field(ge=1)
    usd_source: Literal["binance_eurusdt", "mt5_synthetic", "none"]
    usd_history_days: int = Field(gt=0)
    fred_refresh_minutes: int = Field(gt=0)
    twelvedata_symbol: str
    twelvedata_poll_seconds: float = Field(gt=0)
    mt5_poll_ms: int = Field(gt=0)
    mt5_fx_symbols: dict[str, str]


class DashboardSettings(StrictModel):
    host: str
    port: int = Field(gt=0, lt=65536)
    session_hours: float = Field(gt=0, le=168)
    login_attempts_per_window: int = Field(gt=0)
    login_window_minutes: int = Field(gt=0)
    news_limit: int = Field(gt=0, le=500)
    chart_timeframe: Timeframe
    chart_bars: int = Field(gt=10, le=5000)
    tick_push_per_second: float = Field(gt=0, le=20)


class InfraSettings(StrictModel):
    bus: Literal["memory", "redis"]
    context_interval_seconds: float = Field(gt=0)


class LoggingSettings(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"]
    format: Literal["json", "console"]


class CollectorSettings(StrictModel):
    max_item_age_minutes: int = Field(gt=0)
    seen_capacity: int = Field(gt=0)
    health_flush_seconds: float = Field(gt=0)
    backoff_base_seconds: float = Field(gt=0)
    backoff_max_seconds: float = Field(gt=0)


class WatchdogSettings(StrictModel):
    default_source_silence_minutes: int = Field(gt=0)
    tick_stale_alert_seconds: int = Field(gt=0)
    queue_backlog_alert: int = Field(gt=0)


class Settings(StrictModel):
    version: Literal[1]
    mode: Literal["SIGNAL_ONLY", "PAPER"]
    execution_enabled: Literal[False]
    time: TimeSettings
    infra: InfraSettings
    logging: LoggingSettings
    http: HttpSettings
    collectors: CollectorSettings
    trading: TradingSettings
    price_feed: PriceFeedSettings
    market: MarketSettings
    risk: RiskSettings
    budget: BudgetSettings
    notifications: NotificationSettings
    deployment: DeploymentSettings
    signal_engine: SignalEngineSettings
    news: NewsSettings
    price_led: PriceLedSettings
    analysis: AnalysisSettings
    llm: LlmSettings
    sessions: SessionSettings
    outlook: OutlookSettings
    dashboard: DashboardSettings
    watchdog: WatchdogSettings


# --------------------------------------------------------------------------- .env


class Secrets(BaseSettings):
    """Secrets only. Read from the process environment, then PROJECT_ROOT/.env."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    anthropic_api_key: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: SecretStr | None = None
    telegram_admin_user_ids: SecretStr | None = None
    telegram_api_id: SecretStr | None = None
    telegram_api_hash: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    twelvedata_api_key: SecretStr | None = None
    finnhub_api_key: SecretStr | None = None
    x_bearer_token: SecretStr | None = None
    mt5_login: SecretStr | None = None
    mt5_password: SecretStr | None = None
    mt5_server: SecretStr | None = None
    dashboard_password: SecretStr | None = None
    database_url: SecretStr | None = None
    redis_url: SecretStr | None = None

    def env_values(self) -> dict[str, str]:
        """Non-empty secrets keyed by their env var name (for URL placeholders + redaction)."""
        values: dict[str, str] = {}
        for name in type(self).model_fields:
            secret = getattr(self, name)
            if isinstance(secret, SecretStr) and secret.get_secret_value():
                values[name.upper()] = secret.get_secret_value()
        return values


# --------------------------------------------------------------------------- loaders


def _read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_sources(path: Path = CONFIG_DIR / "sources.yaml") -> SourceRegistry:
    return SourceRegistry.model_validate(_read_yaml(path))


def load_settings(path: Path = CONFIG_DIR / "settings.yaml") -> Settings:
    return Settings.model_validate(_read_yaml(path))
