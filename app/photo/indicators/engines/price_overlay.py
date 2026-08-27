import math
from typing import Any

from ..contracts import closes, demo_ohlcv, ema, event_anchor_valid, series_equal, sma


def _bollinger(values: list[float], period: int, deviation: float) -> dict[str, list[float | None]]:
    middle = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1:index + 1]
        mean = middle[index]
        variance = sum((item - mean) ** 2 for item in window) / period
        width = math.sqrt(variance) * deviation
        upper[index], lower[index] = mean + width, mean - width
    return {"upper": upper, "middle": middle, "lower": lower}


def build_scene(config: dict[str, Any], scenario_id: str, page: dict, route_payload: dict) -> dict[str, Any]:
    candles = demo_ohlcv(seed=41 + len(scenario_id), count=84)
    values = closes(candles)
    parameters = config.get("parameters", {})
    if config["indicator_id"] == "bollinger":
        indicator_values = _bollinger(values, int(parameters.get("period", 20)), float(parameters.get("deviation", 2.0)))
        layers = ["candles", "upper_band", "middle_band", "lower_band", "signal_binding"]
    else:
        periods = list(parameters.get("periods", [20, 50]))
        method = str(parameters.get("method", "ema"))
        calculator = ema if method == "ema" else sma
        indicator_values = {f"line_{period}": calculator(values, int(period)) for period in periods}
        layers = ["candles", "fast_line", "slow_line", "signal_binding"]
    event_index = max(20, len(candles) - 15)
    return {
        "indicator_id": config["indicator_id"], "indicator_family": "overlay", "scenario_id": scenario_id,
        "ohlc": candles, "indicator_values": indicator_values,
        "signals": [{"signal_type": f"{config['indicator_id']}_{scenario_id}", "event_index": event_index}],
        "layers": layers,
    }


def validate_scene(scene: dict[str, Any], config: dict[str, Any]) -> bool:
    candles = scene.get("ohlc") or []
    values = scene.get("indicator_values") or {}
    if len(candles) < 40 or not values or not event_anchor_valid(scene):
        return False
    price_values = closes(candles)
    parameters = config.get("parameters", {})
    if config["indicator_id"] == "bollinger":
        expected = _bollinger(price_values, int(parameters.get("period", 20)), float(parameters.get("deviation", 2.0)))
    else:
        calculator = ema if str(parameters.get("method", "ema")) == "ema" else sma
        expected = {
            f"line_{period}": calculator(price_values, int(period))
            for period in list(parameters.get("periods", [20, 50]))
        }
    return set(values) == set(expected) and all(series_equal(values[key], expected[key]) for key in expected)
