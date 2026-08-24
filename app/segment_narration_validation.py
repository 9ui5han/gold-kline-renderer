"""Deterministic, retry-safe TOOL-08 narration and media contracts.

This module deliberately does not create a paid TTS job.  Dify owns the LLM
calls and the existing strict ``/v1/tts-jobs/await`` endpoint owns paid work.
The helpers here only validate, build repair context, and package media.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from typing import Any

from .tts_profiles import ProfileError, resolve_profile, validate_performance_plan


NARRATION_SCHEMA_VERSION = "segment-narration-v2"
PERFORMANCE_SCHEMA_VERSION = "tts-performance-v1"
CONTEXT_SCHEMA_VERSION = "segment-narration-context-v1"
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
TRADE_DIRECTIVE_PATTERNS = (
    re.compile(r"\b(?:buy|sell)\s+(?:now|gold|xauusd)\b", re.I),
    re.compile(r"\b(?:you\s+should|i\s+recommend(?:\s+you)?(?:\s+to)?)\s+(?:buy|sell|go\s+long|go\s+short)\b", re.I),
    re.compile(r"\b(?:enter|open)\s+(?:a\s+)?(?:long|short)\b", re.I),
    re.compile(r"买入|卖出|做多|做空"),
)


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _as_object_json(raw: str, field_name: str) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or ""))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field_name}_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field_name}_OBJECT_REQUIRED")
    return value


def _load_contract(raw: str, field_name: str, schema_version: str) -> dict[str, Any]:
    value = _as_object_json(raw, field_name)
    if value.get("schema_version") != schema_version:
        raise ValueError(f"{field_name}_VERSION_INVALID")
    return value


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_id(value: Any, fallback: str = "segment") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip())
    cleaned = cleaned.strip("-")
    return (cleaned or fallback)[:60]


def _duration_budget(item: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    segment_id = str(item.get("segment_id") or "").strip()
    target = _as_float(item.get("duration_target_sec"), 0.0) or 0.0
    minimum = _as_float(item.get("duration_min_sec"), max(0.1, target - 1.5))
    maximum = _as_float(item.get("duration_max_sec"), target + 1.5)
    errors: list[str] = []
    if not segment_id:
        errors.append("SEGMENT_ID_EMPTY")
    if target <= 0 or minimum is None or maximum is None or minimum <= 0 or maximum < minimum:
        errors.append("INVALID_DURATION_BUDGET")
    return {
        "segment_id": segment_id,
        "target_duration_sec": target,
        "duration_min_sec": minimum or 0.0,
        "duration_max_sec": maximum or 0.0,
        "duration_tolerance_sec": max(target - (minimum or target), (maximum or target) - target),
    }, errors


def _voice_duration_profile(narrator_profile_id: str) -> dict[str, Any]:
    profile = resolve_profile(narrator_profile_id, allow_documented=False)
    return {
        "schema_version": "voice-duration-profile-v1",
        "narrator_profile_id": profile["profile_id"],
        "provider": profile["provider"],
        "base_chars_per_second": 14.0,
        "safe_speed_min": float(profile.get("speed_min") or 0.90),
        "safe_speed_max": float(profile.get("speed_max") or 1.05),
        "pause_model": {
            "comma_ms": 160,
            "semicolon_ms": 220,
            "period_ms": 320,
            "question_ms": 360,
        },
    }


def initialize_tool08(
    market_input_v1_json: str,
    levels_v1_json: str,
    technical_v1_json: str,
    macro_context_v1_json: str,
    market_analysis_v1_json: str,
    forecast_v1_json: str,
    segment_plan_v1_json: str,
    narrator_profile_id: str,
    master_request_id: str,
) -> dict[str, Any]:
    """Validate cross-workflow inputs and expose Dify-friendly iteration data."""
    try:
        market_input = _load_contract(market_input_v1_json, "MARKET_INPUT", "market-input-contract-v1")
        levels = _load_contract(levels_v1_json, "LEVELS", "levels-contract-v1")
        technical = _load_contract(technical_v1_json, "TECHNICAL", "technical-contract-v1")
        macro = _load_contract(macro_context_v1_json, "MACRO_CONTEXT", "macro-context-contract-v1")
        analysis = _load_contract(market_analysis_v1_json, "MARKET_ANALYSIS", "market-analysis-contract-v1")
        forecast = _load_contract(forecast_v1_json, "FORECAST", "forecast-contract-v1")
        segment_plan_contract = _load_contract(segment_plan_v1_json, "SEGMENT_PLAN", "segment-plan-contract-v1")
        if segment_plan_contract.get("segment_plan_valid") is not True:
            raise ValueError("SEGMENT_PLAN_NOT_VALID")
        segment_plan = segment_plan_contract.get("segment_plan")
        if not isinstance(segment_plan, dict):
            raise ValueError("SEGMENT_PLAN_OBJECT_REQUIRED")
        # TOOL-08 historically did not receive a master request ID from Dify.
        # Generate it once at init and return it for every later step instead.
        master_id = str(master_request_id or "").strip() or f"tool08-{uuid.uuid4().hex}"
        profile = _voice_duration_profile(narrator_profile_id)
        raw_segments = segment_plan.get("segments")
        if not isinstance(raw_segments, list) or not raw_segments:
            raise ValueError("SEGMENT_PLAN_SEGMENTS_REQUIRED")

        context = {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "market_input": market_input,
            "levels": levels,
            "technical": technical,
            "macro_context": macro,
            "market_analysis": analysis,
            "forecast": forecast,
            "segment_plan": segment_plan,
            "narrator_profile_id": str(narrator_profile_id or "").strip(),
            "master_request_id": master_id,
        }
        segments: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_item in raw_segments:
            if not isinstance(raw_item, dict):
                raise ValueError("SEGMENT_PLAN_ITEM_OBJECT_REQUIRED")
            item = copy.deepcopy(raw_item)
            budget, errors = _duration_budget(item)
            if errors:
                raise ValueError(";".join(errors))
            segment_id = budget["segment_id"]
            if segment_id in seen_ids:
                raise ValueError("SEGMENT_ID_DUPLICATE")
            seen_ids.add(segment_id)
            item["duration_target_sec"] = budget["target_duration_sec"]
            item["duration_min_sec"] = budget["duration_min_sec"]
            item["duration_max_sec"] = budget["duration_max_sec"]
            item["narration_prompt_json"] = _compact_json({
                "item": item,
                "segment_duration_budget": budget,
                "technical": technical,
                "market_analysis": analysis,
                "levels": levels,
                "forecast": forecast,
                "macro_context": macro,
            })
            item["performance_context_json"] = _compact_json({
                "item": item,
                "segment_duration_budget": budget,
                "voice_duration_profile": profile,
            })
            segments.append(item)
        return {
            "schema_version": "segment-narration-init-v1",
            "init_valid": True,
            "init_error": "",
            "master_request_id": master_id,
            "voice_duration_profile": profile,
            "segments": segments,
            "context_json": _compact_json(context),
        }
    except (ValueError, ProfileError) as exc:
        return {
            "schema_version": "segment-narration-init-v1",
            "init_valid": False,
            "init_error": str(exc),
            "master_request_id": "",
            "voice_duration_profile": {},
            "segments": [],
            "context_json": "{}",
        }


def _punctuation_seconds(text: str, pause_model: dict[str, Any]) -> float:
    return (
        text.count(",") * float(pause_model.get("comma_ms", 160))
        + text.count(";") * float(pause_model.get("semicolon_ms", 220))
        + text.count(".") * float(pause_model.get("period_ms", 320))
        + text.count("?") * float(pause_model.get("question_ms", 360))
    ) / 1000.0


def _validate_candidate(
    item: dict[str, Any],
    narration: dict[str, Any],
    performance: dict[str, Any],
    voice_profile: dict[str, Any],
) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    narration = narration if isinstance(narration, dict) else {}
    performance = performance if isinstance(performance, dict) else {}
    profile = voice_profile if isinstance(voice_profile, dict) else {}
    budget, errors = _duration_budget(item)
    expected_id = budget["segment_id"]
    text = str(narration.get("text") or "")

    if narration.get("schema_version") != NARRATION_SCHEMA_VERSION:
        errors.append("NARRATION_SCHEMA_INVALID")
    if not text.strip():
        errors.append("NARRATION_TEXT_EMPTY")
    if str(narration.get("segment_id") or "") != expected_id:
        errors.append("NARRATION_SEGMENT_ID_MISMATCH")
    if narration.get("planning_role") != item.get("planning_role"):
        errors.append("NARRATION_ROLE_MISMATCH")
    if narration.get("fact_anchor_ids") != item.get("fact_anchor_ids"):
        errors.append("NARRATION_FACT_ANCHORS_MISMATCH")
    narration_violation = any(pattern.search(text) for pattern in TRADE_DIRECTIVE_PATTERNS)
    if narration_violation:
        errors.append("PERSONALIZED_TRADE_DIRECTIVE")

    if performance.get("schema_version") != PERFORMANCE_SCHEMA_VERSION:
        errors.append("PERFORMANCE_SCHEMA_INVALID")
    if str(performance.get("segment_id") or "") != expected_id:
        errors.append("PERFORMANCE_SEGMENT_ID_MISMATCH")
    if str(performance.get("text") or "") != text:
        errors.append("PERFORMANCE_TEXT_CHANGED")
    if NUMBER_PATTERN.findall(str(performance.get("text") or "")) != NUMBER_PATTERN.findall(text):
        errors.append("NUMBER_TOKENS_CHANGED")
    try:
        normalized_performance = validate_performance_plan(text, performance)
    except ProfileError as exc:
        errors.append(str(exc))
        normalized_performance = {}

    speed_min = _as_float(profile.get("safe_speed_min"), 0.90)
    speed_max = _as_float(profile.get("safe_speed_max"), 1.05)
    actual_speed = _as_float(normalized_performance.get("speed"))
    if (
        actual_speed is None
        or speed_min is None
        or speed_max is None
        or not speed_min <= actual_speed <= speed_max
    ):
        errors.append("SPEED_OUT_OF_PROFILE_RANGE")

    speed = _as_float(normalized_performance.get("speed"))
    cps = _as_float(profile.get("base_chars_per_second"))
    if speed is None or cps is None or speed <= 0 or cps <= 0:
        estimated = 0.0
        errors.append("DURATION_MODEL_INVALID")
    else:
        estimated = (
            len(re.sub(r"\s+", "", text)) / (cps * speed)
            + _punctuation_seconds(text, profile.get("pause_model") or {})
            + float(normalized_performance.get("pause_after_ms", 0)) / 1000.0
        )
    duration_ok = budget["duration_min_sec"] <= estimated <= budget["duration_max_sec"]
    if not duration_ok:
        errors.append("PRE_TTS_DURATION_OUT_OF_RANGE")
    errors = list(dict.fromkeys(errors))
    return {
        "errors": errors,
        "narration_violation": narration_violation,
        "estimated_total_sec": round(estimated, 3),
        "budget": budget,
        "validated_narration": narration if not errors else {},
        "validated_performance": normalized_performance if not errors else {},
    }


def _tts_request(
    master_request_id: str,
    narrator_profile_id: str,
    narration: dict[str, Any],
    performance: dict[str, Any],
    budget: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    segment_id = _safe_id(narration.get("segment_id"))
    digest = hashlib.sha256(
        f"{master_request_id}|{segment_id}|{revision}".encode("utf-8")
    ).hexdigest()[:12]
    request_id = f"{_safe_id(master_request_id, 'tool08')}-{segment_id}-r{revision}-{digest}"[:100]
    text = str(narration.get("text") or "")
    return {
        "request_id": request_id,
        "narrator_profile_id": str(narrator_profile_id or "").strip(),
        "text": text,
        "narration_json": {
            "schema_version": "narration-tts-v2",
            "segments": [{
                "segment_id": segment_id,
                "text": text,
                "performance_plan": performance,
            }],
        },
        "target_duration_sec": budget["target_duration_sec"],
        "duration_tolerance_sec": budget["duration_tolerance_sec"],
    }


def process_step(
    item: dict[str, Any],
    segment_narration: dict[str, Any] | None,
    segment_performance: dict[str, Any] | None,
    voice_duration_profile: dict[str, Any],
    narrator_profile_id: str,
    master_request_id: str,
    repair_count: int = 0,
    narration_revision: int = 0,
    repair_candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one LLM candidate and return TTS, repair, or fail."""
    if repair_candidate is not None:
        if not isinstance(repair_candidate, dict):
            return _step_failure("REPAIR_CANDIDATE_OBJECT_REQUIRED")
        segment_narration = repair_candidate.get("segment_narration")
        segment_performance = repair_candidate.get("segment_performance")
        try:
            state = _as_object_json(
                str(repair_candidate.get("state_json") or ""),
                "REPAIR_STATE",
            )
            repair_count = int(state["repair_count"])
            narration_revision = int(state["narration_revision"])
        except (KeyError, TypeError, ValueError):
            return _step_failure("REPAIR_STATE_INVALID")
    try:
        repairs = int(repair_count)
        revision = int(narration_revision)
    except (TypeError, ValueError):
        repairs = revision = -1
    if repairs < 0 or revision < 0:
        return _step_failure("REPAIR_STATE_INVALID")

    result = _validate_candidate(item, segment_narration, segment_performance, voice_duration_profile)
    errors = result["errors"]
    base_result = {
        "performance_valid": not errors,
        "performance_error": ";".join(errors),
        "pre_tts_duration_valid": not errors,
        "estimated_total_sec": result["estimated_total_sec"],
        "validated_narration": result["validated_narration"],
        "validated_performance": result["validated_performance"],
    }
    if not errors:
        try:
            tts_request = _tts_request(
                master_request_id,
                narrator_profile_id,
                result["validated_narration"],
                result["validated_performance"],
                result["budget"],
                revision,
            )
        except Exception as exc:  # defensive: malformed user identifiers are non-retryable
            return _step_failure(f"TTS_REQUEST_BUILD_FAILED:{exc}")
        base_result["tts_request"] = tts_request
        return {
            "schema_version": "segment-narration-step-result-v1",
            "action": "pass",
            "done": True,
            "result_json": _compact_json(base_result),
            "repair_prompt_json": "{}",
            "next_state_json": _compact_json({"repair_count": repairs, "narration_revision": revision}),
            "step_error": "",
        }

    kind = "narration" if result["narration_violation"] else "performance"
    if repairs >= 2:
        return {
            "schema_version": "segment-narration-step-result-v1",
            "action": "fail",
            "done": True,
            "result_json": _compact_json(base_result),
            "repair_prompt_json": "{}",
            "next_state_json": _compact_json({"repair_count": repairs, "narration_revision": revision}),
            "step_error": "REPAIR_LIMIT_EXCEEDED",
        }
    next_state = {"repair_count": repairs + 1, "narration_revision": revision + 1}
    repair_prompt = {
        "repair_kind": kind,
        "validator_errors": errors,
        "allowed_changes": (
            ["segment_narration.text", "segment_performance"]
            if kind == "narration" else ["segment_performance"]
        ),
        "item": item,
        "segment_duration_budget": result["budget"],
        "voice_duration_profile": voice_duration_profile,
        "segment_narration": segment_narration,
        "segment_performance": segment_performance,
        "state_json": _compact_json(next_state),
    }
    return {
        "schema_version": "segment-narration-step-result-v1",
        "action": f"repair_{kind}",
        "done": False,
        "result_json": _compact_json(base_result),
        "repair_prompt_json": _compact_json(repair_prompt),
        "next_state_json": _compact_json(next_state),
        "step_error": "",
    }


def _step_failure(error: str) -> dict[str, Any]:
    return {
        "schema_version": "segment-narration-step-result-v1",
        "action": "fail",
        "done": True,
        "result_json": "{}",
        "repair_prompt_json": "{}",
        "next_state_json": "{}",
        "step_error": error,
    }


def confirm_tts_result(
    item: dict[str, Any],
    step_result_json: str,
    tts_result: dict[str, Any],
    repair_count: int = 0,
    narration_revision: int = 0,
    state_json: str = "",
) -> dict[str, Any]:
    """Validate the wrapped ``/v1/tts-jobs/await`` response after paid TTS."""
    if state_json:
        try:
            state = _as_object_json(state_json, "STEP_STATE")
            repair_count = int(state["repair_count"])
            narration_revision = int(state["narration_revision"])
        except (KeyError, TypeError, ValueError):
            return _confirm_fail("STEP_STATE_INVALID")
    try:
        step_result = _as_object_json(step_result_json, "STEP_RESULT")
    except ValueError as exc:
        return _confirm_fail(str(exc))
    job_wrapper = tts_result if isinstance(tts_result, dict) else {}
    job = job_wrapper.get("job") if isinstance(job_wrapper.get("job"), dict) else job_wrapper
    wait_status = str(job_wrapper.get("wait_status") or job.get("status") or "")
    if wait_status != "completed" or str(job.get("status") or "") != "completed":
        error = str(job_wrapper.get("error_code") or job.get("error_code") or "TTS_NOT_COMPLETED")
        return _confirm_fail(error)
    audio_url = str(job.get("audio_url") or "").strip()
    duration = _as_float(job.get("duration_sec"), 0.0) or 0.0
    budget, budget_errors = _duration_budget(item if isinstance(item, dict) else {})
    if budget_errors or not audio_url or duration <= 0:
        return _confirm_fail("TTS_MEDIA_RESULT_INVALID")
    if budget["duration_min_sec"] <= duration <= budget["duration_max_sec"]:
        result = _as_object_json(step_result_json, "STEP_RESULT")
        narration = result.get("validated_narration") if isinstance(result.get("validated_narration"), dict) else {}
        performance = result.get("validated_performance") if isinstance(result.get("validated_performance"), dict) else {}
        media = {
            "segment_id": budget["segment_id"],
            "audio": {"url": audio_url, "duration_sec": duration},
            "narration": {
                "schema_version": narration.get("schema_version"),
                "segment_id": narration.get("segment_id"),
                "text": narration.get("text"),
            },
            "performance_plan": performance,
            "duration_validation": {
                "target_duration_sec": budget["target_duration_sec"],
                "duration_min_sec": budget["duration_min_sec"],
                "duration_max_sec": budget["duration_max_sec"],
                "actual_duration_sec": duration,
                "valid": True,
            },
        }
        return {
            "schema_version": "segment-narration-confirm-result-v1",
            "action": "pass",
            "done": True,
            "result_json": _compact_json({"segment_media_input": media, "actual_duration_sec": duration, "actual_duration_valid": True, "actual_duration_error": ""}),
            "repair_prompt_json": "{}",
            "next_state_json": _compact_json({"repair_count": int(repair_count), "narration_revision": int(narration_revision)}),
            "confirm_error": "",
        }
    if int(repair_count) >= 2:
        return _confirm_fail("ACTUAL_DURATION_REPAIR_LIMIT_EXCEEDED")
    result = _as_object_json(step_result_json, "STEP_RESULT")
    next_state = {"repair_count": int(repair_count) + 1, "narration_revision": int(narration_revision) + 1}
    repair_prompt = {
        "repair_kind": "actual_duration",
        "validator_errors": ["ACTUAL_DURATION_OUT_OF_RANGE"],
        "allowed_changes": ["segment_performance"],
        "item": item,
        "segment_duration_budget": budget,
        "actual_duration_sec": duration,
        "segment_narration": result.get("validated_narration") or {},
        "segment_performance": result.get("validated_performance") or {},
        "state_json": _compact_json(next_state),
    }
    return {
        "schema_version": "segment-narration-confirm-result-v1",
        "action": "repair_performance",
        "done": False,
        "result_json": _compact_json({"actual_duration_sec": duration, "actual_duration_valid": False, "actual_duration_error": "ACTUAL_DURATION_OUT_OF_RANGE"}),
        "repair_prompt_json": _compact_json(repair_prompt),
        "next_state_json": _compact_json(next_state),
        "confirm_error": "",
    }


def _confirm_fail(error: str) -> dict[str, Any]:
    return {
        "schema_version": "segment-narration-confirm-result-v1",
        "action": "fail",
        "done": True,
        "result_json": "{}",
        "repair_prompt_json": "{}",
        "next_state_json": "{}",
        "confirm_error": error,
    }


def complete_tool08(
    segment_media_inputs: list[Any],
    segment_plan_v1_json: str,
    voice_duration_profile: dict[str, Any],
) -> dict[str, Any]:
    """Validate the Iteration collection and produce the three TOOL-08 outputs."""
    try:
        plan_contract = _load_contract(segment_plan_v1_json, "SEGMENT_PLAN", "segment-plan-contract-v1")
        if plan_contract.get("segment_plan_valid") is not True:
            raise ValueError("SEGMENT_PLAN_NOT_VALID")
        plan = plan_contract.get("segment_plan")
        if not isinstance(plan, dict):
            raise ValueError("SEGMENT_PLAN_OBJECT_REQUIRED")
        planned = plan.get("segments")
        if not isinstance(planned, list):
            raise ValueError("SEGMENT_PLAN_SEGMENTS_REQUIRED")
    except ValueError as exc:
        return _complete_failure([], voice_duration_profile, str(exc))
    if any(not isinstance(item, dict) for item in planned):
        return _complete_failure([], voice_duration_profile, "SEGMENT_PLAN_ITEM_OBJECT_REQUIRED", segment_media_inputs)
    expected_ids = [str(item.get("segment_id") or "") for item in planned]
    if not expected_ids or any(not segment_id for segment_id in expected_ids) or len(set(expected_ids)) != len(expected_ids):
        return _complete_failure([], voice_duration_profile, "SEGMENT_PLAN_IDS_INVALID", segment_media_inputs)
    media_ids = [
        str(item.get("segment_id") or "")
        for item in (segment_media_inputs or [])
        if isinstance(item, dict)
    ]
    if (
        len(media_ids) != len(segment_media_inputs or [])
        or any(not segment_id for segment_id in media_ids)
        or len(set(media_ids)) != len(media_ids)
    ):
        return _complete_failure([], voice_duration_profile, "SEGMENT_MEDIA_IDS_INVALID", segment_media_inputs)
    media_by_id = {
        str(item.get("segment_id") or ""): item
        for item in (segment_media_inputs or [])
        if isinstance(item, dict)
    }
    bad_ids: list[str] = []
    duration_validations: list[dict[str, Any]] = []
    for expected_id in expected_ids:
        media = media_by_id.get(expected_id) or {}
        audio = media.get("audio") if isinstance(media.get("audio"), dict) else {}
        validation = media.get("duration_validation") if isinstance(media.get("duration_validation"), dict) else {}
        valid = bool(audio.get("url")) and (_as_float(audio.get("duration_sec"), 0.0) or 0.0) > 0 and validation.get("valid") is True
        duration_validations.append(copy.deepcopy(validation) if validation else {"segment_id": expected_id, "valid": False})
        if not valid:
            bad_ids.append(expected_id)
    if len(segment_media_inputs or []) != len(expected_ids) or len(media_by_id) != len(expected_ids):
        for media_id in media_by_id:
            if media_id not in expected_ids and media_id not in bad_ids:
                bad_ids.append(media_id)
    if bad_ids:
        return _complete_failure(bad_ids, voice_duration_profile, "SEGMENT_MEDIA_INVALID", segment_media_inputs, duration_validations)
    ordered_media = [media_by_id[segment_id] for segment_id in expected_ids]
    payload = {
        "schema_version": "segment-media-contract-v1",
        "voice_duration_profile": voice_duration_profile if isinstance(voice_duration_profile, dict) else {},
        "segment_media_inputs": ordered_media,
        "segment_audio_valid": True,
        "bad_segment_ids": [],
        "duration_validations": duration_validations,
    }
    return {
        "schema_version": "segment-narration-complete-result-v1",
        "complete_valid": True,
        "complete_error": "",
        "segment_media_v1_json": _compact_json(payload),
        "segment_audio_valid": True,
        "bad_segment_ids_json": "[]",
    }


def _complete_failure(
    bad_ids: list[str],
    voice_duration_profile: dict[str, Any],
    error: str,
    inputs: list[Any] | None = None,
    duration_validations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": "segment-media-contract-v1",
        "voice_duration_profile": voice_duration_profile if isinstance(voice_duration_profile, dict) else {},
        "segment_media_inputs": inputs or [],
        "segment_audio_valid": False,
        "bad_segment_ids": bad_ids,
        "duration_validations": duration_validations or [],
    }
    return {
        "schema_version": "segment-narration-complete-result-v1",
        "complete_valid": False,
        "complete_error": error,
        "segment_media_v1_json": _compact_json(payload),
        "segment_audio_valid": False,
        "bad_segment_ids_json": _compact_json(bad_ids),
    }
