"""Deterministic TOOL-07 segment-plan validation and loop state."""

from __future__ import annotations

import json
from math import isclose
from typing import Any


EPSILON = 0.001
ALLOWED_TRANSITIONS = {
    "hard_cut", "fade", "slide_left", "slide_right", "zoom_blur",
    "cross_zoom", "light_zoom", "whip_left", "whip_right", "flash",
    "blur_zoom",
}
ALLOWED_TEMPLATES = {
    "hook_chart", "kinetic_text", "chart_push", "level_explain",
    "path_reveal", "gauge_explain", "risk_card", "summary_grid",
    "closing_card",
}
ALLOWED_EVENT_TYPES = {
    "hook_text", "technical_label", "price_level", "scenario_path",
    "macro_marker", "risk_notice", "closing_question", "caption",
}


def _number(value: Any, label: str, errors: list[str]) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"{label}无效")
        return 0.0


def _check_anchor_ids(
    value: Any,
    label: str,
    known_ids: set[str],
    errors: list[str],
) -> list[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{label}:fact_anchor_ids必须是非空Array")
        return []
    seen: set[str] = set()
    anchors: list[str] = []
    for raw_anchor in value:
        anchor = str(raw_anchor or "")
        if not anchor:
            errors.append(f"{label}:fact_anchor_id为空")
        elif anchor in seen:
            errors.append(f"{label}:fact_anchor_id重复={anchor}")
        elif anchor not in known_ids:
            errors.append(f"{label}:未知fact_anchor_id={anchor}")
        seen.add(anchor)
        anchors.append(anchor)
    return anchors


def _check_transition(
    transition: Any,
    label: str,
    duration: float,
    next_duration: float,
    is_last: bool,
    errors: list[str],
) -> None:
    if not isinstance(transition, dict):
        errors.append(f"{label}:transition_out必须是Object")
        return
    transition_type = str(transition.get("type") or "")
    if transition_type not in ALLOWED_TRANSITIONS:
        errors.append(f"{label}:transition type无效")
    try:
        duration_ms = int(transition.get("duration_ms"))
    except (TypeError, ValueError):
        errors.append(f"{label}:transition duration_ms无效")
        return
    if is_last:
        if duration_ms != 0:
            errors.append(f"{label}:最后一个transition_out.duration_ms必须为0")
        return
    if transition_type == "hard_cut":
        if duration_ms != 0:
            errors.append(f"{label}:hard_cut时长必须为0")
        return
    max_boundary_ms = int(min(duration, next_duration) * 0.25 * 1000)
    if not 150 <= duration_ms <= min(500, max_boundary_ms):
        errors.append(f"{label}:transition需150..500ms且不超过相邻时间轴25%")


def validate_segment_plan(
    segment_plan: dict[str, Any],
    segment_budget: dict[str, Any],
    technical_facts: dict[str, Any],
    market_analysis: dict[str, Any],
    validated_levels: dict[str, Any],
    structure_paths: dict[str, Any],
    forecast_framework: dict[str, Any],
    macro_timing: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if segment_plan.get("schema_version") != "video-segment-plan-v1":
        errors.append("schema_version必须是video-segment-plan-v1")

    segments = segment_plan.get("segments") or []
    if not isinstance(segments, list):
        errors.append("segments必须是Array")
        segments = []
    min_segments = int(segment_budget.get("min_segments") or 4)
    max_segments = int(segment_budget.get("max_segments") or 7)
    if not min_segments <= len(segments) <= max_segments:
        errors.append(f"segment数量必须在{min_segments}..{max_segments}")

    level_ids = {
        str(item.get("level_id"))
        for item in validated_levels.get("levels") or []
        if isinstance(item, dict) and str(item.get("level_id") or "")
    }
    scenario_ids = {
        str(item.get("scenario_id"))
        for item in structure_paths.get("scenarios") or []
        if isinstance(item, dict) and str(item.get("scenario_id") or "")
    }
    macro_event_ids = {
        str(item.get("event_id"))
        for item in macro_timing.get("events") or []
        if isinstance(item, dict) and str(item.get("event_id") or "")
    }
    has_relevant_macro = bool(segment_budget.get("has_relevant_macro"))
    known_anchor_ids: set[str] = set()
    for field in ("last_close", "market_structure", "technical_summary"):
        if field in technical_facts:
            known_anchor_ids.add(f"technical:{field}")
    if validated_levels:
        known_anchor_ids.add("technical:validated_levels")
    if market_analysis:
        known_anchor_ids.add("market:analysis")
    if forecast_framework:
        known_anchor_ids.add("forecast:framework")
    known_anchor_ids.update(f"level:{item}" for item in level_ids)
    known_anchor_ids.update(f"scenario:{item}" for item in scenario_ids)
    known_anchor_ids.update(f"macro:{item}" for item in macro_event_ids)

    required_sections = {"intro", "analysis", "primary_path", "outro"}
    expected_roles = {
        "intro": "opening_hook",
        "analysis": "technical_context",
        "macro": "macro_context",
        "primary_path": "primary_forecast",
        "alternate_path": "alternate_forecast",
        "outro": "closing_question",
    }
    allowed_visual_modes = set(segment_budget.get("visual_modes") or [])
    allowed_camera = set(segment_budget.get("camera_motions") or [])
    seen_sections: set[str] = set()
    seen_ids: set[str] = set()
    segment_sum = 0.0
    section_durations: dict[str, float] = {}

    for index, raw_segment in enumerate(segments, start=1):
        segment = raw_segment if isinstance(raw_segment, dict) else {}
        if not isinstance(raw_segment, dict):
            errors.append(f"segment {index}:必须是Object")
        segment_id = str(segment.get("segment_id") or "")
        if not segment_id:
            errors.append(f"segment {index}: segment_id为空")
        elif segment_id in seen_ids:
            errors.append(f"重复segment_id:{segment_id}")
        seen_ids.add(segment_id)
        if int(segment.get("order") or 0) != index:
            errors.append(f"{segment_id}:order必须连续从1开始")

        section = str(segment.get("section") or "")
        role = str(segment.get("planning_role") or "")
        seen_sections.add(section)
        if role != expected_roles.get(section):
            errors.append(f"{segment_id}:planning_role与section不匹配")
        segment_anchors = _check_anchor_ids(
            segment.get("fact_anchor_ids"),
            segment_id or f"segment {index}",
            known_anchor_ids,
            errors,
        )
        duration = _number(
            segment.get("duration_target_sec"),
            f"{segment_id}:duration_target_sec",
            errors,
        )
        if duration < 2.0 or duration > 120.0:
            errors.append(f"{segment_id}:duration_target_sec必须为2..120")
        if section in {"intro", "outro"} and not 2.0 <= duration <= 4.0:
            errors.append(f"{segment_id}:intro/outro时长必须为2..4秒")
        if str(segment.get("importance") or "") not in {
            "normal", "high", "critical",
        }:
            errors.append(f"{segment_id}:importance必须是normal/high/critical")
        if str(segment.get("speech_style") or "") not in {
            "compact", "normal", "slow_emphasis", "caution",
        }:
            errors.append(f"{segment_id}:speech_style无效")

        segment_sum += duration
        section_durations[section] = section_durations.get(section, 0.0) + duration
        scenario_id = segment.get("scenario_id")
        if scenario_id is not None and str(scenario_id) not in scenario_ids:
            errors.append(f"{segment_id}:未知scenario_id={scenario_id}")

        visual = segment.get("visual") or {}
        if not isinstance(visual, dict):
            errors.append(f"{segment_id}:visual必须是Object")
            visual = {}
        if visual.get("visual_mode") not in allowed_visual_modes:
            errors.append(f"{segment_id}:visual_mode无效")
        if visual.get("camera_motion") not in allowed_camera:
            errors.append(f"{segment_id}:camera_motion无效")
        for level_id in visual.get("highlight_levels") or []:
            if str(level_id) not in level_ids:
                errors.append(f"{segment_id}:未知highlight level={level_id}")
        if not has_relevant_macro:
            if section == "macro":
                errors.append(f"{segment_id}:无相关宏观事件时不得有macro切片")
            if visual.get("visual_mode") == "macro_event":
                errors.append(f"{segment_id}:无相关宏观事件时不得用macro_event")
            if visual.get("show_macro_marker") is True:
                errors.append(f"{segment_id}:无相关宏观事件时不得显示宏观标记")
            if any(item.startswith("macro:") for item in segment_anchors):
                errors.append(f"{segment_id}:无相关宏观事件时不得使用macro事实锚点")

        scenes = segment.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            errors.append(f"{segment_id}:scenes必须是非空Array")
            scenes = []
        elif len(scenes) > 5:
            errors.append(f"{segment_id}:scenes最多5项")
        scene_cursor = 0.0
        seen_scene_ids: set[str] = set()
        segment_event_types: set[str] = set()
        for scene_index, raw_scene in enumerate(scenes, start=1):
            label = f"{segment_id}:scene {scene_index}"
            if not isinstance(raw_scene, dict):
                errors.append(f"{label}必须是Object")
                continue
            scene = raw_scene
            scene_id = str(scene.get("scene_id") or "")
            if not scene_id:
                errors.append(f"{label}:scene_id为空")
            elif scene_id in seen_scene_ids:
                errors.append(f"{segment_id}:重复scene_id={scene_id}")
            seen_scene_ids.add(scene_id)
            if str(scene.get("template_id") or "") not in ALLOWED_TEMPLATES:
                errors.append(f"{label}:template_id无效")
            start_sec = _number(scene.get("start_sec"), f"{label}:start_sec", errors)
            scene_duration = _number(
                scene.get("duration_sec"), f"{label}:duration_sec", errors
            )
            if start_sec < 0 or start_sec > duration:
                errors.append(f"{label}:start_sec越界")
            if scene_duration <= 0 or scene_duration > duration:
                errors.append(f"{label}:duration_sec越界")
            if not isclose(start_sec, scene_cursor, abs_tol=EPSILON):
                errors.append(f"{label}:start_sec必须与上一场连续")
            if scene.get("camera_motion") not in allowed_camera:
                errors.append(f"{label}:camera_motion无效")

            events = scene.get("overlay_events")
            if not isinstance(events, list):
                errors.append(f"{label}:overlay_events必须是Array")
                events = []
            elif len(events) > 6:
                errors.append(f"{label}:overlay_events最多6项")
            seen_event_ids: set[str] = set()
            for event_index, raw_event in enumerate(events, start=1):
                event_label = f"{label}:event {event_index}"
                if not isinstance(raw_event, dict):
                    errors.append(f"{event_label}必须是Object")
                    continue
                event = raw_event
                event_id = str(event.get("event_id") or "")
                if not event_id:
                    errors.append(f"{event_label}:event_id为空")
                elif event_id in seen_event_ids:
                    errors.append(f"{label}:重复event_id={event_id}")
                seen_event_ids.add(event_id)
                event_type = str(event.get("event_type") or "")
                segment_event_types.add(event_type)
                if event_type not in ALLOWED_EVENT_TYPES:
                    errors.append(f"{event_label}:event_type无效")
                event_start = _number(
                    event.get("start_sec"), f"{event_label}:start_sec", errors
                )
                event_duration = _number(
                    event.get("duration_sec"), f"{event_label}:duration_sec", errors
                )
                if event_start < 0 or event_duration <= 0:
                    errors.append(f"{event_label}:时间必须为正且不越界")
                elif event_start + event_duration > scene_duration + EPSILON:
                    errors.append(f"{event_label}:超出Scene时间范围")
                event_anchors = _check_anchor_ids(
                    event.get("fact_anchor_ids"), event_label,
                    known_anchor_ids, errors,
                )
                if not has_relevant_macro and (
                    event_type == "macro_marker"
                    or any(item.startswith("macro:") for item in event_anchors)
                ):
                    errors.append(f"{event_label}:无相关宏观事件时不得标记宏观")

            next_scene_duration = 0.0
            if scene_index < len(scenes) and isinstance(scenes[scene_index], dict):
                next_scene_duration = _number(
                    scenes[scene_index].get("duration_sec"),
                    f"{segment_id}:next_scene duration_sec",
                    errors,
                )
            _check_transition(
                scene.get("transition_out"), label, scene_duration,
                next_scene_duration, scene_index == len(scenes), errors,
            )
            scene_cursor += scene_duration

        if not isclose(scene_cursor, duration, abs_tol=EPSILON):
            errors.append(f"{segment_id}:scenes时长之和必须等于segment时长")
        if index == 1 and scenes:
            if section != "intro" or role != "opening_hook":
                errors.append("首段必须是intro/opening_hook")
            if str(scenes[0].get("template_id") or "") not in {
                "hook_chart", "kinetic_text",
            }:
                errors.append("首段第一场必须使用hook_chart或kinetic_text")
            if "hook_text" not in segment_event_types:
                errors.append("首段必须包含hook_text叠加事件")
        if index == len(segments) and scenes:
            if section != "outro" or role != "closing_question":
                errors.append("末段必须是outro/closing_question")
            if str(scenes[-1].get("template_id") or "") != "closing_card":
                errors.append("末段最后一场必须使用closing_card")
            if "closing_question" not in segment_event_types:
                errors.append("末段必须包含closing_question叠加事件")

        next_duration = 0.0
        if index < len(segments) and isinstance(segments[index], dict):
            next_duration = _number(
                segments[index].get("duration_target_sec"),
                f"{segment_id}:next_segment duration_target_sec",
                errors,
            )
        _check_transition(
            segment.get("transition_out"), segment_id or f"segment {index}",
            duration, next_duration, index == len(segments), errors,
        )

    missing = sorted(required_sections - seen_sections)
    if missing:
        errors.append("缺少必须section:" + ",".join(missing))
    if segments:
        first = segments[0] if isinstance(segments[0], dict) else {}
        last = segments[-1] if isinstance(segments[-1], dict) else {}
        if first.get("section") != "intro" or first.get("planning_role") != "opening_hook":
            errors.append("首段必须是intro且planning_role=opening_hook")
        if last.get("section") != "outro" or last.get("planning_role") != "closing_question":
            errors.append("末段必须是outro且planning_role=closing_question")

    estimated = segment_sum
    declared_target = _number(
        segment_plan.get("target_duration_sec"), "target_duration_sec", errors
    )
    budget_target = _number(
        segment_budget.get("target_duration_sec"),
        "segment_budget.target_duration_sec", errors,
    )
    declared_estimated = _number(
        segment_plan.get("estimated_final_duration_sec"),
        "estimated_final_duration_sec", errors,
    )
    if not isclose(declared_target, budget_target, abs_tol=EPSILON):
        errors.append("target_duration_sec必须等于segment_budget目标时长")
    if not isclose(declared_estimated, estimated, abs_tol=EPSILON):
        errors.append("estimated_final_duration_sec必须等于segments总时长")

    if estimated > 0:
        ratio_policy = segment_budget["section_ratio_policy"]
        actual_ratios = {
            "intro": section_durations.get("intro", 0.0) / estimated,
            "analysis": section_durations.get("analysis", 0.0) / estimated,
            "macro": section_durations.get("macro", 0.0) / estimated,
            "forecast_total": (
                section_durations.get("primary_path", 0.0)
                + section_durations.get("alternate_path", 0.0)
            ) / estimated,
            "outro": section_durations.get("outro", 0.0) / estimated,
        }
        for name, ratio in actual_ratios.items():
            lower, upper = [float(item) for item in ratio_policy[name]]
            if ratio < lower - 1e-9 or ratio > upper + 1e-9:
                errors.append(
                    f"{name}比例{ratio:.4f}不在{lower:.2f}..{upper:.2f}"
                )

    preferred_min = float(segment_budget["preferred_min_sec"])
    preferred_max = float(segment_budget["preferred_max_sec"])
    hard_min = float(segment_budget["hard_min_sec"])
    hard_max = float(segment_budget["hard_max_sec"])
    if not hard_min <= estimated <= hard_max:
        errors.append(f"预计最终时长{estimated:.2f}s超出Hard范围")
    return {
        "segment_plan": segment_plan,
        "segment_plan_valid": len(errors) == 0,
        "segment_plan_errors": errors,
        "calculated_final_duration_sec": round(estimated, 3),
        "duration_in_preferred": preferred_min <= estimated <= preferred_max,
    }


def process_segment_plan_step(
    candidate: dict[str, Any],
    segment_budget: dict[str, Any],
    technical_facts: dict[str, Any],
    market_analysis: dict[str, Any],
    validated_levels: dict[str, Any],
    structure_paths: dict[str, Any],
    forecast_framework: dict[str, Any],
    macro_timing: dict[str, Any],
    repair_count: int,
    *,
    max_repairs: int = 2,
) -> dict[str, Any]:
    count = int(repair_count)
    if count < 0:
        raise ValueError("REPAIR_COUNT_INVALID")
    validation = validate_segment_plan(
        candidate, segment_budget, technical_facts, market_analysis,
        validated_levels, structure_paths, forecast_framework, macro_timing,
    )
    valid = bool(validation["segment_plan_valid"])
    action = "pass" if valid else ("fail" if count >= max_repairs else "repair")
    done = action != "repair"
    errors = validation["segment_plan_errors"]
    contract = {
        "schema_version": "segment-plan-contract-v1",
        "segment_plan": candidate,
        "segment_plan_valid": valid,
        "segment_plan_errors": errors,
        "calculated_final_duration_sec": validation["calculated_final_duration_sec"],
        "duration_in_preferred": validation["duration_in_preferred"],
    }
    result = {
        "segment_plan_v1_json": json.dumps(
            contract, ensure_ascii=False, separators=(",", ":")
        ),
        "segment_plan_valid": valid,
        "segment_errors_json": json.dumps(
            errors, ensure_ascii=False, separators=(",", ":")
        ),
        "calculated_final_duration_sec": validation["calculated_final_duration_sec"],
        "duration_in_preferred": validation["duration_in_preferred"],
    }
    repair_payload = {
        "candidate": candidate,
        "validator_errors": errors,
        "segment_budget": segment_budget,
        "technical_facts": technical_facts,
        "market_analysis": market_analysis,
        "validated_levels": validated_levels,
        "structure_paths": structure_paths,
        "forecast_framework": forecast_framework,
        "macro_timing": macro_timing,
    }
    next_request_base = {
        "segment_budget": segment_budget,
        "technical_facts": technical_facts,
        "market_analysis": market_analysis,
        "validated_levels": validated_levels,
        "structure_paths": structure_paths,
        "forecast_framework": forecast_framework,
        "macro_timing": macro_timing,
        "repair_count": count + 1,
    }
    return {
        "schema_version": "segment-plan-step-result-v1",
        "action": action,
        "done": done,
        "result_json": json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        "repair_prompt_json": json.dumps(
            repair_payload, ensure_ascii=False, separators=(",", ":")
        ),
        "next_request_base_json": json.dumps(
            next_request_base, ensure_ascii=False, separators=(",", ":")
        ),
    }
