"""Deterministic TOOL-07 segment-plan validation and loop state."""

from __future__ import annotations

import copy
import json
from math import isclose
from typing import Any

from .kline_precision import normalize_kline_numbers
from .visual_fact_catalog import (
    VisualFactCatalogError,
    build_visual_fact_catalog,
)


EPSILON = 0.001
DEFAULT_EDGE_RATIO = 0.05
DEFAULT_EDGE_MIN_SEC = 5.5
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


def _canonicalize_visual_booleans(visual: dict[str, Any]) -> None:
    """Convert the only safe legacy Boolean spellings before validation."""
    for field in ("show_volume", "show_macro_marker"):
        value = visual.get(field)
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            visual[field] = value.strip().lower() == "true"


def _edge_duration_requirement(
    segment_budget: dict[str, Any],
    target_duration_sec: float,
) -> tuple[float, float, set[str]]:
    """Return the dynamic edge ratio, floor, and applicable sections."""
    policy = segment_budget.get("edge_duration_policy")
    if not isinstance(policy, dict):
        policy = {}

    try:
        ratio = float(policy.get("ratio", DEFAULT_EDGE_RATIO))
    except (TypeError, ValueError):
        ratio = DEFAULT_EDGE_RATIO
    try:
        minimum = float(policy.get("min_sec", DEFAULT_EDGE_MIN_SEC))
    except (TypeError, ValueError):
        minimum = DEFAULT_EDGE_MIN_SEC

    if ratio < 0:
        ratio = DEFAULT_EDGE_RATIO
    if minimum < 0:
        minimum = DEFAULT_EDGE_MIN_SEC

    raw_sections = policy.get("sections")
    if isinstance(raw_sections, list):
        sections = {
            str(item)
            for item in raw_sections
            if str(item) in {"intro", "outro"}
        }
        if not sections:
            sections = {"intro", "outro"}
    else:
        sections = {"intro", "outro"}
    return ratio, minimum, sections


def _rescale_plan_segment(segment: dict[str, Any], new_duration: float) -> None:
    """Resize a segment and its visual timeline without changing its content."""
    old_duration = float(segment.get("duration_target_sec") or 0.0)
    if old_duration <= EPSILON:
        return
    scale = new_duration / old_duration
    scenes = segment.get("scenes")
    if isinstance(scenes, list) and scenes:
        scaled_durations: list[float] = []
        for scene in scenes:
            if not isinstance(scene, dict):
                scaled_durations.append(0.0)
                continue
            old_scene_duration = float(scene.get("duration_sec") or 0.0)
            scaled_durations.append(old_scene_duration * scale)
        scaled_total = sum(scaled_durations)
        if scaled_durations:
            scaled_durations[-1] += new_duration - scaled_total

        cursor = 0.0
        for scene, scaled_scene_duration in zip(scenes, scaled_durations):
            if not isinstance(scene, dict):
                continue
            old_scene_duration = float(scene.get("duration_sec") or 0.0)
            scene["start_sec"] = round(cursor, 3)
            scene_duration = max(0.0, scaled_scene_duration)
            scene["duration_sec"] = round(scene_duration, 3)
            scene_scale = (
                scene_duration / old_scene_duration
                if old_scene_duration > EPSILON else 1.0
            )
            for event in scene.get("overlay_events") or []:
                if not isinstance(event, dict):
                    continue
                event_start = max(
                    0.0,
                    min(
                        scene_duration,
                        float(event.get("start_sec") or 0.0) * scene_scale,
                    ),
                )
                event_duration = max(
                    0.001,
                    min(
                        scene_duration - event_start,
                        float(event.get("duration_sec") or 0.0) * scene_scale,
                    ),
                )
                event["start_sec"] = round(event_start, 3)
                event["duration_sec"] = round(event_duration, 3)
            cursor += float(scene["duration_sec"])
    segment["duration_target_sec"] = round(new_duration, 3)


def _reallocate_edge_duration(
    candidate: dict[str, Any],
    segment_budget: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Move edge-duration overflow into eligible middle segments deterministically."""
    segments = candidate.get("segments")
    if not isinstance(segments, list) or not segments:
        return candidate, []
    try:
        target = float(segment_budget.get("target_duration_sec"))
    except (TypeError, ValueError):
        return candidate, []
    if target <= 0:
        return candidate, []
    current_total = sum(
        float(item.get("duration_target_sec") or 0.0)
        for item in segments
        if isinstance(item, dict)
    )
    if not isclose(current_total, target, abs_tol=EPSILON):
        return candidate, []

    policy = segment_budget.get("edge_duration_policy")
    if not isinstance(policy, dict):
        policy = {}
    mode = str(
        policy.get("reallocation")
        or "average_from_eligible_middle_segments"
    )
    if mode in {"none", "disabled"}:
        return candidate, []

    edge_ratio, edge_min_sec, edge_sections = _edge_duration_requirement(
        segment_budget,
        target,
    )
    required_edge = max(target * edge_ratio, edge_min_sec)
    edge_indexes = [
        index for index, item in enumerate(segments)
        if isinstance(item, dict)
        and str(item.get("section") or "") in edge_sections
    ]
    if not edge_indexes:
        return candidate, []

    edge_extra = sum(
        max(
            0.0,
            required_edge - float(segments[index].get("duration_target_sec") or 0.0),
        )
        for index in edge_indexes
    )
    if edge_extra <= EPSILON:
        return candidate, []

    ratio_policy = segment_budget.get("section_ratio_policy") or {}
    protected = policy.get("protected_sections")
    protected_sections = (
        {str(item) for item in protected}
        if isinstance(protected, list)
        else {"analysis"}
    )
    middle_indexes = [
        index for index, item in enumerate(segments)
        if isinstance(item, dict)
        and str(item.get("section") or "") not in edge_sections
        and str(item.get("section") or "") not in protected_sections
    ]
    capacities: dict[int, float] = {}
    for index in middle_indexes:
        item = segments[index]
        section = str(item.get("section") or "")
        floor = 2.0
        policy_range = ratio_policy.get(section)
        if isinstance(policy_range, list) and policy_range:
            try:
                floor = max(floor, target * float(policy_range[0]))
            except (TypeError, ValueError):
                pass
        duration = float(item.get("duration_target_sec") or 0.0)
        capacity = max(0.0, duration - floor)
        if capacity > EPSILON:
            capacities[index] = capacity

    if sum(capacities.values()) + EPSILON < edge_extra:
        return candidate, [
            "EDGE_DURATION_INFEASIBLE_MIDDLE_REALLOCATION:中间分段没有足够可扣减时长"
        ]

    deductions = {index: 0.0 for index in capacities}
    active = list(capacities)
    remaining = edge_extra
    while active and remaining > EPSILON:
        share = remaining / len(active)
        next_active: list[int] = []
        allocated = 0.0
        for index in active:
            available = capacities[index] - deductions[index]
            take = min(share, available)
            deductions[index] += take
            allocated += take
            if available - take > EPSILON:
                next_active.append(index)
        if allocated <= EPSILON:
            break
        remaining -= allocated
        active = next_active

    adjusted = copy.deepcopy(candidate)
    for index in edge_indexes:
        item = adjusted["segments"][index]
        current = float(item.get("duration_target_sec") or 0.0)
        if current < required_edge:
            _rescale_plan_segment(item, required_edge)
    for index, deduction in deductions.items():
        item = adjusted["segments"][index]
        current = float(item.get("duration_target_sec") or 0.0)
        _rescale_plan_segment(item, max(2.0, current - deduction))
    adjusted["estimated_final_duration_sec"] = round(
        sum(float(item.get("duration_target_sec") or 0.0) for item in adjusted["segments"]),
        3,
    )
    return adjusted, []


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
    try:
        visual_fact_catalog = build_visual_fact_catalog(
            technical_facts,
            market_analysis,
            validated_levels,
            structure_paths,
            forecast_framework,
            macro_timing,
        )
    except VisualFactCatalogError as exc:
        visual_fact_catalog = {
            "schema_version": "visual-fact-catalog-v1",
            "facts": [],
        }
        errors.append(str(exc))

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
    known_anchor_ids = {
        str(item.get("anchor_id"))
        for item in visual_fact_catalog.get("facts") or []
        if isinstance(item, dict) and str(item.get("anchor_id") or "")
    }

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
    budget_target = _number(
        segment_budget.get("target_duration_sec"),
        "segment_budget.target_duration_sec",
        errors,
    )
    edge_ratio, edge_min_sec, edge_sections = _edge_duration_requirement(
        segment_budget,
        budget_target,
    )
    edge_duration_min = max(budget_target * edge_ratio, edge_min_sec)
    if budget_target > 0 and len(segments) >= 2:
        middle_minimum = max(0.0, len(segments) - len(edge_sections)) * 2.0
        if budget_target + EPSILON < (
            edge_duration_min * len(edge_sections) + middle_minimum
        ):
            errors.append(
                "EDGE_DURATION_INFEASIBLE:目标时长不足以容纳边缘分段最低时长"
            )

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
        if section in edge_sections and duration + EPSILON < edge_duration_min:
            errors.append(
                f"{segment_id}:边缘分段时长必须至少为{edge_duration_min:g}秒"
            )
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
        else:
            _canonicalize_visual_booleans(visual)
        if visual.get("visual_mode") not in allowed_visual_modes:
            errors.append(f"{segment_id}:visual_mode无效")
        if visual.get("camera_motion") not in allowed_camera:
            errors.append(f"{segment_id}:camera_motion无效")
        if visual.get("camera_motion") == "static_hold" and section != "outro":
            errors.append(
                f"{segment_id}:static_hold仅允许closing_card"
            )
        for field in ("show_volume", "show_macro_marker"):
            if type(visual.get(field)) is not bool:
                errors.append(f"{segment_id}:{field}必须是Boolean")
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
            if (
                scene.get("camera_motion") == "static_hold"
                and str(scene.get("template_id") or "") != "closing_card"
            ):
                errors.append(f"{label}:static_hold仅允许closing_card")

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
            if name in edge_sections:
                lower = max(lower, edge_ratio, edge_duration_min / estimated)
                # The dynamic minimum supersedes a legacy fixed upper bound.
                upper = max(upper, lower)
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
        "visual_fact_catalog": visual_fact_catalog,
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
    candidate = normalize_kline_numbers(candidate)
    technical_facts = normalize_kline_numbers(technical_facts)
    market_analysis = normalize_kline_numbers(market_analysis)
    validated_levels = normalize_kline_numbers(validated_levels)
    structure_paths = normalize_kline_numbers(structure_paths)
    forecast_framework = normalize_kline_numbers(forecast_framework)
    count = int(repair_count)
    if count < 0:
        raise ValueError("REPAIR_COUNT_INVALID")
    candidate, reallocation_errors = _reallocate_edge_duration(
        candidate, segment_budget
    )
    validation = validate_segment_plan(
        candidate, segment_budget, technical_facts, market_analysis,
        validated_levels, structure_paths, forecast_framework, macro_timing,
    )
    if reallocation_errors:
        validation["segment_plan_errors"] = (
            reallocation_errors + validation["segment_plan_errors"]
        )
        validation["segment_plan_valid"] = False
    valid = bool(validation["segment_plan_valid"])
    action = "pass" if valid else ("fail" if count >= max_repairs else "repair")
    done = action != "repair"
    errors = validation["segment_plan_errors"]
    contract = {
        "schema_version": "segment-plan-contract-v1",
        "visual_fact_catalog": validation["visual_fact_catalog"],
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
