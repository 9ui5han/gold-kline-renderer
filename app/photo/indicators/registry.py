import json
from pathlib import Path
from typing import Any, Callable

from .contracts import finalize_scene
from .engines import (
    build_line_crossover, build_price_overlay, build_price_structure,
    build_range_oscillator, build_volatility, build_volume,
    validate_line_crossover, validate_price_overlay, validate_price_structure,
    validate_range_oscillator, validate_volatility, validate_volume,
)


Builder = Callable[[dict[str, Any], str, dict, dict], dict[str, Any]]
Validator = Callable[[dict[str, Any], dict[str, Any]], bool]

ENGINE_REGISTRY: dict[str, tuple[Builder, Validator]] = {
    "range_oscillator": (build_range_oscillator, validate_range_oscillator),
    "line_crossover": (build_line_crossover, validate_line_crossover),
    "price_overlay": (build_price_overlay, validate_price_overlay),
    "volatility": (build_volatility, validate_volatility),
    "volume": (build_volume, validate_volume),
    "price_structure": (build_price_structure, validate_price_structure),
}

ALIASES = {
    "rsi_14": "rsi",
    "ict_structure": "ict",
    "sma": "moving_average",
    "ema": "moving_average",
    "bb": "bollinger",
}


class IndicatorRegistry:
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self._configs: dict[str, dict[str, Any]] = {}
        for path in sorted(config_dir.glob("*.json")):
            config = json.loads(path.read_text(encoding="utf-8"))
            indicator_id = str(config.get("indicator_id") or "").strip().lower()
            if not indicator_id:
                raise ValueError(f"INDICATOR_CONFIG_ID_MISSING:{path.name}")
            self._configs[indicator_id] = config

    @classmethod
    def default(cls) -> "IndicatorRegistry":
        return cls(Path(__file__).resolve().parent / "configs")

    def normalize_id(self, indicator_id: str) -> str:
        normalized = str(indicator_id or "").strip().lower()
        return ALIASES.get(normalized, normalized)

    def get_config(self, indicator_id: str) -> dict[str, Any]:
        normalized = self.normalize_id(indicator_id)
        config = self._configs.get(normalized)
        if config is None:
            raise ValueError(f"INDICATOR_NOT_REGISTERED:{normalized}")
        return config

    def resolve_scenario(self, config: dict[str, Any], lesson_goal: str) -> str:
        normalized = str(lesson_goal or "").strip().lower()
        mapped = (config.get("goal_map") or {}).get(normalized)
        if mapped:
            return str(mapped)
        if normalized in set(config.get("legacy_goals") or []):
            return normalized
        raise ValueError(f"LESSON_GOAL_NOT_SUPPORTED:{config['indicator_id']}:{normalized}")

    def build_scene(
        self,
        indicator_id: str,
        lesson_goal: str,
        page: dict[str, Any],
        route_payload: dict[str, Any],
    ) -> dict[str, Any]:
        config = self.get_config(indicator_id)
        teaching_spec = page.get("teaching_spec") if isinstance(page.get("teaching_spec"), dict) else {}
        supplied_kind = str(teaching_spec.get("indicator_kind") or "").strip()
        if supplied_kind and supplied_kind != config["indicator_kind"]:
            raise ValueError("INDICATOR_KIND_MISMATCH")
        engine_id = str(config.get("engine_id") or "")
        engine = ENGINE_REGISTRY.get(engine_id)
        if engine is None:
            raise ValueError(f"ENGINE_NOT_REGISTERED:{engine_id}")
        scenario_id = self.resolve_scenario(config, lesson_goal)
        builder, validator = engine
        scene = builder(config, scenario_id, page, route_payload)
        return finalize_scene(scene, engine_id, validator(scene, config))

    def validate_scene(self, scene: dict[str, Any]) -> bool:
        try:
            config = self.get_config(str(scene.get("indicator_id") or ""))
        except ValueError:
            return False
        engine = ENGINE_REGISTRY.get(str(scene.get("engine_id") or config.get("engine_id") or ""))
        return bool(engine and engine[1](scene, config))
