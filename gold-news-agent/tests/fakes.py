"""Test doubles for the Anthropic client (no network, no spend)."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2


def usage(
    input_tokens: int = 120, output_tokens: int = 80, cache_write: int = 0, cache_read: int = 1800
) -> SimpleNamespace:
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
    )


def parsed(
    value: Any,
    *,
    stop_reason: str = "end_turn",
    tokens: SimpleNamespace | None = None,
    model: str | None = None,
) -> Any:
    """A parse() response; `model` is the model that served it (absent = the requested one)."""
    response = SimpleNamespace(
        parsed_output=value, usage=tokens or usage(), stop_reason=stop_reason
    )
    if model is not None:
        response.model = model
    return response


def timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(
        request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    )


Responder = Callable[[dict[str, Any]], Any]


class FakeMessages:
    def __init__(self, owner: FakeAnthropic) -> None:
        self._owner = owner

    async def parse(self, **request: Any) -> Any:
        self._owner.requests.append(request)
        if not self._owner.script:
            raise AssertionError("FakeAnthropic: no scripted response left")
        step = self._owner.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(request)
        return step


class FakeAnthropic:
    """Scripted responses, consumed in order: a value, an exception, or fn(request) -> value."""

    def __init__(self, *script: Any) -> None:
        self.script: list[Any] = list(script)
        self.requests: list[dict[str, Any]] = []
        self.options: list[dict[str, Any]] = []
        self.messages = FakeMessages(self)
        self.beta = SimpleNamespace(messages=self.messages)  # the client calls beta.messages.parse

    def with_options(self, **options: Any) -> FakeAnthropic:
        self.options.append(options)
        return self
