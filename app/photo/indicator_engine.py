import re
from typing import Any

from .indicators.contracts import CANDLE_BODY_SCALE, ENGINE_VERSION
from .indicators.registry import IndicatorRegistry


_REGISTRY = IndicatorRegistry.default()


def identify_indicator(topic_text: str, visual_focus: str = "") -> str:
    text = f"{topic_text} {visual_focus}".lower()
    patterns = (
        ("ict", r"(?<![a-z])ict(?![a-z])|order block|\bfvg\b|\bbos\b|\bchoch\b|liquidity sweep"),
        ("rsi", r"(?<![a-z])rsi(?![a-z])|relative strength index|overbought|oversold|below 30|above 70"),
        ("macd", r"(?<![a-z])macd(?![a-z])|moving average convergence divergence"),
        ("bollinger", r"bollinger|布林"),
        ("atr", r"(?<![a-z])atr(?![a-z])|average true range"),
        ("kdj", r"(?<![a-z])kdj(?![a-z])|stochastic oscillator"),
        ("obv", r"(?<![a-z])obv(?![a-z])|on.balance volume"),
        ("moving_average", r"moving average|均线|(?<![a-z])ema(?![a-z])|(?<![a-z])sma(?![a-z])"),
    )
    for indicator_id, pattern in patterns:
        if re.search(pattern, text):
            return indicator_id
    return "generic"


def _legacy_scenario(indicator_id: str, focus: str) -> str:
    text = str(focus or "").lower()
    if indicator_id == "ict":
        return "bearish_order_block" if "bear" in text else "bullish_order_block"
    if indicator_id == "rsi":
        if any(term in text for term in ("0 to 100", "0-100", "scale", "range")):
            return "range_overview"
        return "overbought_reversal" if any(term in text for term in ("overbought", "above 70")) else "oversold_recovery"
    return "overview"


def build_teaching_scene(indicator_id: str, scenario_id: str = "") -> dict[str, Any]:
    normalized = _REGISTRY.normalize_id(indicator_id)
    goal = scenario_id or _legacy_scenario(normalized, "")
    return _REGISTRY.build_scene(normalized, goal, {}, {})


def validate_teaching_scene(scene: dict[str, Any]) -> bool:
    return _REGISTRY.validate_scene(scene)


def resolve_teaching_scene(
    page: dict[str, Any],
    route_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    route = route_payload or {}
    teaching_spec = page.get("teaching_spec") if isinstance(page.get("teaching_spec"), dict) else {}
    indicator_id = _REGISTRY.normalize_id(str(teaching_spec.get("indicator_id") or ""))
    if not indicator_id:
        focus = f"{page.get('visual_focus') or ''} {' '.join(str(item) for item in page.get('required_elements') or [])}"
        indicator_id = identify_indicator(str(route.get("topic_text") or ""), focus)
    if indicator_id == "generic" and str(page.get("visual_type") or "") in {
        "indicator_panel", "zone_diagram", "candlestick_demo", "market_chart",
    }:
        raise ValueError("INDICATOR_TOPIC_NOT_RECOGNIZED")
    if teaching_spec:
        lesson_goal = str(teaching_spec.get("lesson_goal") or "")
    else:
        lesson_goal = _legacy_scenario(indicator_id, str(page.get("visual_focus") or ""))
        if indicator_id == "rsi" and str(page.get("visual_type") or "") == "candlestick_demo":
            lesson_goal = "worked_example"
    return _REGISTRY.build_scene(indicator_id, lesson_goal, page, route)


__all__ = [
    "CANDLE_BODY_SCALE",
    "ENGINE_VERSION",
    "build_teaching_scene",
    "identify_indicator",
    "resolve_teaching_scene",
    "validate_teaching_scene",
]
