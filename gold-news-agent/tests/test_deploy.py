"""Deployment invariants: every registered service runs in Compose with a healthcheck and a
restart policy, nothing but the dashboard is published (and only on the host's loopback),
secrets never enter the image, and the container does not run as root."""

from __future__ import annotations

from typing import Any

import yaml

from gold_agent.config import PROJECT_ROOT
from gold_agent.services.registry import SERVICE_FACTORIES

INFRA = {"postgres", "redis", "migrate"}


def compose() -> dict[str, Any]:
    with (PROJECT_ROOT / "docker-compose.yml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)["services"]


def test_every_registered_service_is_deployed_supervised_and_health_checked() -> None:
    services = compose()
    assert set(services) - INFRA == set(SERVICE_FACTORIES)
    for name in SERVICE_FACTORIES:
        service = services[name]
        assert service["command"][:5] == ["run", name, "--bus", "redis", "--no-migrate"]
        assert service["restart"] == "unless-stopped"
        assert service["healthcheck"]["test"] == ["CMD", "gold-agent", "healthcheck", name]
        assert service["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
        assert "./config:/app/config:ro" in service["volumes"]


def test_only_the_dashboard_is_published_and_only_on_loopback() -> None:
    published = {name: s["ports"] for name, s in compose().items() if s.get("ports")}
    assert published == {"dashboard": ["127.0.0.1:8787:8787"]}
    assert "DASHBOARD_PASSWORD" in compose()["dashboard"]["environment"]


def test_secrets_stay_out_of_the_image_and_it_does_not_run_as_root() -> None:
    ignored = (PROJECT_ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    assert {".env", "*.session", "data/", "secrets/"} <= set(ignored)
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    users = [line.split()[1] for line in dockerfile if line.startswith("USER ")]
    assert users and users[-1] != "root"
