"""Native runs can keep a rotating log file: every event lands there too, secrets redacted."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from gold_agent.config import PROJECT_ROOT, LoggingSettings
from gold_agent.core.logging import configure_logging


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:], root.level = handlers, level
    structlog.reset_defaults()


@pytest.mark.usefixtures("restore_logging")
def test_events_also_go_to_the_log_file_redacted(tmp_path: Path) -> None:
    target = tmp_path / "logs" / "gold-agent.log"
    configure_logging("INFO", "json", secrets=["sk-ant-test-secret"], file=target)
    log = structlog.get_logger("test.file")
    log.info("started", key="sk-ant-test-secret")
    log.debug("too detailed")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed")
    lines = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert [line["event"] for line in lines] == ["started", "failed"]
    assert lines[0]["key"] == "***"
    assert "ValueError: boom" in lines[1]["exception"]


def test_a_relative_log_file_lives_under_the_project() -> None:
    assert LoggingSettings(level="INFO", format="json").file_path() is None
    settings = LoggingSettings(level="INFO", format="json", file="data/logs/a.log")
    assert settings.file_path() == PROJECT_ROOT / "data" / "logs" / "a.log"
