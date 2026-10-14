"""Claude API access for analysis: structured outputs, prompt caching, timeouts, circuit breaker,
daily budget and per-call usage/cost logging (section 13.1)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any, Literal, TypeVar

import anthropic
import pydantic
import structlog

from gold_agent.config import LlmRoute, ModelPrice, Settings
from gold_agent.core.clock import Clock
from gold_agent.storage.ops_repo import OpsRepo
from gold_agent.timeutils.sessions import WIB

log = structlog.get_logger(__name__)

Route = Literal["triage", "deep", "outlook", "brief"]
T = TypeVar("T", bound=pydantic.BaseModel)


class LlmUnavailable(Exception):
    """Not attempted: disabled, no key, circuit open or over budget."""


class LlmFailed(Exception):
    """Attempted and failed: timeout, API error, refusal or schema-invalid output."""

    def __init__(self, message: str, *, invalid_output: bool = False) -> None:
        super().__init__(message)
        self.invalid_output = invalid_output


@dataclass(frozen=True)
class LlmResult[T2]:
    value: T2
    model: str
    latency_ms: float
    cost_usd: float
    cache_read_tokens: int


def cost_usd(price: ModelPrice | None, usage: Any) -> float:
    if price is None or usage is None:
        return 0.0
    cached_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cached_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    fresh = getattr(usage, "input_tokens", 0) or 0
    output = getattr(usage, "output_tokens", 0) or 0
    total = (
        fresh * price.input
        + cached_write * price.cache_write
        + cached_read * price.cache_read
        + output * price.output
    )
    return total / 1_000_000


class CircuitBreaker:
    def __init__(self, failures: int, cooldown_seconds: float, clock: Clock) -> None:
        self._threshold = failures
        self._cooldown = timedelta(seconds=cooldown_seconds)
        self._clock = clock
        self._failures = 0
        self._open_until: datetime | None = None

    @property
    def is_open(self) -> bool:
        return self._open_until is not None and self._clock.now() < self._open_until

    def success(self) -> None:
        self._failures = 0
        self._open_until = None

    def failure(self) -> bool:
        """Record a failure; returns True when this failure opened the circuit."""
        self._failures += 1
        if self._failures >= self._threshold and not self.is_open:
            self._open_until = self._clock.now() + self._cooldown
            return True
        return False


def wib_day_start(moment: datetime) -> datetime:
    local = moment.astimezone(WIB)
    return datetime.combine(local.date(), dtime(0, 0), tzinfo=WIB)


class LlmClient:
    def __init__(
        self,
        settings: Settings,
        *,
        api_key: str | None,
        ops: OpsRepo,
        clock: Clock,
        client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._ops = ops
        self._clock = clock
        self._client: Any = client
        if self._client is None and api_key:
            self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)
        breaker = settings.llm.circuit_breaker
        self.breaker = CircuitBreaker(breaker.failures, breaker.cooldown_seconds, clock)
        self.budget_exceeded = False

    @property
    def available(self) -> bool:
        return self._settings.llm.enabled and self._client is not None and not self.breaker.is_open

    def route(self, name: Route) -> LlmRoute:
        return getattr(self._settings.llm, name)

    async def spent_today(self) -> float:
        return await self._ops.llm_spend_since(wib_day_start(self._clock.now()))

    async def _check_budget(self, high_impact: bool) -> None:
        spent = await self.spent_today()
        over = spent >= self._settings.budget.llm_daily_usd
        if over and not self.budget_exceeded:
            log.warning("llm.budget_exceeded", spent=round(spent, 4))
        self.budget_exceeded = over
        if over and not high_impact:
            raise LlmUnavailable(f"budget LLM harian habis (${spent:.2f})")

    async def _record(
        self,
        route: Route,
        model: str,
        *,
        usage: Any,
        latency_ms: float,
        ok: bool,
        error: str | None,
        ref_id: str | None,
    ) -> float:
        price = self._settings.llm.pricing_per_mtok.get(model)
        cost = cost_usd(price, usage)
        await self._ops.add_llm_usage(
            ts=self._clock.now(),
            route=route,
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cost_usd=cost,
            latency_ms=latency_ms,
            ok=ok,
            error=error[:200] if error else None,
            ref_id=ref_id,
        )
        return cost

    async def structured(
        self,
        route: Route,
        *,
        system: str,
        user: str,
        schema: type[T],
        ref_id: str | None = None,
        high_impact: bool = False,
    ) -> LlmResult[T]:
        if not self._settings.llm.enabled:
            raise LlmUnavailable("LLM dimatikan (mode rules-only)")
        if self._client is None:
            raise LlmUnavailable("ANTHROPIC_API_KEY belum diisi")
        if self.breaker.is_open:
            raise LlmUnavailable("circuit breaker terbuka setelah kegagalan beruntun")
        await self._check_budget(high_impact)

        config = self.route(route)
        request: dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": [{"role": "user", "content": user}],
            "output_format": schema,
            "output_config": {"effort": config.effort},
        }
        if config.thinking == "disabled":
            request["thinking"] = {"type": "disabled"}

        started = time.perf_counter()
        usage: Any = None
        try:
            client = self._client.with_options(timeout=config.timeout_seconds, max_retries=0)
            response = await client.messages.parse(**request)
            usage = getattr(response, "usage", None)
            if getattr(response, "stop_reason", None) == "refusal":
                raise LlmFailed("model menolak (refusal)")
            parsed = getattr(response, "parsed_output", None)
            if parsed is None:
                raise LlmFailed("output kosong / tidak sesuai skema", invalid_output=True)
        except (pydantic.ValidationError, ValueError) as exc:
            latency = (time.perf_counter() - started) * 1000
            await self._record(
                route,
                config.model,
                usage=usage,
                latency_ms=latency,
                ok=False,
                error=f"invalid output: {exc}",
                ref_id=ref_id,
            )
            self.breaker.failure()
            raise LlmFailed(f"output tidak valid: {exc}", invalid_output=True) from exc
        except LlmFailed as exc:
            latency = (time.perf_counter() - started) * 1000
            await self._record(
                route,
                config.model,
                usage=usage,
                latency_ms=latency,
                ok=False,
                error=str(exc),
                ref_id=ref_id,
            )
            self.breaker.failure()
            raise
        except anthropic.APIError as exc:
            latency = (time.perf_counter() - started) * 1000
            await self._record(
                route,
                config.model,
                usage=usage,
                latency_ms=latency,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
                ref_id=ref_id,
            )
            if self.breaker.failure():
                log.error("llm.circuit_open", route=route, error=type(exc).__name__)
            raise LlmFailed(f"{type(exc).__name__}") from exc
        latency = (time.perf_counter() - started) * 1000
        cost = await self._record(
            route, config.model, usage=usage, latency_ms=latency, ok=True, error=None, ref_id=ref_id
        )
        self.breaker.success()
        return LlmResult(
            value=parsed,
            model=config.model,
            latency_ms=round(latency, 1),
            cost_usd=cost,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
