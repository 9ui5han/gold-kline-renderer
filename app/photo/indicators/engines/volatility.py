from typing import Any

from ..contracts import demo_ohlcv, event_anchor_valid, ohlc_series_valid, series_equal


def _atr(candles: list[dict[str, float]], period: int) -> list[float | None]:
    ranges = []
    for index, candle in enumerate(candles):
        previous_close = candles[index - 1]["close"] if index else candle["close"]
        ranges.append(max(
            candle["high"] - candle["low"],
            abs(candle["high"] - previous_close),
            abs(candle["low"] - previous_close),
        ))
    result: list[float | None] = [None] * len(candles)
    if len(candles) < period:
        return result
    current = sum(ranges[:period]) / period
    result[period - 1] = current
    for index in range(period, len(candles)):
        current = (current * (period - 1) + ranges[index]) / period
        result[index] = current
    return result


def build_scene(config: dict[str, Any], scenario_id: str, page: dict, route_payload: dict) -> dict[str, Any]:
    candles = demo_ohlcv(seed=53 + len(scenario_id))
    values = _atr(candles, int(config.get("parameters", {}).get("period", 14)))
    event_index = next(index for index, value in enumerate(values) if value is not None)
    return {
        "indicator_id": config["indicator_id"], "indicator_family": "panel", "scenario_id": scenario_id,
        "ohlc": candles, "indicator_values": values,
        "signals": [{"signal_type": f"atr_{scenario_id}", "event_index": event_index}],
        "layers": ["candles", "indicator_panel", "atr_line", "volatility_zone", "signal_binding"],
    }


def validate_scene(scene: dict[str, Any], config: dict[str, Any]) -> bool:
    candles = scene.get("ohlc") or []
    values = scene.get("indicator_values") or []
    if not ohlc_series_valid(candles):
        return False
    expected = _atr(candles, int(config.get("parameters", {}).get("period", 14)))
    return event_anchor_valid(scene) and series_equal(values, expected)
