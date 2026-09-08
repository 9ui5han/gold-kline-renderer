"""Deterministic visual-fact catalog construction for TOOL-07."""

from __future__ import annotations

from typing import Any


MARKET_STRUCTURE_LABELS = {
    "range_or_mixed": "Range / mixed",
    "lower_high_lower_low": "Lower highs / lower lows",
    "higher_high_higher_low": "Higher highs / higher lows",
    "bullish": "Bullish structure",
    "bearish": "Bearish structure",
    "sideways": "Sideways structure",
    "mixed": "Mixed structure",
}


class VisualFactCatalogError(ValueError):
    """A deterministic source fact cannot be represented by the catalog."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _display_number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _text(value)
    if number.is_integer():
        return str(int(number))
    return format(number, ".15g")


def _add_fact(facts: list[dict[str, Any]], fact: dict[str, Any]) -> None:
    anchor_id = _text(fact.get("anchor_id"))
    if not anchor_id:
        raise VisualFactCatalogError("VISUAL_FACT_CATALOG_INVALID:anchor_id为空")
    if any(item.get("anchor_id") == anchor_id for item in facts):
        raise VisualFactCatalogError(
            f"VISUAL_FACT_DUPLICATE_ANCHOR:{anchor_id}"
        )
    facts.append(fact)


def _build_scenario_fact(scenario: dict[str, Any]) -> dict[str, Any] | None:
    scenario_id = _text(scenario.get("scenario_id"))
    points = scenario.get("path_points")
    if not scenario_id or not isinstance(points, list) or len(points) < 2:
        return None
    path_points: list[dict[str, Any]] = []
    for point in points:
        if not isinstance(point, dict):
            return None
        ref = _text(point.get("ref"))
        if not ref:
            return None
        try:
            price = float(point["resolved_value"])
            time_ratio = float(point["time_ratio"])
        except (KeyError, TypeError, ValueError):
            return None
        path_points.append({
            "ref": ref,
            "price": price,
            "time_ratio": time_ratio,
        })
    first_price = path_points[0]["price"]
    last_price = path_points[-1]["price"]
    direction = "up" if last_price > first_price else (
        "down" if last_price < first_price else "flat"
    )
    return {
        "anchor_id": f"scenario:{scenario_id}",
        "fact_type": "scenario_path",
        "scenario_id": scenario_id,
        "direction": direction,
        "display_text": _text(scenario.get("label")) or scenario_id,
        "condition_text": _text(scenario.get("condition")),
        "invalidation_text": _text(scenario.get("invalidation")),
        "path_points": path_points,
    }


def build_visual_fact_catalog(
    technical_facts: dict[str, Any],
    market_analysis: dict[str, Any],
    validated_levels: dict[str, Any],
    structure_paths: dict[str, Any],
    forecast_framework: dict[str, Any],
    macro_timing: dict[str, Any],
) -> dict[str, Any]:
    """Build the visual fact catalog from already validated upstream objects."""
    facts: list[dict[str, Any]] = []
    technical_facts = technical_facts if isinstance(technical_facts, dict) else {}
    market_analysis = market_analysis if isinstance(market_analysis, dict) else {}
    validated_levels = validated_levels if isinstance(validated_levels, dict) else {}
    structure_paths = structure_paths if isinstance(structure_paths, dict) else {}
    forecast_framework = forecast_framework if isinstance(forecast_framework, dict) else {}
    macro_timing = macro_timing if isinstance(macro_timing, dict) else {}

    if "last_close" in technical_facts:
        try:
            price = float(technical_facts["last_close"])
        except (TypeError, ValueError) as exc:
            raise VisualFactCatalogError(
                "VISUAL_FACT_CATALOG_INVALID:technical.last_close无效"
            ) from exc
        _add_fact(facts, {
            "anchor_id": "technical:last_close",
            "fact_type": "price_point",
            "price": price,
            "display_text": _display_number(price),
        })

    if "market_structure" in technical_facts:
        structure = _text(technical_facts.get("market_structure"))
        if structure not in MARKET_STRUCTURE_LABELS:
            raise VisualFactCatalogError(
                f"VISUAL_TEXT_ENUM_UNKNOWN:technical:market_structure={structure}"
            )
        _add_fact(facts, {
            "anchor_id": "technical:market_structure",
            "fact_type": "text_fact",
            "display_text": MARKET_STRUCTURE_LABELS[structure],
        })

    if _text(technical_facts.get("technical_summary")):
        _add_fact(facts, {
            "anchor_id": "technical:technical_summary",
            "fact_type": "text_fact",
            "display_text": _text(technical_facts["technical_summary"]),
        })

    levels = validated_levels.get("levels")
    if isinstance(levels, list) and levels:
        collection: list[dict[str, Any]] = []
        for level in levels:
            if not isinstance(level, dict):
                continue
            level_id = _text(level.get("level_id"))
            if not level_id:
                continue
            try:
                lower = float(level["zone_low"])
                upper = float(level["zone_high"])
                center = float(level["center"])
            except (KeyError, TypeError, ValueError) as exc:
                raise VisualFactCatalogError(
                    f"VISUAL_FACT_CATALOG_INVALID:level={level_id}"
                ) from exc
            fact = {
                "anchor_id": f"level:{level_id}",
                "fact_type": "price_zone",
                "role": _text(level.get("side")),
                "lower_price": lower,
                "upper_price": upper,
                "center_price": center,
                "display_text": level_id,
            }
            _add_fact(facts, fact)
            collection.append(fact)
        if collection:
            _add_fact(facts, {
                "anchor_id": "technical:validated_levels",
                "fact_type": "level_collection",
                "display_text": "Validated levels",
                "levels": collection,
            })

    scenarios = structure_paths.get("scenarios")
    if isinstance(scenarios, list):
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                continue
            fact = _build_scenario_fact(scenario)
            if fact is not None:
                _add_fact(facts, fact)

    market_summary = _text(market_analysis.get("technical_summary"))
    if market_summary:
        _add_fact(facts, {
            "anchor_id": "market:analysis",
            "fact_type": "text_fact",
            "display_text": market_summary,
        })

    if forecast_framework:
        limitations = forecast_framework.get("limitations")
        limitation_text = ""
        if isinstance(limitations, list):
            limitation_text = next(
                (_text(item) for item in limitations if _text(item)), ""
            )
        if len(limitation_text) > 72:
            raise VisualFactCatalogError("VISUAL_TEXT_TOO_LONG:forecast:framework")
        _add_fact(facts, {
            "anchor_id": "forecast:framework",
            "fact_type": "framework_fact",
            "available": forecast_framework.get("available") is True,
            "direction_state": _text(forecast_framework.get("direction_state")),
            "limitation_text": limitation_text,
        })

    events = macro_timing.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = _text(event.get("event_id"))
            if not event_id:
                continue
            title = (
                _text(event.get("title"))
                or _text(event.get("event_title"))
                or _text(event.get("name"))
                or event_id
            )
            _add_fact(facts, {
                "anchor_id": f"macro:{event_id}",
                "fact_type": "macro_event",
                "event_id": event_id,
                "scheduled_time_utc": _text(event.get("scheduled_time_utc")),
                "display_text": title,
            })

    return {
        "schema_version": "visual-fact-catalog-v1",
        "facts": facts,
    }
