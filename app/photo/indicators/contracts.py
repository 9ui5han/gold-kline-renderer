import hashlib
import json
import math
from typing import Any


ENGINE_VERSION = "indicator-teaching-v1"
CANDLE_BODY_SCALE = 1.55


def demo_ohlcv(seed: int = 0, count: int = 72) -> list[dict[str, float]]:
    closes = []
    price = 100.0 + seed * 0.07
    for index in range(count):
        phase = seed * 0.31
        drift = 0.10 + math.sin((index + seed) / 13.0) * 0.08
        delta = drift + math.sin(index * 0.61 + phase) * 0.58 + math.cos(index * 0.19 + phase) * 0.24
        price += delta
        closes.append(round(price, 4))
    candles = candles_from_closes(closes)
    for index, candle in enumerate(candles):
        candle["volume"] = float(900 + ((index * 137 + seed * 83) % 850))
    return candles


def candles_from_closes(closes: list[float]) -> list[dict[str, float]]:
    candles = []
    previous = closes[0] - 0.35
    for index, close in enumerate(closes):
        movement = close - previous
        open_price = close - movement * CANDLE_BODY_SCALE
        body_size = abs(close - open_price)
        wick = max(0.24, min(0.58, body_size * 0.42 + (index % 3) * 0.04))
        candles.append({
            "open": round(open_price, 4),
            "high": round(max(open_price, close) + wick, 4),
            "low": round(min(open_price, close) - wick * 0.9, 4),
            "close": round(close, 4),
        })
        previous = close
    return candles


def closes(candles: list[dict[str, float]]) -> list[float]:
    return [float(item["close"]) for item in candles]


def sma(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        result[index] = sum(values[index - period + 1:index + 1]) / period
    return result


def ema(values: list[float], period: int) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    multiplier = 2.0 / (period + 1)
    for index in range(period, len(values)):
        current = (values[index] - current) * multiplier + current
        result[index] = current
    return result


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains = [max(0.0, values[index] - values[index - 1]) for index in range(1, len(values))]
    losses = [max(0.0, values[index - 1] - values[index]) for index in range(1, len(values))]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    def current_value() -> float:
        if average_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + average_gain / average_loss)

    result[period] = current_value()
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
        average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        result[index] = current_value()
    return result


def fingerprint(scene: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "indicator_id": scene.get("indicator_id"),
            "scenario_id": scene.get("scenario_id"),
            "ohlc": scene.get("ohlc"),
            "indicator_values": scene.get("indicator_values"),
            "signals": scene.get("signals"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def series_equal(actual: list[Any], expected: list[Any], tolerance: float = 1e-9) -> bool:
    if len(actual) != len(expected):
        return False
    for left, right in zip(actual, expected):
        if left is None or right is None:
            if left is not None or right is not None:
                return False
        elif abs(float(left) - float(right)) > tolerance:
            return False
    return True


def event_anchor_valid(scene: dict[str, Any]) -> bool:
    candles = scene.get("ohlc") or []
    signals = scene.get("signals") or []
    if not candles or not signals:
        return False
    event_index = signals[0].get("event_index")
    return isinstance(event_index, int) and 0 <= event_index < len(candles)


def finalize_scene(scene: dict[str, Any], engine_id: str, valid: bool) -> dict[str, Any]:
    scene["engine_id"] = engine_id
    scene["engine_version"] = ENGINE_VERSION
    scene["signal_contract_valid"] = bool(valid)
    scene["data_fingerprint"] = fingerprint(scene)
    if not scene["signal_contract_valid"]:
        raise ValueError("TEACHING_SIGNAL_VALIDATION_FAILED")
    return scene
