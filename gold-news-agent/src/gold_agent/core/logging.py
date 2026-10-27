"""Structured logging (structlog) with secret redaction on every event.

Everything goes through stdlib logging to stdout (Docker / systemd collect and rotate it) and,
when `logging.file` is set, to a rotating file as well, for native runs (Windows Task Scheduler,
launchd) where nothing else keeps the output.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from collections.abc import Iterable, MutableMapping
from pathlib import Path
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


LOG_FILE_BYTES = 10 * 1024 * 1024
LOG_FILE_BACKUPS = 5


def configure_logging(
    level: str = "INFO",
    fmt: str = "json",
    secrets: Iterable[str] = (),
    *,
    file: Path | None = None,
) -> None:
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
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if file is not None:
        file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                file, maxBytes=LOG_FILE_BYTES, backupCount=LOG_FILE_BACKUPS, encoding="utf-8"
            )
        )
    logging.basicConfig(
        level=getattr(logging, level.upper()), format="%(message)s", handlers=handlers, force=True
    )
    for noisy in ("httpx", "httpcore", "telethon", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
