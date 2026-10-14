from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from gold_agent.config import (
    CONFIG_DIR,
    Secrets,
    Settings,
    Source,
    SourceRegistry,
    expand_url,
    load_settings,
    load_sources,
)


def _source(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "example",
        "name": "Example",
        "kind": "news",
        "type": "rss",
        "url": "https://example.com/feed",
        "tier": 1,
        "reliability": 0.5,
        "enabled": True,
        "status": "candidate",
        "tos_note": "n/a",
    }
    return base | overrides


def test_shipped_config_files_validate() -> None:
    settings = load_settings()
    registry = load_sources()
    assert settings.execution_enabled is False
    assert settings.mode == "SIGNAL_ONLY"
    assert registry.sources


def test_collectors_only_get_enabled_and_verified_sources() -> None:
    registry = SourceRegistry.model_validate(
        {
            "sources": [
                _source(id="verified_on", status="verified"),
                _source(id="verified_off", status="verified", enabled=False),
                _source(id="candidate_on"),
                _source(id="rejected_on", status="rejected"),
            ]
        }
    )
    assert [s.id for s in registry.production()] == ["verified_on"]


def test_url_placeholders_must_be_declared_as_required_env() -> None:
    with pytest.raises(ValidationError, match="undeclared env vars"):
        Source.model_validate(_source(url="https://example.com/?apikey=${SOME_KEY}"))
    ok = Source.model_validate(
        _source(url="https://example.com/?apikey=${SOME_KEY}", requires_env=["SOME_KEY"])
    )
    assert ok.placeholders() == {"SOME_KEY"}


def test_duplicate_source_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate source id"):
        SourceRegistry.model_validate({"sources": [_source(), _source()]})


def test_websocket_sources_need_wss_and_telegram_sources_have_no_url() -> None:
    with pytest.raises(ValidationError, match="wss://"):
        Source.model_validate(_source(type="websocket", url="https://example.com/ws"))
    with pytest.raises(ValidationError, match="channels, not url"):
        Source.model_validate(_source(type="telegram"))


def test_expand_url_fills_placeholders_and_names_missing_ones() -> None:
    template = "https://api.example.com/q?key=${A_KEY}&x=1"
    assert expand_url(template, {"A_KEY": "abc"}) == "https://api.example.com/q?key=abc&x=1"
    with pytest.raises(KeyError, match="A_KEY"):
        expand_url(template, {})
    with pytest.raises(KeyError, match="A_KEY"):
        expand_url(template, {"A_KEY": ""})


def _settings_dict() -> dict[str, Any]:
    with (CONFIG_DIR / "settings.yaml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_signal_weights_must_sum_to_one() -> None:
    raw = _settings_dict()
    raw["signal_engine"]["weights"]["news"] = 0.5
    with pytest.raises(ValidationError, match=r"sum to 1\.0"):
        Settings.model_validate(raw)


def test_reversal_threshold_cannot_undercut_entry_threshold() -> None:
    raw = _settings_dict()
    raw["signal_engine"]["reversal_threshold"] = 0.2
    with pytest.raises(ValidationError, match="hysteresis"):
        Settings.model_validate(raw)


def test_execution_cannot_be_enabled_from_config() -> None:
    raw = _settings_dict()
    raw["execution_enabled"] = True
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


def test_time_zones_and_clock_times_are_validated() -> None:
    raw = _settings_dict()
    raw["sessions"]["moments"][0]["zone"] = "Asia/Atlantis"
    with pytest.raises(ValidationError, match="unknown IANA time zone"):
        Settings.model_validate(raw)
    raw = _settings_dict()
    raw["notifications"]["quiet_hours_wib"]["start"] = "25:00"
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)


def test_the_ntp_fallback_is_a_host_name_or_explicitly_off() -> None:
    raw = _settings_dict()
    raw["watchdog"]["ntp_server"] = ""
    with pytest.raises(ValidationError):
        Settings.model_validate(raw)
    raw["watchdog"]["ntp_server"] = None
    assert Settings.model_validate(raw).watchdog.ntp_server is None


def test_secrets_expose_only_non_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FRED_API_KEY", "fred-secret")
    monkeypatch.setenv("FINNHUB_API_KEY", "")
    secrets = Secrets(_env_file=None)  # pyright: ignore[reportCallIssue]
    values = secrets.env_values()
    assert values.get("FRED_API_KEY") == "fred-secret"
    assert "FINNHUB_API_KEY" not in values
    assert "fred-secret" not in repr(secrets)
