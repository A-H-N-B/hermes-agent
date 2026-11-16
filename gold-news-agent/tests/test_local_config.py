"""This computer's own settings (config/local.yaml, config/sources.local.yaml): they override the
shipped files, a bad edit is refused, and before an update the user's edits to the tracked files
move into them so `git pull` never conflicts and nothing the user set is lost."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from gold_agent.config import CONFIG_DIR, load_settings, load_sources
from gold_agent.local_config import changes, main, merge

GIT = shutil.which("git")


@pytest.fixture
def config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    target.mkdir()
    for name in ("settings.yaml", "sources.yaml"):
        shutil.copy(CONFIG_DIR / name, target / name)
    return target


def validate(config: Path) -> Callable[[], object]:
    return lambda: (load_settings(config / "settings.yaml"), load_sources(config / "sources.yaml"))


def test_nested_settings_merge_and_only_real_changes_are_kept() -> None:
    base = {"risk": {"account_equity": None, "max_spread_usd": 0.5}, "list": [1, 2]}
    assert merge(base, {"risk": {"account_equity": 1000}, "list": [3]}) == {
        "risk": {"account_equity": 1000, "max_spread_usd": 0.5},
        "list": [3],
    }
    edited = merge(base, {"risk": {"account_equity": 1000}})
    assert changes(base, edited) == {"risk": {"account_equity": 1000}}


def test_local_files_override_the_shipped_config(config: Path) -> None:
    assert main(["--config-dir", str(config), "set", "risk.account_equity", "2500"]) == 0
    assert main(["--config-dir", str(config), "set", "trading.timeframe.select", "M15"]) == 0
    assert main(["--config-dir", str(config), "verified", "investinglive_rss"]) == 0
    settings = load_settings(config / "settings.yaml")
    assert settings.risk.account_equity == 2500 and settings.trading.timeframe.select == "M15"
    assert settings.risk.max_spread_usd == 0.5  # untouched keys keep the shipped value
    source = load_sources(config / "sources.yaml").get("investinglive_rss")
    assert source.status.value == "verified"
    assert load_settings(config / "settings.yaml", local=False).risk.account_equity is None


def test_an_edit_that_would_stop_the_agent_starting_is_refused(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ["--config-dir", str(config), "set", "risk.risk_per_trade_pct", "banyak"]
    assert main(args, validate=validate(config)) == 1
    assert not (config / "local.yaml").exists()  # rolled back
    assert "risk_per_trade_pct" in capsys.readouterr().err
    assert main(["--config-dir", str(config), "verified", "tidak_ada"]) == 1


@pytest.mark.skipif(GIT is None, reason="git not installed")
def test_before_an_update_the_users_edits_move_to_the_local_files(
    config: Path, tmp_path: Path
) -> None:
    def git(*args: str) -> str:
        assert GIT is not None
        done = subprocess.run(  # noqa: S603 - fixed git commands in a temp repo
            [GIT, *args], cwd=tmp_path, capture_output=True, text=True, check=True
        )
        return done.stdout

    git("init", "-q")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "add", "config")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "shipped")
    settings = config / "settings.yaml"
    text = settings.read_text(encoding="utf-8")
    text = text.replace("  contact_email: null", '  contact_email: "saya@example.com"')
    settings.write_text(text.replace("  file: null", "  file: data/logs/gold-agent.log"))
    sources = config / "sources.yaml"
    marked = sources.read_text(encoding="utf-8").replace(
        "    status: candidate\n    tos_url: https://investinglive.com/terms",
        "    status: verified\n    tos_url: https://investinglive.com/terms",
        1,
    )
    assert marked != sources.read_text(encoding="utf-8")
    sources.write_text(marked, encoding="utf-8")

    assert main(["--config-dir", str(config), "migrate", "--backups", str(tmp_path / "b")]) == 0

    assert git("status", "--porcelain", "--", "config/settings.yaml", "config/sources.yaml") == ""
    patch = next((tmp_path / "b").glob("config-sebelum-update-*.patch")).read_text()
    assert "saya@example.com" in patch  # the full diff is kept
    loaded = load_settings(settings)
    assert loaded.http.contact_email == "saya@example.com"
    assert loaded.logging.file == "data/logs/gold-agent.log"
    registry = load_sources(sources)
    assert registry.get("investinglive_rss").status.value == "verified"
    assert main(["--config-dir", str(config), "migrate"]) == 0  # nothing left to move


def test_the_updater_reads_the_setting_this_computer_runs_with(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--config-dir", str(config), "get", "price_feed.primary"]) == 0
    assert capsys.readouterr().out.strip() == "auto"  # shipped default
    (config / "local.yaml").write_text("price_feed:\n  primary: binance_paxg\n", encoding="utf-8")
    assert main(["--config-dir", str(config), "get", "price_feed.primary"]) == 0
    assert capsys.readouterr().out.strip() == "binance_paxg"  # this computer's choice wins
    assert main(["--config-dir", str(config), "get", "price_feed.nothing"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_after_an_update_settings_the_version_dropped_are_removed(
    config: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (config / "local.yaml").write_text(
        "trading:\n  style: scalping\nrisk:\n  account_equity: 900\n", encoding="utf-8"
    )
    assert main(["--config-dir", str(config), "repair"], validate=validate(config)) == 0
    assert "dibuang (tidak dipakai lagi): trading.style" in capsys.readouterr().out
    assert load_settings(config / "settings.yaml").risk.account_equity == 900  # kept
    (config / "local.yaml").write_text("risk:\n  risk_per_trade_pct: banyak\n", encoding="utf-8")
    assert main(["--config-dir", str(config), "repair"], validate=validate(config)) == 1
