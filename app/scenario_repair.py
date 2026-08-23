"""Validate and repair TOOL-06 structure-path candidates."""

from __future__ import annotations

import json
from typing import Any


def validate_structure_paths(
    structure_paths: dict[str, Any],
    active_levels: dict[str, Any],
    forecast_framework: dict[str, Any],
) -> list[str]:
    errors: list[str] = []

    if structure_paths.get("schema_version") != "structure-path-v1":
        errors.append("schema_version 必须是 structure-path-v1")

    scenarios = structure_paths.get("scenarios") or []
    if not isinstance(scenarios, list) or not 1 <= len(scenarios) <= 3:
        errors.append("scenarios 数量必须是 1..3")
        scenarios = []

    scenario_ids = {
        str(item.get("scenario_id"))
        for item in scenarios
        if isinstance(item, dict)
    }
    primary = str(structure_paths.get("primary_scenario") or "")
    alternate = structure_paths.get("alternate_scenario")

    if primary not in scenario_ids:
        errors.append("primary_scenario 不存在")
    if len(scenario_ids) >= 2 and (
        alternate is None
        or str(alternate) not in scenario_ids
        or str(alternate) == primary
    ):
        errors.append("多Scenario时 alternate_scenario 无效")

    try:
        price_map = {
            str(key): float(value)
            for key, value in (
                active_levels.get("authoritative_price_map") or {}
            ).items()
        }
    except (AttributeError, TypeError, ValueError):
        price_map = {}
        errors.append("authoritative_price_map 无效")

    allowed_active_ids = {
        str(item.get("level_id"))
        for item in active_levels.get("active") or []
        if isinstance(item, dict)
    }
    allowed_refs = {"CURRENT", "OPEN_UPSIDE", "OPEN_DOWNSIDE"}
    allowed_refs.update(
        ref
        for ref in price_map
        if ref == "MIDPOINT"
        or ref.startswith("MIDPOINT.")
        or ref.startswith("SETUP.")
    )
    for level_id in allowed_active_ids:
        allowed_refs.update(
            {level_id, f"{level_id}.lower", f"{level_id}.upper"}
        )

    banned_terms = (
        "稳赚", "必赚", "保证盈利", "guaranteed profit", "certain profit",
        "建议买入", "建议卖出", "立即买入", "立即卖出", "现在买入",
        "现在卖出", "buy now", "sell now", "you should buy",
        "you should sell", "buy gold", "sell gold", "recommend you buy",
        "recommend you sell", "买入", "卖出", "做多", "做空", "go long",
        "go short", "enter long", "enter short", "open a long",
        "open a short",
    )

    for scenario in scenarios:
        if not isinstance(scenario, dict):
            errors.append("scenario 必须是Object")
            continue
        scenario_id = str(scenario.get("scenario_id") or "")
        points = scenario.get("path_points") or []
        combined_text = " ".join(
            str(scenario.get(key) or "")
            for key in ("label", "condition", "invalidation", "reason")
        ).lower()
        if any(term in combined_text for term in banned_terms):
            errors.append(f"{scenario_id}: 禁止盈利保证或个性化买卖指令")
        if not isinstance(points, list) or not 2 <= len(points) <= 4:
            errors.append(f"{scenario_id}: path_points 必须 2..4")
            continue

        previous_ratio = -1.0
        for point in points:
            if not isinstance(point, dict):
                errors.append(f"{scenario_id}: path_point 必须是Object")
                continue
            ref = str(point.get("ref") or "")
            if ref not in allowed_refs:
                errors.append(f"{scenario_id}: 非法或不可达ref {ref}")
                continue
            if ref not in price_map:
                errors.append(f"{scenario_id}: ref没有Code价格 {ref}")
                continue
            try:
                resolved_value = float(point.get("resolved_value"))
                time_ratio = float(point.get("time_ratio"))
            except (TypeError, ValueError):
                errors.append(f"{scenario_id}: resolved_value/time_ratio 无效")
                continue
            if abs(resolved_value - price_map[ref]) > 1e-9:
                errors.append(f"{scenario_id}: {ref} resolved_value被修改")
            if not 0.0 <= time_ratio <= 1.0:
                errors.append(f"{scenario_id}: time_ratio 超出0..1")
            if time_ratio < previous_ratio:
                errors.append(f"{scenario_id}: time_ratio 未递增")
            previous_ratio = time_ratio

    midpoint = forecast_framework.get("midpoint_zone") or {}
    if (
        forecast_framework.get("available") is True
        and forecast_framework.get("direction_state")
        in {"wait_for_confirmation", "event_conditional_only"}
    ):
        try:
            lower = float(midpoint["lower"])
            upper = float(midpoint["upper"])
            up_scenarios: set[str] = set()
            down_scenarios: set[str] = set()
            for scenario in scenarios:
                if not isinstance(scenario, dict):
                    continue
                values = [
                    float(point["resolved_value"])
                    for point in scenario.get("path_points") or []
                ]
                has_up = any(value > upper for value in values)
                has_down = any(value < lower for value in values)
                scenario_id = str(scenario.get("scenario_id") or "")
                if has_up and not has_down:
                    up_scenarios.add(scenario_id)
                if has_down and not has_up:
                    down_scenarios.add(scenario_id)
            if (
                not up_scenarios
                or not down_scenarios
                or up_scenarios.intersection(down_scenarios)
            ):
                errors.append(
                    "中间区域等待/事件覆盖时必须由两条独立相反方向情景组成"
                )
        except (KeyError, TypeError, ValueError):
            errors.append("forecast_framework midpoint无效")

    return errors


def validate_candidate(
    candidate: dict[str, Any],
    active_levels: dict[str, Any],
    forecast_framework: dict[str, Any],
    market_analysis: dict[str, Any],
    macro_timing: dict[str, Any],
) -> dict[str, Any]:
    normalized_candidate = dict(candidate or {})
    errors = validate_structure_paths(
        normalized_candidate,
        active_levels,
        forecast_framework,
    )
    valid = not errors
    repair_payload = {
        "candidate": normalized_candidate,
        "validator_errors": errors,
        "active_levels": active_levels,
        "forecast_framework": forecast_framework,
        "market_analysis": market_analysis,
        "macro_timing": macro_timing,
    }
    return {
        "schema_version": "scenario-validation-result-v1",
        "candidate_json": json.dumps(
            normalized_candidate,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "scenario_valid": valid,
        "scenario_errors_json": json.dumps(
            errors,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "repair_prompt_json": json.dumps(
            repair_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }


def process_scenario_step(
    candidate: dict[str, Any],
    active_levels: dict[str, Any],
    forecast_framework: dict[str, Any],
    market_analysis: dict[str, Any],
    macro_timing: dict[str, Any],
    repair_count: int,
    *,
    max_repairs: int = 2,
) -> dict[str, Any]:
    count = int(repair_count)
    if count < 0:
        raise ValueError("REPAIR_COUNT_INVALID")

    validation = validate_candidate(
        candidate,
        active_levels,
        forecast_framework,
        market_analysis,
        macro_timing,
    )
    valid = bool(validation["scenario_valid"])
    if valid:
        action = "pass"
        done = True
    elif count >= max_repairs:
        action = "fail"
        done = True
    else:
        action = "repair"
        done = False

    forecast_contract = {
        "schema_version": "forecast-contract-v1",
        "active_levels": active_levels,
        "forecast_framework": forecast_framework,
        "structure_paths": candidate,
        "scenario_valid": valid,
        "scenario_errors": json.loads(validation["scenario_errors_json"]),
    }
    result = {
        "forecast_v1_json": json.dumps(
            forecast_contract,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "scenario_valid": valid,
        "scenario_errors_json": validation["scenario_errors_json"],
    }
    next_request_base = {
        "active_levels": active_levels,
        "forecast_framework": forecast_framework,
        "market_analysis": market_analysis,
        "macro_timing": macro_timing,
        "repair_count": count + 1,
    }
    return {
        "schema_version": "scenario-step-result-v1",
        "action": action,
        "done": done,
        "result_json": json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "repair_prompt_json": validation["repair_prompt_json"],
        "next_request_base_json": json.dumps(
            next_request_base,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
