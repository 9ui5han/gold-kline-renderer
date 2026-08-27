from typing import Any

from ..contracts import demo_ohlcv, event_anchor_valid, series_equal


def build_scene(config: dict[str, Any], scenario_id: str, page: dict, route_payload: dict) -> dict[str, Any]:
    candles = demo_ohlcv(seed=67 + len(scenario_id))
    obv = [0.0]
    for index in range(1, len(candles)):
        direction = 1 if candles[index]["close"] > candles[index - 1]["close"] else -1 if candles[index]["close"] < candles[index - 1]["close"] else 0
        obv.append(obv[-1] + direction * candles[index]["volume"])
    event_index = len(candles) - 12
    return {
        "indicator_id": config["indicator_id"], "indicator_family": "panel", "scenario_id": scenario_id,
        "ohlc": candles, "indicator_values": obv,
        "signals": [{"signal_type": f"obv_{scenario_id}", "event_index": event_index}],
        "layers": ["candles", "volume_bars", "obv_line", "signal_binding"],
    }


def validate_scene(scene: dict[str, Any], config: dict[str, Any]) -> bool:
    candles = scene.get("ohlc") or []
    values = scene.get("indicator_values") or []
    if len(candles) < 40 or not event_anchor_valid(scene) or not all("volume" in item for item in candles):
        return False
    expected = [0.0]
    for index in range(1, len(candles)):
        direction = 1 if candles[index]["close"] > candles[index - 1]["close"] else -1 if candles[index]["close"] < candles[index - 1]["close"] else 0
        expected.append(expected[-1] + direction * candles[index]["volume"])
    return series_equal(values, expected)
