"""Gold driver knowledge base (config/gold_drivers.yaml) for rules and the cached LLM prompt."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from gold_agent.config import CONFIG_DIR

DEFAULT_HALF_LIFE_MINUTES = 120.0


class DriverKB:
    def __init__(self, data: dict[str, Any]) -> None:
        self.drivers: list[dict[str, Any]] = list(data.get("drivers", []))
        self._half_life: dict[str, float] = {}
        for driver in self.drivers:
            category = str(driver.get("category"))
            half_life = float(driver.get("half_life_minutes", DEFAULT_HALF_LIFE_MINUTES))
            # When several drivers share a category keep the shortest: news decays fast.
            self._half_life[category] = min(half_life, self._half_life.get(category, half_life))

    @classmethod
    def from_file(cls, path: Path = CONFIG_DIR / "gold_drivers.yaml") -> DriverKB:
        with path.open(encoding="utf-8") as handle:
            return cls(yaml.safe_load(handle))

    def half_life(self, category: str) -> float:
        return self._half_life.get(category, DEFAULT_HALF_LIFE_MINUTES)

    def as_prompt_text(self) -> str:
        """Compact, stable rendering for the cached system prompt (stable order = cache hits)."""
        lines = [
            "Basis pengetahuan pendorong harga emas (efek tipikal jangka pendek jika MENGEJUTKAN):"
        ]
        for driver in self.drivers:
            effects = ", ".join(
                f"{key.removeprefix('when_')}={value}"
                for key, value in driver.items()
                if key.startswith("when_")
            )
            weight = f"; bobot {driver['weight']}" if "weight" in driver else ""
            lines.append(
                f"- {driver['id']} [{driver['category']}]: {effects or 'campuran'}; "
                f"half-life ~{driver.get('half_life_minutes', DEFAULT_HALF_LIFE_MINUTES)} mnt"
                f"{weight}. "
                f"{driver.get('note', '')}".rstrip()
            )
        lines.append(
            "Aturan inti: reaksi pasar ditentukan oleh kejutan terhadap ekspektasi/konsensus, "
            "bukan oleh headline mentah."
        )
        return "\n".join(lines)
