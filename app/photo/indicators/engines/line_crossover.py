from typing import Any

from ..contracts import closes, demo_ohlcv, ema, event_anchor_valid, series_equal


def build_scene(config: dict[str, Any], scenario_id: str, page: dict, route_payload: dict) -> dict[str, Any]:
    candles = demo_ohlcv(seed=29 + len(scenario_id), count=84)
    price_values = closes(candles)
    parameters = config.get("parameters", {})
    fast = ema(price_values, int(parameters.get("fast", 12)))
    slow = ema(price_values, int(parameters.get("slow", 26)))
    macd: list[float | None] = [
        None if left is None or right is None else left - right
        for left, right in zip(fast, slow)
    ]
    dense = [value if value is not None else 0.0 for value in macd]
    signal = ema(dense, int(parameters.get("signal", 9)))
    histogram = [
        None if left is None or right is None else left - right
        for left, right in zip(macd, signal)
    ]
    event_index = next(
        index for index in range(27, len(candles))
        if macd[index] is not None and signal[index] is not None
    )
    return {
        "indicator_id": config["indicator_id"], "indicator_family": "panel", "scenario_id": scenario_id,
        "ohlc": candles,
        "indicator_values": {"main": macd, "signal": signal, "histogram": histogram},
        "signals": [{"signal_type": f"macd_{scenario_id}", "event_index": event_index}],
        "layers": ["candles", "indicator_panel", "main_line", "signal_line", "histogram", "signal_binding"],
    }


def validate_scene(scene: dict[str, Any], config: dict[str, Any]) -> bool:
    candles = scene.get("ohlc") or []
    values = scene.get("indicator_values") or {}
    if len(candles) < 40 or not event_anchor_valid(scene):
        return False
    parameters = config.get("parameters", {})
    price_values = closes(candles)
    fast = ema(price_values, int(parameters.get("fast", 12)))
    slow = ema(price_values, int(parameters.get("slow", 26)))
    expected_main = [None if left is None or right is None else left - right for left, right in zip(fast, slow)]
    expected_signal = ema([value if value is not None else 0.0 for value in expected_main], int(parameters.get("signal", 9)))
    expected_histogram = [None if left is None or right is None else left - right for left, right in zip(expected_main, expected_signal)]
    return (
        series_equal(values.get("main") or [], expected_main) and
        series_equal(values.get("signal") or [], expected_signal) and
        series_equal(values.get("histogram") or [], expected_histogram)
    )
