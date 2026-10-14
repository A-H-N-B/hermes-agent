"""Structured logging (structlog) with secret redaction on every event."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable, MutableMapping
from typing import Any

import structlog

from gold_agent.ops.redaction import Redactor


def _redacting_processor(redact: Redactor) -> Any:
    def processor(_logger: Any, _method: str, event: MutableMapping[str, Any]) -> Any:
        for key, value in list(event.items()):
            if isinstance(value, str):
                event[key] = redact(value)
        return event

    return processor


def configure_logging(level: str = "INFO", fmt: str = "json", secrets: Iterable[str] = ()) -> None:
    redact = Redactor(secrets)
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            _redacting_processor(redact),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(
        level=getattr(logging, level.upper()), stream=sys.stdout, format="%(message)s"
    )
    for noisy in ("httpx", "httpcore", "telethon", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
