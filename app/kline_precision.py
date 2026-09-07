"""Canonical two-decimal precision for K-line market values.

This module deliberately leaves timing, counters, retry state, and provider
settings untouched.  Only prices, candle fields, and derived K-line indicators
are rounded at workflow boundaries.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
import re
from typing import Any


KLINE_NUMBER_FIELDS = frozenset({
    "open", "high", "low", "close", "volume",
    "last_close", "current_price", "price", "price_low", "price_high",
    "center", "zone_low", "zone_high", "resolved_value",
    "ema20", "ema50", "atr14", "atr_primary", "rsi14", "vwap",
    "incoming_move", "reaction_move", "min_move_threshold",
    "price_tolerance", "distance", "reachable_distance", "indicator_value",
    "lower", "upper", "midpoint",
})

KLINE_NUMBER_MAP_FIELDS = frozenset({
    "authoritative_price_map",
    "visual_anchors",
})

KLINE_NARRATIVE_FIELDS = frozenset({
    "technical_summary",
    "content_goal",
    "condition",
    "invalidation",
    "reason",
})

DECIMAL_TOKEN = re.compile(r"(?<![A-Za-z0-9_])-?\d+\.\d+(?![A-Za-z0-9_])")


def _round_two(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if not isinstance(value, (int, float, Decimal)):
        return value
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _round_decimal_tokens(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = Decimal(match.group(0)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        return format(value, "f")

    return DECIMAL_TOKEN.sub(replace, text)


def normalize_kline_numbers(value: Any) -> Any:
    """Deep-copy a workflow value and round only K-line numeric fields."""
    if isinstance(value, list):
        return [normalize_kline_numbers(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, raw in value.items():
        if key in KLINE_NUMBER_MAP_FIELDS and isinstance(raw, dict):
            normalized[key] = {
                map_key: _round_two(map_value)
                for map_key, map_value in raw.items()
            }
        elif key in KLINE_NUMBER_FIELDS:
            normalized[key] = _round_two(raw)
        elif key in KLINE_NARRATIVE_FIELDS and isinstance(raw, str):
            normalized[key] = _round_decimal_tokens(raw)
        else:
            normalized[key] = normalize_kline_numbers(raw)
    return normalized
