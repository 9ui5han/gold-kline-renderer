import hashlib
import json
import math
import re
from typing import Any


ENGINE_VERSION = "indicator-teaching-v1"
CANDLE_BODY_SCALE = 1.55

LESSON_GOAL_MAP = {
    "rsi": {
        "overview": "range_overview",
        "state_a": "overbought_reversal",
        "state_b": "oversold_recovery",
        "components": "range_overview",
        "setup": "worked_example",
        "worked_example": "worked_example",
    },
    "ict": {
        "overview": "bullish_order_block",
        "state_a": "bullish_order_block",
        "state_b": "bearish_order_block",
        "components": "bullish_fvg",
        "setup": "bullish_liquidity_sweep",
        "worked_example": "bullish_bos",
    },
}

LEGACY_LESSON_GOALS = {
    "rsi": {
        "range_overview", "oversold_recovery", "overbought_reversal", "worked_example",
    },
    "ict": {
        "bullish_order_block", "bearish_order_block",
        "bullish_liquidity_sweep", "bearish_liquidity_sweep",
        "bullish_fvg", "bearish_fvg", "bullish_bos", "bearish_bos",
    },
}


def _resolve_lesson_goal(indicator_id: str, lesson_goal: str) -> str:
    normalized = str(lesson_goal or "").strip().lower()
    mapped = LESSON_GOAL_MAP.get(indicator_id, {}).get(normalized)
    if mapped:
        return mapped
    if normalized in LEGACY_LESSON_GOALS.get(indicator_id, set()):
        return normalized
    raise ValueError(f"LESSON_GOAL_NOT_SUPPORTED:{indicator_id}:{normalized}")


def identify_indicator(topic_text: str, visual_focus: str = "") -> str:
    text = f"{topic_text} {visual_focus}".lower()
    if re.search(r"(?<![a-z])ict(?![a-z])|order block|\bfvg\b|\bbos\b|\bchoch\b|liquidity sweep", text):
        return "ict"
    if re.search(r"(?<![a-z])rsi(?![a-z])|relative strength index|overbought|oversold|below 30|above 70", text):
        return "rsi"
    return "generic"


def _scenario(indicator_id: str, focus: str) -> str:
    text = str(focus or "").lower()
    if indicator_id == "ict":
        return "bearish_order_block" if "bear" in text else "bullish_order_block"
    if indicator_id == "rsi":
        if "0 to 100" in text or "0-100" in text or "scale" in text or "range" in text:
            return "range_overview"
        return "overbought_reversal" if "overbought" in text or "above 70" in text else "oversold_recovery"
    return "price_context"


def _rsi(values: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return result
    gains = [max(0.0, values[index] - values[index - 1]) for index in range(1, len(values))]
    losses = [max(0.0, values[index - 1] - values[index]) for index in range(1, len(values))]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    def value() -> float:
        if average_loss == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + average_gain / average_loss)

    result[period] = value()
    for index in range(period + 1, len(values)):
        average_gain = (average_gain * (period - 1) + gains[index - 1]) / period
        average_loss = (average_loss * (period - 1) + losses[index - 1]) / period
        result[index] = value()
    return result


def _candles_from_closes(closes: list[float]) -> list[dict[str, float]]:
    candles = []
    previous = closes[0] - 0.35
    for index, close in enumerate(closes):
        movement = close - previous
        # Preserve the authoritative close series used by each indicator while
        # making the candle body easier to read in a 1080px teaching graphic.
        # The small opening gap is intentional and remains proportional to the
        # actual close-to-close movement instead of inventing a new direction.
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


def _rsi_scene(scenario_id: str) -> dict[str, Any]:
    closes = []
    price = 100.0
    for index in range(72):
        if scenario_id == "overbought_reversal":
            base = 0.12 if index < 20 else 0.62 if index < 43 else -0.95 if index < 64 else -0.52
        else:
            base = -0.08 if index < 18 else -0.62 if index < 42 else 0.68 if index < 60 else 0.05
        delta = base + math.sin(index * 1.73) * 0.48 + math.cos(index * .61) * 0.18
        price += delta
        closes.append(round(price, 4))
    candles = _candles_from_closes(closes)
    values = _rsi(closes)
    if scenario_id == "overbought_reversal":
        extreme = max(range(14, len(values)), key=lambda index: values[index] or 0)
        cross = next(index for index in range(extreme + 1, len(values)) if values[index - 1] is not None and values[index - 1] > 70 >= values[index])
        signal_type, threshold = "rsi_overbought_reversal", 70
    else:
        extreme = min(range(14, len(values)), key=lambda index: values[index] if values[index] is not None else 101)
        cross = next(index for index in range(extreme + 1, len(values)) if values[index - 1] is not None and values[index - 1] < 30 <= values[index])
        signal_type, threshold = "rsi_oversold_recovery", 30
    if threshold == 30:
        confirmation = next((
            index for index in range(cross + 1, min(cross + 7, len(candles)))
            if candles[index]["close"] > candles[index - 1]["high"]
        ), -1)
    else:
        confirmation = next((
            index for index in range(cross + 1, min(cross + 7, len(candles)))
            if candles[index]["close"] < candles[index - 1]["low"]
        ), -1)
    if confirmation < 0:
        raise ValueError("LESSON_PRICE_CONFIRMATION_NOT_FOUND")
    signals = [{
        "signal_type": signal_type,
        "indicator_candle_index": extreme,
        "cross_candle_index": cross,
        "confirmation_candle_index": confirmation,
        "indicator_value": round(float(values[extreme]), 2),
        "threshold": threshold,
    }]
    valid = (
        values[extreme] is not None and
        (values[extreme] < 30 <= values[cross] if threshold == 30 else values[extreme] > 70 >= values[cross]) and
        confirmation > cross and
        (
            candles[confirmation]["close"] > candles[confirmation - 1]["high"]
            if threshold == 30 else
            candles[confirmation]["close"] < candles[confirmation - 1]["low"]
        )
    )
    return {
        "indicator_id": "rsi", "indicator_family": "panel", "scenario_id": scenario_id,
        "ohlc": candles, "indicator_values": values, "signals": signals,
        "layers": ["candles", "rsi_panel", "threshold_zones", "signal_binding", "price_confirmation"],
        "signal_contract_valid": bool(valid),
    }


def _ict_scene(scenario_id: str) -> dict[str, Any]:
    closes = [100 + index * 0.11 + math.sin(index / 2.8) * 1.4 for index in range(60)]
    candles = _candles_from_closes(closes)
    prior_low_index = min(range(10, 20), key=lambda index: candles[index]["low"])
    prior_high_index = max(range(10, 23), key=lambda index: candles[index]["high"])
    prior_low = candles[prior_low_index]["low"]
    prior_high = candles[prior_high_index]["high"]
    candles[20] = {"open": prior_low + 1.2, "high": prior_low + 1.8, "low": prior_low - 1.5, "close": prior_low + 0.7}
    candles[23] = {"open": 103.0, "high": 103.4, "low": 99.4, "close": 100.0}
    candles[24] = {"open": 100.0, "high": 107.0, "low": 99.8, "close": 106.5}
    candles[25] = {"open": 106.5, "high": max(111.0, prior_high + 2.0), "low": 105.7, "close": max(110.2, prior_high + 1.2)}
    candles[30] = {"open": 102.0, "high": 104.1, "low": 100.4, "close": 103.7}
    signals = [
        {"signal_type": "liquidity_sweep", "event_index": 20, "reference_index": prior_low_index, "price": round(prior_low, 2)},
        {"signal_type": "bullish_order_block", "zone_start_index": 23, "zone_end_index": 30, "price_low": 99.4, "price_high": 103.4, "retest_index": 30},
        {"signal_type": "break_of_structure", "event_index": 25, "reference_index": prior_high_index, "price": round(prior_high, 2)},
        {"signal_type": "fair_value_gap", "zone_start_index": 23, "zone_end_index": 25, "price_low": 103.4, "price_high": 105.7},
    ]
    if scenario_id.startswith("bearish_"):
        pivot = 205.0
        candles = [{
            "open": round(pivot - item["open"], 4),
            "high": round(pivot - item["low"], 4),
            "low": round(pivot - item["high"], 4),
            "close": round(pivot - item["close"], 4),
        } for item in candles]
        transformed = []
        for signal in signals:
            item = dict(signal)
            if item["signal_type"] == "bullish_order_block":
                item["signal_type"] = "bearish_order_block"
            for low_key, high_key in (("price_low", "price_high"),):
                if low_key in item and high_key in item:
                    old_low, old_high = item[low_key], item[high_key]
                    item[low_key], item[high_key] = round(pivot - old_high, 4), round(pivot - old_low, 4)
            if "price" in item:
                item["price"] = round(pivot - item["price"], 4)
            transformed.append(item)
        signals = transformed
    return {
        "indicator_id": "ict", "indicator_family": "price_structure", "scenario_id": scenario_id,
        "ohlc": candles, "indicator_values": [], "signals": signals,
        "layers": ["candles", "liquidity", "order_block", "fair_value_gap", "break_of_structure", "retest"],
        "signal_contract_valid": True,
    }


def validate_teaching_scene(scene: dict[str, Any]) -> bool:
    candles = scene.get("ohlc") or []
    signals = scene.get("signals") or []
    if len(candles) < 40 or not signals:
        return False
    if scene.get("indicator_id") == "rsi":
        supplied_values = scene.get("indicator_values") or []
        recalculated_values = _rsi([float(item["close"]) for item in candles])
        if len(supplied_values) != len(recalculated_values):
            return False
        for supplied, recalculated in zip(supplied_values, recalculated_values):
            if supplied is None or recalculated is None:
                if supplied is not None or recalculated is not None:
                    return False
            elif abs(float(supplied) - float(recalculated)) > 1e-9:
                return False
        values = recalculated_values
        signal = signals[0]
        extreme = signal.get("indicator_candle_index", -1)
        cross = signal.get("cross_candle_index", -1)
        confirmation = signal.get("confirmation_candle_index", -1)
        if not all(isinstance(index, int) and 0 <= index < len(candles) for index in (extreme, cross, confirmation)):
            return False
        threshold = signal.get("threshold")
        if confirmation <= cross:
            return False
        if threshold == 30:
            return bool(values[extreme] < 30 <= values[cross] and candles[confirmation]["close"] > candles[confirmation - 1]["high"])
        if threshold == 70:
            return bool(values[extreme] > 70 >= values[cross] and candles[confirmation]["close"] < candles[confirmation - 1]["low"])
        return False
    if scene.get("indicator_id") == "ict":
        by_type = {item.get("signal_type"): item for item in signals}
        bearish = "bearish_order_block" in by_type
        order = by_type.get("bearish_order_block" if bearish else "bullish_order_block")
        sweep, bos, fvg = by_type.get("liquidity_sweep"), by_type.get("break_of_structure"), by_type.get("fair_value_gap")
        if not all(isinstance(item, dict) for item in (order, sweep, bos, fvg)):
            return False
        try:
            sweep_event, sweep_ref = candles[sweep["event_index"]], candles[sweep["reference_index"]]
            bos_event, bos_ref = candles[bos["event_index"]], candles[bos["reference_index"]]
            first, third = candles[fvg["zone_start_index"]], candles[fvg["zone_end_index"]]
            retest = candles[order["retest_index"]]
        except (IndexError, KeyError, TypeError):
            return False
        in_zone = order["price_low"] <= (retest["high"] if bearish else retest["low"]) <= order["price_high"]
        indices_valid = (
            sweep["reference_index"] < sweep["event_index"] < order["zone_start_index"] <
            bos["event_index"] <= order["zone_end_index"] == order["retest_index"] and
            fvg["zone_start_index"] < fvg["zone_end_index"]
        )
        if not indices_valid:
            return False
        if bearish:
            zone_values_valid = (
                abs(order["price_low"] - candles[order["zone_start_index"]]["low"]) < 1e-9 and
                abs(order["price_high"] - candles[order["zone_start_index"]]["high"]) < 1e-9 and
                abs(fvg["price_low"] - third["high"]) < 1e-9 and
                abs(fvg["price_high"] - first["low"]) < 1e-9
            )
            anchors_valid = abs(sweep["price"] - sweep_ref["high"]) < .011 and abs(bos["price"] - bos_ref["low"]) < .011
            return bool(anchors_valid and zone_values_valid and sweep_event["high"] > sweep_ref["high"] > sweep_event["close"] and bos_event["close"] < bos_ref["low"] and third["high"] < first["low"] and in_zone)
        zone_values_valid = (
            abs(order["price_low"] - candles[order["zone_start_index"]]["low"]) < 1e-9 and
            abs(order["price_high"] - candles[order["zone_start_index"]]["high"]) < 1e-9 and
            abs(fvg["price_low"] - first["high"]) < 1e-9 and
            abs(fvg["price_high"] - third["low"]) < 1e-9
        )
        anchors_valid = abs(sweep["price"] - sweep_ref["low"]) < .011 and abs(bos["price"] - bos_ref["high"]) < .011
        return bool(anchors_valid and zone_values_valid and sweep_event["low"] < sweep_ref["low"] < sweep_event["close"] and bos_event["close"] > bos_ref["high"] and third["low"] > first["high"] and in_zone)
    return False


def build_teaching_scene(indicator_id: str, scenario_id: str = "") -> dict[str, Any]:
    resolved_scenario = scenario_id or _scenario(indicator_id, "")
    if indicator_id == "ict":
        scene = _ict_scene(resolved_scenario)
    elif indicator_id == "rsi":
        scene = _rsi_scene(resolved_scenario)
    else:
        scene = _rsi_scene("oversold_recovery")
        scene["indicator_id"] = "generic"
        scene["scenario_id"] = "price_context"
    payload = json.dumps({"ohlc": scene["ohlc"], "indicator_values": scene["indicator_values"]}, sort_keys=True, separators=(",", ":"))
    scene["engine_version"] = ENGINE_VERSION
    scene["data_fingerprint"] = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    scene["signal_contract_valid"] = validate_teaching_scene(scene)
    if not scene["signal_contract_valid"]:
        raise ValueError("TEACHING_SIGNAL_VALIDATION_FAILED")
    return scene


def resolve_teaching_scene(page: dict[str, Any], route_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route_payload or {}
    teaching_spec = page.get("teaching_spec") if isinstance(page.get("teaching_spec"), dict) else {}
    indicator_id = str(teaching_spec.get("indicator_id") or "").strip().lower()
    if indicator_id in {"rsi", "rsi_14"}:
        indicator_id = "rsi"
    elif indicator_id in {"ict", "ict_structure"}:
        indicator_id = "ict"
    if not indicator_id:
        focus = f"{page.get('visual_focus') or ''} {' '.join(str(item) for item in page.get('required_elements') or [])}"
        indicator_id = identify_indicator(str(route.get("topic_text") or ""), focus)
    if teaching_spec and indicator_id not in {"rsi", "ict"}:
        raise ValueError(f"INDICATOR_PLUGIN_NOT_SUPPORTED:{indicator_id}")
    if not teaching_spec and indicator_id == "generic" and str(page.get("visual_type") or "") in {
        "indicator_panel", "zone_diagram", "candlestick_demo", "market_chart",
    }:
        raise ValueError("INDICATOR_TOPIC_NOT_RECOGNIZED")
    if teaching_spec:
        scenario_id = _resolve_lesson_goal(
            indicator_id,
            str(teaching_spec.get("lesson_goal") or ""),
        )
    else:
        scenario_id = _scenario(indicator_id, str(page.get("visual_focus") or ""))
    if not teaching_spec and indicator_id == "rsi" and str(page.get("visual_type") or "") == "candlestick_demo":
        scenario_id = "worked_example"
    return build_teaching_scene(indicator_id, scenario_id)
