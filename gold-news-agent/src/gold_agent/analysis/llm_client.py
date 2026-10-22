"""Claude API access for analysis: structured outputs, prompt caching, timeouts, circuit breaker,
a hard daily budget and per-call usage/cost logging (section 13.1).

Requests go through the beta Messages endpoint so a safety-classifier refusal (a false positive on
benign news) can be retried server-side on the recommended fallback model (`fallbacks="default"`).
The served model and every sampling iteration are priced at their own rates, so the cost log and
the dashboard's credit estimate stay right when a fallback model answers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any, Literal, TypeVar

import anthropic
import pydantic
import structlog

from gold_agent.config import LlmRoute, LlmSettings, ModelPrice, Settings
from gold_agent.core.clock import Clock
from gold_agent.storage.ops_repo import OpsRepo
from gold_agent.timeutils.sessions import WIB

log = structlog.get_logger(__name__)

Route = Literal["triage", "triage_high", "deep", "outlook", "brief", "desk", "desk_second"]
BudgetState = Literal["ok", "reserve", "exhausted"]  # reserve: only priority calls still run
BUDGET_RANK: dict[BudgetState, int] = {"ok": 0, "reserve": 1, "exhausted": 2}
IN_FLIGHT = "in flight (no result recorded)"
CHARS_PER_TOKEN = 2  # text runs ~3-4 characters per token: this keeps the estimate high
T = TypeVar("T", bound=pydantic.BaseModel)
FALLBACK_BETA = "server-side-fallback-2026-07-01"  # pairs with fallbacks="default"
PRICED_ITERATIONS = ("message", "fallback_message")  # compaction etc. are not model turns


class LlmUnavailable(Exception):
    """Not attempted: disabled, no key, circuit open or over budget."""


def worst_case_usd(llm: LlmSettings, route: LlmRoute, prompt_chars: int) -> float:
    """The most one call can cost, held against the budget while it runs: the prompt as if all of
    it were written to the cache, plus max_tokens of output (thinking included), at the dearest
    model that may serve it. With server-side fallbacks on, a refused hop's input is billed again
    on the fallback model, so the input counts twice. Deliberately high: it is released for the
    real cost when the call ends."""
    prices = [llm.pricing_per_mtok[route.model]]
    hops = 1
    if llm.fallbacks == "default":
        prices, hops = list(llm.pricing_per_mtok.values()), 2
    rate_in = max(max(price.input, price.cache_write) for price in prices)
    rate_out = max(price.output for price in prices)
    tokens_in = prompt_chars / CHARS_PER_TOKEN
    return (hops * tokens_in * rate_in + route.max_tokens * rate_out) / 1e6


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


def usage_cost(prices: Mapping[str, ModelPrice], usage: Any, model: str) -> tuple[float, bool]:
    """Cost of one response: each sampling iteration at its own model's price when the response
    lists them (a refused hop plus the fallback that served), else the totals at `model`'s price.
    The flag is False when a model had no price (its tokens were counted as free)."""
    iterations = [
        step
        for step in getattr(usage, "iterations", None) or []
        if getattr(step, "type", None) in PRICED_ITERATIONS
    ]
    if not iterations:
        return cost_usd(prices.get(model), usage), model in prices
    total, priced = 0.0, True
    for step in iterations:
        name = getattr(step, "model", None) or model
        priced = priced and name in prices
        total += cost_usd(prices.get(name), step)
    return total, priced


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
        self.budget_state: BudgetState = "ok"  # the furthest the WIB day got (for alerts)
        self._budget_day: datetime | None = None
        self._admission = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._settings.llm.enabled and self._client is not None and not self.breaker.is_open

    def route(self, name: Route) -> LlmRoute:
        return getattr(self._settings.llm, name)

    async def spent_today(self) -> float:
        return await self._ops.llm_spend_since(wib_day_start(self._clock.now()))

    def _note_budget(self, total: float) -> None:
        """Track how far today's spend got: it only moves up within a WIB day, so a total that
        dips as reservations resolve does not flap the alerts."""
        budget = self._settings.budget
        state: BudgetState = (
            "exhausted"
            if total > budget.llm_daily_usd
            else "reserve"
            if total > budget.routine_daily_usd
            else "ok"
        )
        day = wib_day_start(self._clock.now())
        if day != self._budget_day:
            self._budget_day, self.budget_state = day, "ok"
        if BUDGET_RANK[state] > BUDGET_RANK[self.budget_state]:
            log.warning("llm.budget", state=state, total=round(total, 4))
            self.budget_state = state

    async def _admit(
        self, route: Route, *, priority: bool, prompt_chars: int, ref_id: str | None
    ) -> int:
        """Hold this call's worst case in llm_usage, then check the WIB day's total (finished
        calls and every hold, this one included): routine calls stop at the routine share of the
        budget, priority calls (the signal decision, high-impact news) at the ceiling. The hold
        comes first and lives in the shared table, so calls running at the same time, in any
        process, cannot pass the ceiling together: at worst two that start together near it both
        wait. This client's own admissions take turns, so its parallel calls (the desk's votes)
        are admitted exactly. Returns the held row."""
        config = self.route(route)
        budget = self._settings.budget
        worst = worst_case_usd(self._settings.llm, config, prompt_chars)
        async with self._admission:
            hold = await self._ops.add_llm_usage(
                ts=self._clock.now(),
                route=route,
                model=config.model,
                cost_usd=worst,
                ok=False,
                error=IN_FLIGHT,
                ref_id=ref_id,
            )
            total = await self.spent_today()
            self._note_budget(total)
            if total <= (budget.llm_daily_usd if priority else budget.routine_daily_usd):
                return hold
            await self._ops.delete_llm_usage(hold)
        spent = total - worst
        if priority:
            raise LlmUnavailable(
                f"batas biaya AI harian ${budget.llm_daily_usd:.2f} tercapai "
                f"(terpakai ${spent:.2f})"
            )
        raise LlmUnavailable(
            f"budget AI rutin hari ini terpakai (${spent:.2f} dari "
            f"${budget.routine_daily_usd:.2f}); sisanya untuk keputusan sinyal dan berita penting"
        )

    async def _record(
        self,
        hold: int,
        route: Route,
        model: str,
        *,
        usage: Any,
        latency_ms: float,
        ok: bool,
        error: str | None,
    ) -> float:
        """Replace the held worst case with what the call really cost."""
        cost, priced = usage_cost(self._settings.llm.pricing_per_mtok, usage, model)
        if usage is not None and not priced:
            log.warning("llm.unpriced_model", model=model, route=route)
        await self._ops.update_llm_usage(
            hold,
            model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cost_usd=cost,
            latency_ms=latency_ms,
            ok=ok,
            error=error[:200] if error else None,
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
        priority: bool = False,
    ) -> LlmResult[T]:
        """`priority`: the signal decision or high-impact news, which may use the reserve."""
        if not self._settings.llm.enabled:
            raise LlmUnavailable("LLM dimatikan (mode rules-only)")
        if self._client is None:
            raise LlmUnavailable("ANTHROPIC_API_KEY belum diisi")
        if self.breaker.is_open:
            raise LlmUnavailable("circuit breaker terbuka setelah kegagalan beruntun")
        hold = await self._admit(
            route, priority=priority, prompt_chars=len(system) + len(user), ref_id=ref_id
        )

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
        if self._settings.llm.fallbacks == "default":
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"

        started = time.perf_counter()
        usage: Any = None
        try:
            client = self._client.with_options(timeout=config.timeout_seconds, max_retries=0)
            response = await client.beta.messages.parse(**request)
            usage = getattr(response, "usage", None)
            served = getattr(response, "model", None) or config.model
            if getattr(response, "stop_reason", None) == "refusal":  # the whole chain declined
                raise LlmFailed("model menolak (refusal)")
            parsed = getattr(response, "parsed_output", None)
            if parsed is None:
                raise LlmFailed("output kosong / tidak sesuai skema", invalid_output=True)
        except (pydantic.ValidationError, ValueError) as exc:
            latency = (time.perf_counter() - started) * 1000
            await self._record(
                hold,
                route,
                config.model,
                usage=usage,
                latency_ms=latency,
                ok=False,
                error=f"invalid output: {exc}",
            )
            self.breaker.failure()
            raise LlmFailed(f"output tidak valid: {exc}", invalid_output=True) from exc
        except LlmFailed as exc:
            latency = (time.perf_counter() - started) * 1000
            await self._record(
                hold,
                route,
                config.model,
                usage=usage,
                latency_ms=latency,
                ok=False,
                error=str(exc),
            )
            self.breaker.failure()
            raise
        except anthropic.APIError as exc:
            latency = (time.perf_counter() - started) * 1000
            await self._record(
                hold,
                route,
                config.model,
                usage=usage,
                latency_ms=latency,
                ok=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            if self.breaker.failure():
                log.error("llm.circuit_open", route=route, error=type(exc).__name__)
            raise LlmFailed(f"{type(exc).__name__}") from exc
        latency = (time.perf_counter() - started) * 1000
        cost = await self._record(
            hold, route, served, usage=usage, latency_ms=latency, ok=True, error=None
        )
        if served != config.model:
            log.info("llm.fallback_served", route=route, requested=config.model, served=served)
        self.breaker.success()
        return LlmResult(
            value=parsed,
            model=served,
            latency_ms=round(latency, 1),
            cost_usd=cost,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        )
