import math
from typing import Any

from ..contracts import candles_from_closes


def build_scene(config: dict[str, Any], scenario_id: str, page: dict, route_payload: dict) -> dict[str, Any]:
    price_values = [100 + index * 0.11 + math.sin(index / 2.8) * 1.4 for index in range(60)]
    candles = candles_from_closes(price_values)
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
            if "price_low" in item and "price_high" in item:
                old_low, old_high = item["price_low"], item["price_high"]
                item["price_low"], item["price_high"] = round(pivot - old_high, 4), round(pivot - old_low, 4)
            if "price" in item:
                item["price"] = round(pivot - item["price"], 4)
            transformed.append(item)
        signals = transformed
    return {
        "indicator_id": config["indicator_id"], "indicator_family": "price_structure", "scenario_id": scenario_id,
        "ohlc": candles, "indicator_values": [], "signals": signals,
        "layers": ["candles", "liquidity", "order_block", "fair_value_gap", "break_of_structure", "retest"],
    }


def validate_scene(scene: dict[str, Any], config: dict[str, Any]) -> bool:
    candles = scene.get("ohlc") or []
    signals = scene.get("signals") or []
    by_type = {item.get("signal_type"): item for item in signals}
    bearish = "bearish_order_block" in by_type
    order = by_type.get("bearish_order_block" if bearish else "bullish_order_block")
    sweep, bos, fvg = by_type.get("liquidity_sweep"), by_type.get("break_of_structure"), by_type.get("fair_value_gap")
    if len(candles) < 40 or not all(isinstance(item, dict) for item in (order, sweep, bos, fvg)):
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
        zones = (
            abs(order["price_low"] - candles[order["zone_start_index"]]["low"]) < 1e-9 and
            abs(order["price_high"] - candles[order["zone_start_index"]]["high"]) < 1e-9 and
            abs(fvg["price_low"] - third["high"]) < 1e-9 and
            abs(fvg["price_high"] - first["low"]) < 1e-9
        )
        anchors = abs(sweep["price"] - sweep_ref["high"]) < .011 and abs(bos["price"] - bos_ref["low"]) < .011
        return bool(anchors and zones and sweep_event["high"] > sweep_ref["high"] > sweep_event["close"] and bos_event["close"] < bos_ref["low"] and third["high"] < first["low"] and in_zone)
    zones = (
        abs(order["price_low"] - candles[order["zone_start_index"]]["low"]) < 1e-9 and
        abs(order["price_high"] - candles[order["zone_start_index"]]["high"]) < 1e-9 and
        abs(fvg["price_low"] - first["high"]) < 1e-9 and
        abs(fvg["price_high"] - third["low"]) < 1e-9
    )
    anchors = abs(sweep["price"] - sweep_ref["low"]) < .011 and abs(bos["price"] - bos_ref["high"]) < .011
    return bool(anchors and zones and sweep_event["low"] < sweep_ref["low"] < sweep_event["close"] and bos_event["close"] > bos_ref["high"] and third["low"] > first["high"] and in_zone)
