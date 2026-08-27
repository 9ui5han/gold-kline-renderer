import math
from typing import Any

from ..contracts import TEACHING_CANDLE_COUNT, candles_from_closes, closes, demo_ohlcv, event_anchor_valid, ohlc_series_valid, rsi, series_equal


def _rsi_closes(scenario_id: str) -> list[float]:
    result = []
    price = 100.0
    count = TEACHING_CANDLE_COUNT
    for index in range(count):
        if scenario_id == "overbought_reversal":
            base = 0.10 if index < 18 else 0.66 if index < 43 else -0.98 if index < 64 else -0.42
            phase = 0.0
        elif scenario_id == "oversold_recovery":
            base = -0.10 if index < 18 else -0.64 if index < 42 else 0.70 if index < 61 else 0.08
            phase = 0.7
        elif scenario_id == "worked_example":
            base = 0.16 if index < 17 else -0.72 if index < 44 else 0.82 if index < 65 else 0.14
            phase = 1.4
        elif scenario_id == "range_overview":
            base = math.sin(index / 8.0) * 0.22
            phase = 2.1
        else:
            raise ValueError(f"LESSON_GOAL_NOT_SUPPORTED:rsi:{scenario_id}")
        delta = base + math.sin(index * 1.73 + phase) * 0.46 + math.cos(index * 0.61 + phase) * 0.17
        price += delta
        result.append(round(price, 4))
    return result


def _crossing_signal(
    scenario_id: str,
    candles: list[dict[str, float]],
    values: list[float | None],
) -> dict[str, Any]:
    overbought = scenario_id == "overbought_reversal"
    threshold = 70 if overbought else 30
    usable = range(14, len(values))
    if overbought:
        extreme = max(usable, key=lambda index: values[index] or 0)
        cross = next(index for index in range(extreme + 1, len(values)) if values[index - 1] > 70 >= values[index])
        confirmation = next((
            index for index in range(cross + 1, min(cross + 8, len(candles)))
            if candles[index]["close"] < candles[index - 1]["low"]
        ), -1)
        signal_type = "rsi_overbought_reversal"
    else:
        extreme = min(usable, key=lambda index: values[index] if values[index] is not None else 101)
        cross = next(index for index in range(extreme + 1, len(values)) if values[index - 1] < 30 <= values[index])
        confirmation = next((
            index for index in range(cross + 1, min(cross + 8, len(candles)))
            if candles[index]["close"] > candles[index - 1]["high"]
        ), -1)
        signal_type = "rsi_oversold_recovery"
    if confirmation < 0:
        raise ValueError("LESSON_PRICE_CONFIRMATION_NOT_FOUND")
    return {
        "signal_type": signal_type,
        "indicator_candle_index": extreme,
        "cross_candle_index": cross,
        "confirmation_candle_index": confirmation,
        "indicator_value": round(float(values[extreme]), 2),
        "threshold": threshold,
    }


def _build_rsi(config: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    price_values = _rsi_closes(scenario_id)
    candles = candles_from_closes(price_values)
    values = rsi(price_values, int(config.get("parameters", {}).get("period", 14)))
    if scenario_id == "range_overview":
        signals = [{
            "signal_type": "rsi_range_overview",
            "levels": [30, 50, 70],
            "range_min": 0,
            "range_max": 100,
        }]
        layers = ["rsi_panel", "threshold_zones", "midline", "range_labels"]
    elif scenario_id == "worked_example":
        signal = _crossing_signal("oversold_recovery", candles, values)
        signals = [{
            **signal,
            "signal_type": "rsi_worked_example",
            "setup_candle_index": max(0, signal["indicator_candle_index"] - 3),
            "lesson_steps": [
                "Observe the RSI extreme",
                "Wait for RSI to cross back",
                "Check price confirmation",
            ],
        }]
        layers = ["candles", "rsi_panel", "threshold_zones", "signal_binding", "price_confirmation", "lesson_steps"]
    else:
        signals = [_crossing_signal(scenario_id, candles, values)]
        layers = ["candles", "rsi_panel", "threshold_zones", "signal_binding", "price_confirmation"]
    return {
        "indicator_id": "rsi",
        "indicator_family": "panel",
        "scenario_id": scenario_id,
        "ohlc": candles,
        "indicator_values": values,
        "signals": signals,
        "layers": layers,
    }


def _kdj(candles: list[dict[str, float]], period: int) -> dict[str, list[float | None]]:
    k_values: list[float | None] = [None] * len(candles)
    d_values: list[float | None] = [None] * len(candles)
    j_values: list[float | None] = [None] * len(candles)
    k = d = 50.0
    for index in range(period - 1, len(candles)):
        window = candles[index - period + 1:index + 1]
        low = min(item["low"] for item in window)
        high = max(item["high"] for item in window)
        rsv = 50.0 if high == low else (candles[index]["close"] - low) / (high - low) * 100
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
        k_values[index], d_values[index], j_values[index] = k, d, 3 * k - 2 * d
    return {"k": k_values, "d": d_values, "j": j_values}


def _build_kdj(config: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    candles = demo_ohlcv(seed=17 + len(scenario_id))
    period = int(config.get("parameters", {}).get("period", 9))
    indicator_values = _kdj(candles, period)
    k_values, d_values = indicator_values["k"], indicator_values["d"]
    index = next(i for i in range(period, len(candles)) if k_values[i] is not None and d_values[i] is not None)
    return {
        "indicator_id": "kdj", "indicator_family": "panel", "scenario_id": scenario_id,
        "ohlc": candles,
        "indicator_values": indicator_values,
        "signals": [{"signal_type": f"kdj_{scenario_id}", "event_index": index}],
        "layers": ["candles", "indicator_panel", "k_line", "d_line", "j_line", "signal_binding"],
    }


def build_scene(config: dict[str, Any], scenario_id: str, page: dict, route_payload: dict) -> dict[str, Any]:
    if config["indicator_id"] == "rsi":
        return _build_rsi(config, scenario_id)
    if config["indicator_id"] == "kdj":
        return _build_kdj(config, scenario_id)
    raise ValueError(f"INDICATOR_ALGORITHM_NOT_IMPLEMENTED:{config['indicator_id']}")


def validate_scene(scene: dict[str, Any], config: dict[str, Any]) -> bool:
    candles = scene.get("ohlc") or []
    if not ohlc_series_valid(candles):
        return False
    if not scene.get("signals"):
        return False
    if scene.get("indicator_id") != "rsi":
        values = scene.get("indicator_values") or {}
        expected = _kdj(candles, int(config.get("parameters", {}).get("period", 9)))
        return (
            event_anchor_valid(scene) and
            set(values) == set(expected) and
            all(series_equal(values[key], expected[key]) for key in expected)
        )
    supplied = scene.get("indicator_values") or []
    recalculated = rsi(closes(candles), int(config.get("parameters", {}).get("period", 14)))
    if len(supplied) != len(recalculated):
        return False
    for actual, expected in zip(supplied, recalculated):
        if actual is None or expected is None:
            if actual is not None or expected is not None:
                return False
        elif abs(float(actual) - float(expected)) > 1e-9:
            return False
    signal = scene["signals"][0]
    signal_type = signal.get("signal_type")
    if signal_type == "rsi_range_overview":
        return signal.get("levels") == [30, 50, 70] and "price_confirmation" not in scene.get("layers", [])
    if signal_type == "rsi_worked_example" and len(signal.get("lesson_steps") or []) != 3:
        return False
    extreme = signal.get("indicator_candle_index", -1)
    cross = signal.get("cross_candle_index", -1)
    confirmation = signal.get("confirmation_candle_index", -1)
    if not all(isinstance(index, int) and 0 <= index < len(candles) for index in (extreme, cross, confirmation)):
        return False
    if confirmation <= cross:
        return False
    threshold = signal.get("threshold")
    if threshold == 30:
        return bool(recalculated[extreme] < 30 <= recalculated[cross] and candles[confirmation]["close"] > candles[confirmation - 1]["high"])
    if threshold == 70:
        return bool(recalculated[extreme] > 70 >= recalculated[cross] and candles[confirmation]["close"] < candles[confirmation - 1]["low"])
    return False
