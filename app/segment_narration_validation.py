"""Deterministic, retry-safe TOOL-08 narration and media contracts.

This module deliberately does not create a paid TTS job.  Dify owns the LLM
calls and the existing strict ``/v1/tts-jobs/await`` endpoint owns paid work.
The helpers here only validate, build repair context, and package media.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from typing import Any

from .kline_precision import normalize_kline_numbers
from .tts_profiles import ProfileError, resolve_profile, validate_performance_plan


NARRATION_SCHEMA_VERSION = "segment-narration-v2"
PERFORMANCE_SCHEMA_VERSION = "tts-performance-v1"
CONTEXT_SCHEMA_VERSION = "segment-narration-context-v1"
NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?")
ENGLISH_WORD_PATTERN = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)?")
TIME_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?(?![A-Za-z])"
)
TIMEFRAME_PATTERN = re.compile(r"(?<![A-Za-z0-9])(\d+)([mhdw])\b", re.I)
PERCENT_PATTERN = re.compile(r"(?<![A-Za-z0-9])([+-]?)(\d+)(?:\.(\d+))?%(?![A-Za-z])")
LEVEL_PATTERN = re.compile(r"\b([RrSs])(\d+)\b")
NUMBER_RANGE_PATTERN = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)[–—-](\d+(?:\.\d+)?)(?![A-Za-z0-9])")
SPOKEN_NUMBER_PATTERN = re.compile(r"(?<![A-Za-z0-9])([+-]?)(\d+)(?:\.(\d+))?(?![A-Za-z0-9])")
# Minimax can take materially longer on short, word-dense English than a
# character-only model predicts.  This is a conservative lower-bound rate
# used before creating a paid TTS job.
MIN_ENGLISH_WORDS_PER_SECOND = 2.2
# TOOL-08's global default matches the upstream 60-second plan: the complete
# spoken timeline may exceed its authored total by at most three seconds.
DEFAULT_GLOBAL_DURATION_TOLERANCE_SEC = 3.0
# Calibrated against completed MiniMax segment audio.  Unknown voices retain
# the conservative fallback until they have their own measured profile.
VOICE_DURATION_CALIBRATIONS = {
    "mm_finance_male_02": {
        "base_chars_per_second": 20.0,
        "base_words_per_second": 2.6,
        # Median actual/estimated ratio from completed TOOL-08 audio.
        "duration_factor": 0.83,
    },
}
TRADE_DIRECTIVE_PATTERNS = (
    re.compile(r"\b(?:buy|sell)\s+(?:now|gold|xauusd)\b", re.I),
    re.compile(r"\b(?:you\s+should|i\s+recommend(?:\s+you)?(?:\s+to)?)\s+(?:buy|sell|go\s+long|go\s+short)\b", re.I),
    re.compile(r"\b(?:enter|open)\s+(?:a\s+)?(?:long|short)\b", re.I),
    re.compile(r"买入|卖出|做多|做空"),
)


_ONES = (
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
)
_TENS = (
    "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
    "eighty", "ninety",
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


def _integer_to_english(value: int) -> str:
    """Render a non-negative integer in the pronunciation used by TTS."""
    if not 0 <= value <= 999_999_999:
        raise ValueError("SPOKEN_INTEGER_OUT_OF_RANGE")

    if value >= 1_000_000:
        millions, remainder = divmod(value, 1_000_000)
        prefix = f"{_integer_to_english(millions)} million"
        return prefix if not remainder else f"{prefix} {_integer_to_english(remainder)}"

    if value >= 1_000:
        thousands, remainder = divmod(value, 1_000)
        prefix = f"{_integer_to_english(thousands)} thousand"
        return prefix if not remainder else f"{prefix} {_integer_to_english(remainder)}"

    parts: list[str] = []
    if value >= 100:
        parts.extend([_ONES[value // 100], "hundred"])
        value %= 100
    if value >= 20:
        tens = _TENS[value // 10]
        ones = value % 10
        parts.append(f"{tens}-{_ONES[ones]}" if ones else tens)
    elif value:
        parts.append(_ONES[value])
    return " ".join(parts) or "zero"


def _spoken_number(sign: str, whole: str, fraction: str | None = None) -> str:
    number = _integer_to_english(int(whole))
    if fraction:
        number = f"{number} point {' '.join(_ONES[int(digit)] for digit in fraction)}"
    if sign == "+":
        return f"plus {number}"
    if sign == "-":
        return f"minus {number}"
    return number


def _spoken_tts_text(display_text: str) -> str:
    """Keep subtitle text intact while making common market tokens explicit for TTS."""
    text = str(display_text or "")

    def replace_time(match: re.Match[str]) -> str:
        hour = int(match.group(1))
        minute = int(match.group(2))
        if hour > 12 or minute > 59:
            return match.group(0)
        minute_text = "o " + _integer_to_english(minute) if minute < 10 else _integer_to_english(minute)
        return f"{_integer_to_english(hour)} {minute_text} {match.group(3).lower()} m"

    def replace_timeframe(match: re.Match[str]) -> str:
        number = _integer_to_english(int(match.group(1)))
        unit = match.group(2).lower()
        names = {"m": "minute", "h": "hour", "d": "day", "w": "week"}
        label = names[unit]
        return f"{number} {label if match.group(1) == '1' else label + 's'}"

    def replace_percent(match: re.Match[str]) -> str:
        return f"{_spoken_number(match.group(1), match.group(2), match.group(3))} percent"

    def replace_level(match: re.Match[str]) -> str:
        return f"{match.group(1).upper()} {_integer_to_english(int(match.group(2)))}"

    def replace_range(match: re.Match[str]) -> str:
        left = match.group(1).split(".", 1)
        right = match.group(2).split(".", 1)
        return f"{_spoken_number('', left[0], left[1] if len(left) == 2 else None)} to {_spoken_number('', right[0], right[1] if len(right) == 2 else None)}"

    def replace_number(match: re.Match[str]) -> str:
        return _spoken_number(match.group(1), match.group(2), match.group(3))

    text = TIME_PATTERN.sub(replace_time, text)
    text = TIMEFRAME_PATTERN.sub(replace_timeframe, text)
    text = PERCENT_PATTERN.sub(replace_percent, text)
    text = LEVEL_PATTERN.sub(replace_level, text)
    text = NUMBER_RANGE_PATTERN.sub(replace_range, text)
    return SPOKEN_NUMBER_PATTERN.sub(replace_number, text)


def _spoken_performance(display_performance: dict[str, Any], spoken_text: str) -> dict[str, Any]:
    """Translate spoken cue text for the provider's payload."""
    performance = copy.deepcopy(display_performance)
    performance["text"] = spoken_text
    cues = performance.get("cues")
    if isinstance(cues, list):
        for cue in cues:
            if isinstance(cue, dict):
                cue["text"] = _spoken_tts_text(str(cue.get("text") or ""))
    return performance


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


def _duration_repair_budget(
    budget: dict[str, Any],
    spoken_text: str,
    estimated_total_sec: float,
    voice_profile: dict[str, Any],
) -> dict[str, Any]:
    """Expose deterministic provider-facing duration facts to the repair LLM."""
    target_duration = _as_float(budget.get("target_duration_sec"), 0.0) or 0.0
    duration_max = _as_float(budget.get("duration_max_sec"), 0.0) or 0.0
    safe_max = max(0.1, duration_max - 0.30)
    pause_model = voice_profile.get("pause_model") or {}
    pause_after_ms = 0
    punctuation_pause_sec = _punctuation_seconds(spoken_text, pause_model)
    words_per_second = _profile_words_per_second(voice_profile)
    spoken_word_count = len(ENGLISH_WORD_PATTERN.findall(spoken_text))
    max_spoken_words = max(
        0,
        math.floor(
            max(0.0, safe_max - punctuation_pause_sec - pause_after_ms / 1000.0)
            * words_per_second
            * 1.05
        ),
    )
    return {
        **copy.deepcopy(budget),
        "spoken_text": spoken_text,
        "spoken_word_count": spoken_word_count,
        "spoken_words_per_second": round(words_per_second, 3),
        "punctuation_pause_sec": round(punctuation_pause_sec, 3),
        "estimated_spoken_duration_sec": round(estimated_total_sec, 3),
        "safe_duration_max_sec": round(safe_max, 3),
        # The repair decision is based on the original segment proportion,
        # not on the wider display-duration range.  Keep the old safe-max
        # field for compatibility, but expose the target-based values used by
        # the aggregate budget gate explicitly.
        "repair_target_sec": round(target_duration, 3),
        "duration_overrun_sec": round(max(0.0, estimated_total_sec - target_duration), 3),
        "duration_margin_sec": round(target_duration - estimated_total_sec, 3),
        "max_spoken_word_budget": max_spoken_words,
        "min_spoken_word_budget": 0,
        "target_spoken_word_budget": 0,
        "repair_speed": 1.05,
        "repair_pause_after_ms": pause_after_ms,
    }


def _compact_repair_item(item: dict[str, Any]) -> dict[str, Any]:
    """Keep a repair prompt focused on editable narration facts and budget.

    Iteration items also carry the original generation prompt, which embeds
    every upstream contract and can dwarf the actual repair instructions.
    That context is redundant here: resolved visual facts and the current
    narration are the authoritative repair inputs.
    """
    allowed_keys = (
        "segment_id", "order", "section", "planning_role", "scenario_id",
        "fact_anchor_ids", "content_goal", "importance", "speech_style",
        "duration_target_sec", "duration_min_sec", "duration_max_sec",
        "draft_delivery", "draft_emotion", "draft_speed",
        "draft_sentence_count", "draft_sentence_pause_ms",
        "duration_calibration_factor", "accepted_min_estimated_sec",
        "accepted_max_estimated_sec", "draft_min_spoken_words",
        "draft_target_spoken_words", "draft_max_spoken_words",
        "resolved_visual_facts", "_video_hard_max_sec",
        "_video_preferred_max_sec", "_global_overrun_sec",
        "_global_tolerance_sec", "_segment_overrun_sec",
        "_duration_reduction_required_sec", "_accepted_max_estimated_sec",
        "_pre_repair_estimated_sec", "_duration_repair_authorized",
        "_force_duration_repair",
    )
    return {
        key: copy.deepcopy(item[key])
        for key in allowed_keys
        if key in item
    }


def _visual_fact_catalog_map(catalog: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(catalog, dict) or catalog.get("schema_version") != "visual-fact-catalog-v1":
        raise ValueError("VISUAL_FACT_CATALOG_REQUIRED")
    facts = catalog.get("facts")
    if not isinstance(facts, list) or not facts:
        raise ValueError("VISUAL_FACT_CATALOG_EMPTY")
    result: dict[str, dict[str, Any]] = {}
    for fact in facts:
        if not isinstance(fact, dict) or not str(fact.get("anchor_id") or "").strip():
            raise ValueError("VISUAL_FACT_CATALOG_ITEM_INVALID")
        anchor_id = str(fact["anchor_id"]).strip()
        if anchor_id in result:
            raise ValueError("VISUAL_FACT_CATALOG_DUPLICATE")
        result[anchor_id] = copy.deepcopy(fact)
    return result


def _visual_anchor_ids(item: dict[str, Any]) -> list[str]:
    anchors: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        anchor = str(value or "").strip()
        if anchor and anchor not in seen:
            seen.add(anchor)
            anchors.append(anchor)

    for value in item.get("fact_anchor_ids") or []:
        add(value)
    visual = item.get("visual") if isinstance(item.get("visual"), dict) else {}
    for value in visual.get("highlight_levels") or []:
        raw = str(value or "").strip()
        add(raw)
    for scene in item.get("scenes") or []:
        if not isinstance(scene, dict):
            continue
        for event in scene.get("overlay_events") or []:
            if not isinstance(event, dict):
                continue
            for value in event.get("fact_anchor_ids") or []:
                add(value)
    return anchors


def _resolve_visual_facts(item: dict[str, Any], catalog: Any) -> list[dict[str, Any]]:
    facts = _visual_fact_catalog_map(catalog)
    resolved: list[dict[str, Any]] = []
    for anchor_id in _visual_anchor_ids(item):
        fact = facts.get(anchor_id)
        if fact is None:
            level_anchor = (
                anchor_id
                if anchor_id.startswith("level:")
                else f"level:{anchor_id}"
            )
            fact = facts.get(level_anchor)
        if fact is None:
            raise ValueError(f"VISUAL_FACT_NOT_RESOLVED:{anchor_id}")
        if not any(value.get("anchor_id") == fact["anchor_id"] for value in resolved):
            resolved.append(copy.deepcopy(fact))
    if not resolved:
        raise ValueError("VISUAL_FACTS_REQUIRED")
    return resolved


def _rescale_visual_timeline(item: dict[str, Any], actual_duration: float) -> tuple[list[dict[str, Any]], float, bool]:
    scenes = item.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("SCENES_REQUIRED")
    planned_duration = _as_float(item.get("duration_target_sec"), 0.0) or 0.0
    if planned_duration <= 0:
        raise ValueError("PLANNED_SEGMENT_DURATION_REQUIRED")
    time_scale = actual_duration / planned_duration
    adjusted = not math.isclose(time_scale, 1.0, rel_tol=0.0, abs_tol=0.0005)
    scaled: list[dict[str, Any]] = []
    for raw_scene in scenes:
        if not isinstance(raw_scene, dict):
            raise ValueError("SCENE_OBJECT_REQUIRED")
        scene = copy.deepcopy(raw_scene)
        scene["start_sec"] = round((_as_float(scene.get("start_sec"), 0.0) or 0.0) * time_scale, 3)
        scene["duration_sec"] = round((_as_float(scene.get("duration_sec"), 0.0) or 0.0) * time_scale, 3)
        events = scene.get("overlay_events")
        if events is not None:
            if not isinstance(events, list):
                raise ValueError("OVERLAY_EVENTS_REQUIRED")
            for event in events:
                if not isinstance(event, dict):
                    raise ValueError("OVERLAY_EVENT_OBJECT_REQUIRED")
                event["start_sec"] = round((_as_float(event.get("start_sec"), 0.0) or 0.0) * time_scale, 3)
                event["duration_sec"] = round((_as_float(event.get("duration_sec"), 0.0) or 0.0) * time_scale, 3)
        scaled.append(scene)
    last = scaled[-1]
    last_start = _as_float(last.get("start_sec"), 0.0) or 0.0
    last["duration_sec"] = round(max(0.0, actual_duration - last_start), 3)
    return scaled, time_scale, adjusted


def _voice_duration_profile(narrator_profile_id: str) -> dict[str, Any]:
    profile = resolve_profile(narrator_profile_id, allow_documented=False)
    calibration = VOICE_DURATION_CALIBRATIONS.get(profile["profile_id"], {})
    return {
        "schema_version": "voice-duration-profile-v1",
        "narrator_profile_id": profile["profile_id"],
        "provider": profile["provider"],
        "base_chars_per_second": float(calibration.get("base_chars_per_second", 14.0)),
        "base_words_per_second": float(
            calibration.get("base_words_per_second", MIN_ENGLISH_WORDS_PER_SECOND)
        ),
        "duration_calibration_factor": float(
            calibration.get("duration_factor", 1.0)
        ),
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
        market_input = normalize_kline_numbers(market_input)
        levels = normalize_kline_numbers(levels)
        technical = normalize_kline_numbers(technical)
        analysis = normalize_kline_numbers(analysis)
        forecast = normalize_kline_numbers(forecast)
        segment_plan_contract = normalize_kline_numbers(segment_plan_contract)
        if segment_plan_contract.get("segment_plan_valid") is not True:
            raise ValueError("SEGMENT_PLAN_NOT_VALID")
        segment_plan = segment_plan_contract.get("segment_plan")
        if not isinstance(segment_plan, dict):
            raise ValueError("SEGMENT_PLAN_OBJECT_REQUIRED")
        visual_fact_catalog = segment_plan_contract.get("visual_fact_catalog")
        _visual_fact_catalog_map(visual_fact_catalog)
        # The MASTER workflow creates this once. TOOL-08 must preserve it so
        # TTS, TOOL-09, and TOOL-10 share the same idempotency key.
        master_id = str(master_request_id or "").strip()
        if not master_id:
            raise ValueError("MASTER_REQUEST_ID_REQUIRED")
        profile = _voice_duration_profile(narrator_profile_id)
        video_config = (
            market_input.get("job_config", {}).get("video", {})
            if isinstance(market_input.get("job_config"), dict)
            else {}
        )
        video_hard_max = _as_float(video_config.get("hard_max_sec"))
        video_preferred_max = _as_float(video_config.get("preferred_max_sec"))
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
            "visual_fact_catalog": visual_fact_catalog,
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
            if video_hard_max is not None and video_hard_max > 0:
                # Internal scheduling metadata travels with the iteration item
                # but is not copied into the public segment-media contract.
                item["_video_hard_max_sec"] = video_hard_max
            if video_preferred_max is not None and video_preferred_max > 0:
                item["_video_preferred_max_sec"] = video_preferred_max
            if not isinstance(item.get("visual"), dict):
                raise ValueError(f"{segment_id}:VISUAL_PLAN_REQUIRED")
            if not isinstance(item.get("scenes"), list) or not item.get("scenes"):
                raise ValueError(f"{segment_id}:SCENES_REQUIRED")
            item["resolved_visual_facts"] = _resolve_visual_facts(item, visual_fact_catalog)
            item.update(
                _draft_spoken_budget(
                    item,
                    profile,
                    {
                        "resolved_visual_facts": item["resolved_visual_facts"],
                        "authoritative_price_map": (
                            forecast.get("active_levels", {}).get(
                                "authoritative_price_map",
                                {},
                            )
                            if isinstance(forecast.get("active_levels"), dict)
                            else {}
                        ),
                    },
                )
            )
            item["narration_prompt_json"] = _compact_json({
                "item": item,
                "segment_duration_budget": budget,
                "voice_duration_profile": profile,
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


def _estimated_spoken_seconds(
    text: str,
    chars_per_second: float,
    speed: float,
    pause_model: dict[str, Any],
    pause_after_ms: int,
    words_per_second: float = MIN_ENGLISH_WORDS_PER_SECOND,
) -> tuple[float, float]:
    """Return conservative total and word-based duration estimates."""
    pause_seconds = (
        _punctuation_seconds(text, pause_model)
        + float(pause_after_ms) / 1000.0
    )
    character_speech_seconds = len(re.sub(r"\s+", "", text)) / (chars_per_second * speed)
    word_rate = words_per_second if words_per_second > 0 else MIN_ENGLISH_WORDS_PER_SECOND
    # The provider's word throughput is measured at speed 1.0.  Applying the
    # selected speed here keeps the pre-TTS estimate aligned with the text and
    # performance plan that will actually be sent to the provider.
    word_speech_seconds = len(ENGLISH_WORD_PATTERN.findall(text)) / (word_rate * speed)
    return (
        max(character_speech_seconds, word_speech_seconds) + pause_seconds,
        word_speech_seconds + pause_seconds,
    )


def _profile_words_per_second(profile: dict[str, Any]) -> float:
    value = _as_float(profile.get("base_words_per_second"))
    return value if value is not None and value > 0 else MIN_ENGLISH_WORDS_PER_SECOND


def _numeric_spoken_costs(value: Any) -> dict[str, int]:
    """Return the spoken-word cost of numeric tokens available to one segment."""
    tokens: list[str] = []

    def collect(current: Any) -> None:
        if isinstance(current, dict):
            for child in current.values():
                collect(child)
        elif isinstance(current, list):
            for child in current:
                collect(child)
        elif isinstance(current, str):
            tokens.extend(NUMBER_PATTERN.findall(current))
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            number = float(current)
            if math.isfinite(number):
                tokens.append(str(current))

    collect(value)
    costs: dict[str, int] = {}
    for token in tokens:
        if token in costs:
            continue
        spoken = _spoken_tts_text(token)
        costs[token] = len(ENGLISH_WORD_PATTERN.findall(spoken))
    return costs


def _duration_factor(voice_profile: dict[str, Any]) -> float:
    return (
        _as_float(voice_profile.get("duration_calibration_factor"), 1.0)
        or 1.0
    )


def _draft_spoken_budget(
    item: dict[str, Any],
    voice_profile: dict[str, Any],
    numeric_sources: Any = None,
) -> dict[str, Any]:
    """Build a fixed delivery preset and two-sided word budget for T8-05."""
    target = _as_float(item.get("duration_target_sec"), 0.0) or 0.0
    minimum = _as_float(item.get("duration_min_sec"), target) or target
    maximum = _as_float(item.get("duration_max_sec"), target) or target
    words_per_second = _profile_words_per_second(voice_profile)
    pause_model = voice_profile.get("pause_model") or {}
    role = str(item.get("planning_role") or "")
    presets = {
        "opening_hook": ("compact", "neutral", 1.0, 1, 0),
        "technical_context": ("calm_analysis", "neutral", 0.98, 2, 120),
        "macro_context": ("calm_analysis", "serious", 0.98, 2, 120),
        "primary_forecast": ("calm_analysis", "serious", 0.98, 2, 120),
        "alternate_forecast": ("caution", "serious", 0.98, 2, 120),
        "closing_question": ("compact", "calm", 1.0, 1, 0),
    }
    delivery, emotion, speed, sentence_count, sentence_pause_ms = presets.get(
        role,
        ("calm_analysis", "neutral", 0.98, 2, 120),
    )
    speed_min = _as_float(voice_profile.get("safe_speed_min"), 0.90) or 0.90
    speed_max = _as_float(voice_profile.get("safe_speed_max"), 1.05) or 1.05
    speed = min(speed_max, max(speed_min, speed))
    duration_factor = _duration_factor(voice_profile)
    punctuation_ms = (
        float(pause_model.get("question_ms", 360))
        if role == "closing_question"
        else sentence_count * float(pause_model.get("period_ms", 320))
    )
    punctuation_sec = punctuation_ms / 1000.0
    sentence_pause_sec = max(0, sentence_count - 1) * sentence_pause_ms / 1000.0
    safety_margin_sec = 0.30

    def words_for_duration(duration: float, rounding: str) -> int:
        raw_total = duration / duration_factor
        usable = max(0.1, raw_total - punctuation_sec - sentence_pause_sec)
        value = usable * words_per_second * speed
        return max(1, math.ceil(value) if rounding == "ceil" else math.floor(value))

    accepted_min = min(maximum, minimum + safety_margin_sec)
    accepted_max = max(accepted_min, maximum - safety_margin_sec)
    min_spoken_words = words_for_duration(accepted_min, "ceil")
    target_spoken_words = words_for_duration(target, "ceil")
    max_spoken_words = words_for_duration(accepted_max, "floor")
    max_spoken_words = max(min_spoken_words, max_spoken_words)
    target_spoken_words = min(max_spoken_words, max(min_spoken_words, target_spoken_words))
    return {
        "draft_duration_cap_sec": round(target, 3),
        "draft_delivery": delivery,
        "draft_emotion": emotion,
        "draft_speed": round(speed, 3),
        "draft_sentence_count": sentence_count,
        "draft_sentence_pause_ms": sentence_pause_ms,
        "duration_calibration_factor": round(duration_factor, 3),
        "accepted_min_estimated_sec": round(accepted_min, 3),
        "accepted_max_estimated_sec": round(accepted_max, 3),
        "draft_punctuation_sec": round(punctuation_sec, 3),
        "draft_sentence_pause_sec": round(sentence_pause_sec, 3),
        "draft_safety_margin_sec": round(safety_margin_sec, 3),
        "draft_min_spoken_words": min_spoken_words,
        "draft_target_spoken_words": target_spoken_words,
        "draft_max_spoken_words": max_spoken_words,
        "numeric_spoken_costs": _numeric_spoken_costs(
            numeric_sources
            if numeric_sources is not None
            else item.get("resolved_visual_facts") or []
        ),
    }


def _as_candidate_object(value: Any, field_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return copy.deepcopy(value)
    if isinstance(value, str):
        return _as_object_json(value, field_name)
    raise ValueError(f"{field_name}_OBJECT_REQUIRED")


def _sentence_level_estimate(
    narration: dict[str, Any],
    performance: dict[str, Any],
    voice_profile: dict[str, Any],
) -> tuple[float, float] | None:
    """Estimate each sentence with its own speed and trailing pause."""
    narration_sentences = narration.get("sentences")
    performance_sentences = performance.get("sentences")
    if narration_sentences is None and performance_sentences is None:
        return None
    if (
        not isinstance(narration_sentences, list)
        or not narration_sentences
        or not isinstance(performance_sentences, list)
        or len(narration_sentences) != len(performance_sentences)
    ):
        raise ValueError("SENTENCE_PLAN_INVALID")

    pause_model = voice_profile.get("pause_model") or {}
    chars_per_second = _as_float(voice_profile.get("base_chars_per_second"))
    words_per_second = _profile_words_per_second(voice_profile)
    if chars_per_second is None or chars_per_second <= 0:
        raise ValueError("DURATION_MODEL_INVALID")
    total = 0.0
    word_total = 0.0
    for index, (narration_sentence, performance_sentence) in enumerate(
        zip(narration_sentences, performance_sentences), start=1
    ):
        if not isinstance(narration_sentence, dict) or not isinstance(
            performance_sentence, dict
        ):
            raise ValueError(f"SENTENCE_{index}_OBJECT_INVALID")
        narration_index = int(narration_sentence.get("index") or index)
        performance_index = int(performance_sentence.get("index") or index)
        if narration_index != performance_index:
            raise ValueError(f"SENTENCE_{index}_INDEX_MISMATCH")
        display_sentence = str(narration_sentence.get("text") or "").strip()
        performance_text = str(performance_sentence.get("text") or "").strip()
        if not display_sentence or display_sentence != performance_text:
            raise ValueError(f"SENTENCE_{index}_TEXT_MISMATCH")
        plan = performance_sentence.get("performance_plan")
        if not isinstance(plan, dict):
            plan = performance_sentence
        speed = _as_float(plan.get("speed"))
        pause_after_ms = plan.get("pause_after_ms")
        if speed is None or not 0.90 <= speed <= 1.05:
            raise ValueError(f"SENTENCE_{index}_SPEED_INVALID")
        if isinstance(pause_after_ms, bool) or not isinstance(pause_after_ms, int):
            raise ValueError(f"SENTENCE_{index}_PAUSE_INVALID")
        if not 0 <= pause_after_ms <= 650:
            raise ValueError(f"SENTENCE_{index}_PAUSE_INVALID")
        spoken_sentence = _spoken_tts_text(display_sentence)
        sentence_total, sentence_words = _estimated_spoken_seconds(
            spoken_sentence,
            chars_per_second,
            speed,
            pause_model,
            pause_after_ms,
            words_per_second,
        )
        factor = _duration_factor(voice_profile)
        total += sentence_total * factor
        word_total += sentence_words * factor
    return total, word_total


def rebalance_tool08(
    candidates: list[Any],
    voice_duration_profile: dict[str, Any],
) -> dict[str, Any]:
    """Prepare all drafts against one global spoken-duration budget.

    The estimate is calculated from the provider-facing spoken text. Segment
    targets remain at their authored proportions. Spare time from shorter
    segments offsets longer segments first. Only the amount by which the
    estimated full narration exceeds the video hard maximum is assigned back
    to overrun segments as explicit one-shot repair targets before paid TTS.
    """
    profile = voice_duration_profile if isinstance(voice_duration_profile, dict) else {}
    cps = _as_float(profile.get("base_chars_per_second"))
    if cps is None or cps <= 0:
        return {
            "schema_version": "segment-narration-schedule-v1",
            "schedule_valid": False,
            "schedule_error": "DURATION_MODEL_INVALID",
            "content_fit_valid": False,
            "content_fit_error": "DURATION_MODEL_INVALID",
            "scheduled_items": [],
            "scheduled_total_sec": 0.0,
        }
    prepared: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        for raw in candidates or []:
            candidate = _as_candidate_object(raw, "SCHEDULE_CANDIDATE")
            item = _as_candidate_object(candidate.get("item"), "SCHEDULE_ITEM")
            narration = _as_candidate_object(
                candidate.get("segment_narration"), "SCHEDULE_NARRATION"
            )
            performance = _as_candidate_object(
                candidate.get("segment_performance"), "SCHEDULE_PERFORMANCE"
            )
            budget, budget_errors = _duration_budget(item)
            if budget_errors:
                raise ValueError(";".join(budget_errors))
            segment_id = budget["segment_id"]
            if segment_id in seen_ids:
                raise ValueError("SCHEDULE_SEGMENT_ID_DUPLICATE")
            seen_ids.add(segment_id)
            if str(narration.get("segment_id") or "") != segment_id:
                raise ValueError("SCHEDULE_NARRATION_ID_MISMATCH")
            if narration.get("planning_role") != item.get("planning_role"):
                raise ValueError("SCHEDULE_NARRATION_ROLE_MISMATCH")
            if narration.get("fact_anchor_ids") != item.get("fact_anchor_ids"):
                raise ValueError("SCHEDULE_NARRATION_FACT_ANCHORS_MISMATCH")
            display_text = str(narration.get("text") or "")
            display_performance = validate_performance_plan(display_text, performance)
            spoken_text = _spoken_tts_text(display_text)
            spoken_performance = validate_performance_plan(
                spoken_text,
                _spoken_performance(display_performance, spoken_text),
            )
            speed = _as_float(spoken_performance.get("speed"))
            if speed is None or speed <= 0:
                raise ValueError("SCHEDULE_SPEED_INVALID")
            estimated, _ = _estimated_spoken_seconds(
                spoken_text,
                cps,
                speed,
                profile.get("pause_model") or {},
                int(spoken_performance.get("pause_after_ms", 0)),
                _profile_words_per_second(profile),
            )
            estimated *= _duration_factor(profile)
            prepared.append({
                "item": item,
                "segment_narration": narration,
                "segment_performance": display_performance,
                "display_text": display_text,
                "spoken_text": spoken_text,
                "estimated_spoken_sec": estimated,
                "original_target_sec": budget["target_duration_sec"],
            })
    except (ValueError, ProfileError) as exc:
        return {
            "schema_version": "segment-narration-schedule-v1",
            "schedule_valid": False,
            "schedule_error": str(exc),
            "content_fit_valid": False,
            "content_fit_error": str(exc),
            "scheduled_items": [],
            "scheduled_total_sec": 0.0,
        }

    if not prepared:
        return {
            "schema_version": "segment-narration-schedule-v1",
            "schedule_valid": False,
            "schedule_error": "SCHEDULE_CANDIDATES_EMPTY",
            "content_fit_valid": False,
            "content_fit_error": "SCHEDULE_CANDIDATES_EMPTY",
            "scheduled_items": [],
            "scheduled_total_sec": 0.0,
        }

    original_total = sum(entry["original_target_sec"] for entry in prepared)
    explicit_hard_max = next(
        (
            _as_float(entry["item"].get("_video_hard_max_sec"))
            for entry in prepared
            if _as_float(entry["item"].get("_video_hard_max_sec")) is not None
        ),
        None,
    )
    hard_max = (
        explicit_hard_max
        if explicit_hard_max is not None
        else original_total + DEFAULT_GLOBAL_DURATION_TOLERANCE_SEC
    )
    hard_max = max(original_total, hard_max)
    explicit_preferred_max = next(
        (
            _as_float(entry["item"].get("_video_preferred_max_sec"))
            for entry in prepared
            if _as_float(entry["item"].get("_video_preferred_max_sec")) is not None
        ),
        None,
    )
    pre_tts_max = (
        min(hard_max, explicit_preferred_max)
        if explicit_preferred_max is not None and explicit_preferred_max > 0
        else hard_max
    )
    # A malformed configuration must not require a rewrite of an otherwise
    # valid planned timeline.  The normal ceiling can never be below the
    # original segment-plan total; the hard ceiling remains the final guard.
    pre_tts_max = max(original_total, pre_tts_max)

    overruns = [
        max(0.0, entry["estimated_spoken_sec"] - entry["original_target_sec"])
        for entry in prepared
    ]
    positive_segment_overrun = sum(overruns)
    estimated_total = sum(entry["estimated_spoken_sec"] for entry in prepared)
    # Keep the authored total and segment proportions.  The hard maximum is a
    # full-video limit, so spare time in one segment offsets another segment's
    # overrun before any repair is authorized.
    explicit_tolerance = next(
        (
            _as_float(entry["item"].get("_video_duration_tolerance_sec"))
            for entry in prepared
            if _as_float(entry["item"].get("_video_duration_tolerance_sec")) is not None
        ),
        None,
    )
    global_tolerance = (
        max(0.0, explicit_tolerance)
        if explicit_tolerance is not None
        else (
            max(0.0, pre_tts_max - original_total)
            if explicit_hard_max is not None
            else DEFAULT_GLOBAL_DURATION_TOLERANCE_SEC
        )
    )
    global_overrun = max(0.0, estimated_total - original_total)
    duration_reduction_required = max(0.0, estimated_total - pre_tts_max)
    global_overrun_exceeded = duration_reduction_required > 0.001

    repair_reductions = [0.0 for _ in prepared]
    if global_overrun_exceeded and positive_segment_overrun > 0.001:
        for index, overrun in enumerate(overruns):
            if overrun > 0.001:
                repair_reductions[index] = min(
                    overrun,
                    duration_reduction_required
                    * overrun
                    / positive_segment_overrun,
                )

    scheduled_items: list[dict[str, Any]] = []
    scheduled_total = 0.0
    for entry, overrun, required_reduction in zip(
        prepared, overruns, repair_reductions
    ):
        # Preserve authored visual targets.  Assign only the real full-video
        # reduction requirement, proportionally across overrun segments.
        target = entry["original_target_sec"]
        section = str(entry["item"].get("section") or "")
        edge_max = _as_float(entry["item"].get("duration_max_sec"), 0.0) or 0.0
        edge_reduction = (
            max(0.0, entry["estimated_spoken_sec"] - edge_max)
            if section in {"intro", "outro"} and edge_max > 0
            else 0.0
        )
        required_reduction = max(required_reduction, edge_reduction)
        requires_global_repair = required_reduction > 0.001
        accepted_max_estimated = max(
            0.1,
            entry["estimated_spoken_sec"] - required_reduction,
        )
        item = copy.deepcopy(entry["item"])
        item["_global_overrun_sec"] = round(global_overrun, 3)
        item["_global_tolerance_sec"] = round(global_tolerance, 3)
        item["_segment_overrun_sec"] = round(overrun, 3)
        item["_duration_reduction_required_sec"] = round(required_reduction, 3)
        item["_accepted_max_estimated_sec"] = round(accepted_max_estimated, 3)
        item["_pre_repair_estimated_sec"] = round(entry["estimated_spoken_sec"], 3)
        item["_duration_repair_authorized"] = requires_global_repair
        if requires_global_repair:
            item["_force_duration_repair"] = True
        scheduled_total += target
        scheduled_items.append({
            "item": item,
            "segment_narration": entry["segment_narration"],
            "segment_performance": entry["segment_performance"],
            "display_text": entry["display_text"],
            "spoken_text": entry["spoken_text"],
            "estimated_spoken_sec": round(entry["estimated_spoken_sec"], 3),
            "original_target_sec": round(entry["original_target_sec"], 3),
            "segment_overrun_sec": round(overrun, 3),
            "global_overrun_sec": round(global_overrun, 3),
            "global_tolerance_sec": round(global_tolerance, 3),
            "duration_reduction_required_sec": round(required_reduction, 3),
            "accepted_max_estimated_sec": round(accepted_max_estimated, 3),
            "needs_narration_repair": requires_global_repair,
            "content_fit_error": (
                "TOTAL_SPOKEN_DURATION_EXCEEDS_VIDEO_BUDGET"
                if requires_global_repair else ""
            ),
        })
    return {
        "schema_version": "segment-narration-schedule-v1",
        "schedule_valid": True,
        "schedule_error": "",
        "content_fit_valid": True,
        "content_fit_error": "",
        "scheduled_items": scheduled_items,
        "scheduled_total_sec": round(scheduled_total, 3),
        "estimated_spoken_total_sec": round(
            sum(entry["estimated_spoken_sec"] for entry in prepared), 3
        ),
        "global_overrun_sec": round(global_overrun, 3),
        "global_tolerance_sec": round(global_tolerance, 3),
        "duration_reduction_required_sec": round(duration_reduction_required, 3),
        "duration_repair_required": any(
            bool(scheduled["item"].get("_duration_repair_authorized"))
            for scheduled in scheduled_items
        ),
        "pre_tts_max_sec": round(pre_tts_max, 3),
        "video_hard_max_sec": round(hard_max, 3),
    }


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
        display_performance = validate_performance_plan(text, performance)
    except ProfileError as exc:
        errors.append(str(exc))
        display_performance = {}

    preset_speed = _as_float(item.get("draft_speed"))
    if preset_speed is not None:
        preset_mismatch = (
            display_performance.get("delivery") != item.get("draft_delivery")
            or display_performance.get("emotion") != item.get("draft_emotion")
            or not math.isclose(
                _as_float(display_performance.get("speed"), 0.0) or 0.0,
                preset_speed,
                rel_tol=0.0,
                abs_tol=0.0005,
            )
            or (_as_float(display_performance.get("pause_after_ms"), 0.0) or 0.0) != 0
        )
        sentence_plans = display_performance.get("sentences")
        if isinstance(sentence_plans, list):
            preset_pause = int(item.get("draft_sentence_pause_ms") or 0)
            for index, sentence_plan in enumerate(sentence_plans):
                if not isinstance(sentence_plan, dict):
                    preset_mismatch = True
                    break
                plan = sentence_plan.get("performance_plan")
                if not isinstance(plan, dict):
                    plan = sentence_plan
                expected_pause = 0 if index == len(sentence_plans) - 1 else preset_pause
                if (
                    plan.get("delivery") != item.get("draft_delivery")
                    or plan.get("emotion") != item.get("draft_emotion")
                    or not math.isclose(
                        _as_float(plan.get("speed"), 0.0) or 0.0,
                        preset_speed,
                        rel_tol=0.0,
                        abs_tol=0.0005,
                    )
                    or (_as_float(plan.get("pause_after_ms"), -1.0) or 0.0)
                    != expected_pause
                ):
                    preset_mismatch = True
                    break
        if preset_mismatch:
            errors.append("PRESET_PERFORMANCE_MISMATCH")

    # Prices stay numeric in the display/subtitle contract, while the paid
    # TTS request uses an explicit English pronunciation.  Estimating the
    # spoken form prevents ``4434.88`` being treated as a few characters.
    spoken_text = _spoken_tts_text(text)
    try:
        spoken_performance = validate_performance_plan(
            spoken_text,
            _spoken_performance(display_performance, spoken_text),
        )
    except ProfileError as exc:
        errors.append(str(exc))
        spoken_performance = {}

    speed_min = _as_float(profile.get("safe_speed_min"), 0.90)
    speed_max = _as_float(profile.get("safe_speed_max"), 1.05)
    actual_speed = _as_float(spoken_performance.get("speed"))
    if (
        actual_speed is None
        or speed_min is None
        or speed_max is None
        or not speed_min <= actual_speed <= speed_max
    ):
        errors.append("SPEED_OUT_OF_PROFILE_RANGE")

    speed = _as_float(spoken_performance.get("speed"))
    cps = _as_float(profile.get("base_chars_per_second"))
    if speed is None or cps is None or speed <= 0 or cps <= 0:
        estimated = 0.0
        word_estimated = 0.0
        errors.append("DURATION_MODEL_INVALID")
    else:
        estimated, word_estimated = _estimated_spoken_seconds(
            spoken_text,
            cps,
            speed,
            profile.get("pause_model") or {},
            int(spoken_performance.get("pause_after_ms", 0)),
            _profile_words_per_second(profile),
        )
        factor = _duration_factor(profile)
        estimated *= factor
        word_estimated *= factor
        try:
            sentence_estimate = _sentence_level_estimate(
                narration,
                performance,
                profile,
            )
        except ValueError as exc:
            errors.append(str(exc))
        else:
            if sentence_estimate is not None:
                estimated, word_estimated = sentence_estimate
    errors = list(dict.fromkeys(errors))
    return {
        "errors": errors,
        "narration_violation": narration_violation,
        "estimated_total_sec": round(estimated, 3),
        "display_text": text,
        "spoken_text": spoken_text,
        "budget": budget,
        "validated_narration": narration if not errors else {},
        "validated_performance": display_performance if not errors else {},
        "tts_narration": (
            {**narration, "text": spoken_text} if not errors else {}
        ),
        "tts_performance": spoken_performance if not errors else {},
    }


def _tts_request(
    master_request_id: str,
    narrator_profile_id: str,
    narration: dict[str, Any],
    performance: dict[str, Any],
    revision: int,
) -> dict[str, Any]:
    segment_id = _safe_id(narration.get("segment_id"))
    digest = hashlib.sha256(
        f"{master_request_id}|{segment_id}|{revision}".encode("utf-8")
    ).hexdigest()[:12]
    request_id = f"{_safe_id(master_request_id, 'tool08')}-{segment_id}-r{revision}-{digest}"[:100]
    text = str(narration.get("text") or "")
    tts_narration = copy.deepcopy(narration)
    tts_performance = copy.deepcopy(performance)
    tts_narration["text"] = text
    tts_performance["text"] = text
    raw_narration_sentences = tts_narration.get("sentences")
    raw_performance_sentences = tts_performance.get("sentences")
    # Sentence performance is carried by segment.sentences[].performance_plan;
    # the strict v7.2 top-level performance schema must not contain it.
    tts_performance.pop("sentences", None)

    # Preserve the sentence-level plan at the paid-TTS boundary.  The
    # MiniMax worker uses one request per sentence; dropping this field here
    # silently falls back to one request per segment.
    if isinstance(raw_narration_sentences, list) and raw_narration_sentences:
        sentence_entries: list[dict[str, Any]] = []
        for index, sentence in enumerate(raw_narration_sentences, start=1):
            if not isinstance(sentence, dict):
                continue
            sentence_text = str(sentence.get("text") or "").strip()
            if not sentence_text:
                continue
            sentence_text = _spoken_tts_text(sentence_text)
            perf_item: dict[str, Any] = {}
            if isinstance(raw_performance_sentences, list):
                for candidate in raw_performance_sentences:
                    if not isinstance(candidate, dict):
                        continue
                    if int(candidate.get("index") or index) == int(
                        sentence.get("index") or index
                    ):
                        perf_item = copy.deepcopy(
                            candidate.get("performance_plan")
                            if isinstance(candidate.get("performance_plan"), dict)
                            else candidate
                        )
                        break
            perf_item["text"] = sentence_text
            sentence_entries.append(
                {
                    "index": int(sentence.get("index") or index),
                    "text": sentence_text,
                    "performance_plan": perf_item,
                }
            )
        if sentence_entries:
            tts_narration["sentences"] = [
                {"index": entry["index"], "text": entry["text"]}
                for entry in sentence_entries
            ]
    segment_payload: dict[str, Any] = {
        "segment_id": segment_id,
        "text": text,
        "performance_plan": tts_performance,
    }
    if isinstance(tts_narration.get("sentences"), list) and tts_narration["sentences"]:
        segment_payload["sentences"] = [
            {
                "index": entry["index"],
                "text": entry["text"],
                "performance_plan": entry["performance_plan"],
            }
            for entry in sentence_entries
        ]
    return {
        "request_id": request_id,
        "narrator_profile_id": str(narrator_profile_id or "").strip(),
        "text": text,
        "narration_json": {
            "schema_version": "narration-tts-v2",
            "segments": [segment_payload],
        },
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
    repair_baseline_from_state: float | None = None
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
            repair_baseline_from_state = _as_float(state.get("pre_repair_estimated_sec"))
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
    band_enabled = all(
        _as_float(item.get(key)) is not None
        for key in (
            "accepted_min_estimated_sec",
            "accepted_max_estimated_sec",
            "draft_min_spoken_words",
            "draft_target_spoken_words",
            "draft_max_spoken_words",
        )
    )
    band_min = _as_float(item.get("accepted_min_estimated_sec"), 0.0) or 0.0
    band_max = _as_float(item.get("accepted_max_estimated_sec"), 0.0) or 0.0
    candidate_under_band = (
        band_enabled and result["estimated_total_sec"] < band_min - 0.001
    )
    candidate_over_band = (
        band_enabled and result["estimated_total_sec"] > band_max + 0.001
    )
    if candidate_under_band or candidate_over_band:
        errors.append(
            "PRE_TTS_DURATION_UNDER_RANGE"
            if candidate_under_band else "PRE_TTS_DURATION_OUT_OF_RANGE"
        )
        result["narration_violation"] = True
        item["_duration_repair_authorized"] = True
        item["_force_duration_repair"] = True
    duration_repair_authorized = bool(item.get("_duration_repair_authorized"))
    duration_repair_forced = bool(item.get("_force_duration_repair"))
    target_duration = _as_float(item.get("duration_target_sec"), 0.0) or 0.0
    explicit_accepted_max = _as_float(item.get("_accepted_max_estimated_sec"))
    accepted_max_estimated = (
        explicit_accepted_max
        if explicit_accepted_max is not None else target_duration
    )
    candidate_over_target = (
        result["estimated_total_sec"] > accepted_max_estimated + 0.001
    )
    if duration_repair_forced and duration_repair_authorized and candidate_over_target:
        if repair_candidate is None:
            # The initial candidate starts the one-shot repair.  After the
            # repair, the global tolerance owns acceptance; a segment may
            # remain above its authored target when its spoken number tokens
            # make the target unreachable.
            errors.append("PRE_TTS_DURATION_OUT_OF_RANGE")
            result["narration_violation"] = True
        else:
            baseline = repair_baseline_from_state
            if baseline is None:
                baseline = _as_float(item.get("_pre_repair_estimated_sec"))
            if baseline is None:
                baseline = target_duration + (
                    _as_float(item.get("_segment_overrun_sec"), 0.0) or 0.0
                )
            if explicit_accepted_max is not None:
                if result["estimated_total_sec"] > explicit_accepted_max + 0.001:
                    errors.append("PRE_TTS_DURATION_REPAIR_TARGET_NOT_MET")
                    result["narration_violation"] = True
            elif result["estimated_total_sec"] >= baseline - 0.001:
                errors.append("PRE_TTS_DURATION_REPAIR_NO_IMPROVEMENT")
                result["narration_violation"] = True
    errors = list(dict.fromkeys(errors))
    base_result = {
        "performance_valid": not errors,
        "performance_error": ";".join(errors),
        "pre_tts_duration_valid": not errors,
        "estimated_total_sec": result["estimated_total_sec"],
        "display_text": result["display_text"],
        "spoken_text": result["spoken_text"],
        "validated_narration": result["validated_narration"],
        "validated_performance": result["validated_performance"],
        "tts_narration": result["tts_narration"],
        "tts_performance": result["tts_performance"],
    }
    if not errors:
        try:
            tts_request = _tts_request(
                master_request_id,
                narrator_profile_id,
                result["tts_narration"],
                result["tts_performance"],
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

    narration_repair_required = result["narration_violation"]
    kind = "narration" if narration_repair_required else "performance"
    # Dify keeps exactly one repair LLM node.  A second failed candidate is a
    # deterministic failure, rather than a second front-end repair loop.
    if repairs >= 1:
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
    if narration_repair_required:
        # Keep the original provider-facing estimate inside the state that
        # Dify copies back from the repair LLM.  This survives iteration
        # serialization even when private item metadata is not forwarded.
        next_state["pre_repair_estimated_sec"] = result["estimated_total_sec"]
    repair_budget = _duration_repair_budget(
        result["budget"],
        result["spoken_text"],
        result["estimated_total_sec"],
        voice_duration_profile,
    ) if narration_repair_required else copy.deepcopy(result["budget"])
    repair_budget.update({
        "global_overrun_sec": round(
            _as_float(item.get("_global_overrun_sec"), 0.0) or 0.0, 3
        ),
        "global_tolerance_sec": round(
            _as_float(item.get("_global_tolerance_sec"), 0.0) or 0.0, 3
        ),
        "segment_overrun_sec": round(
            _as_float(item.get("_segment_overrun_sec"), 0.0) or 0.0, 3
        ),
        "duration_reduction_required_sec": round(
            _as_float(item.get("_duration_reduction_required_sec"), 0.0) or 0.0,
            3,
        ),
        "accepted_max_estimated_sec": round(
            _as_float(item.get("_accepted_max_estimated_sec"), 0.0) or 0.0,
            3,
        ),
        "duration_repair_authorized": bool(
            item.get("_duration_repair_authorized", False)
        ),
        "accepted_min_estimated_sec": round(
            _as_float(item.get("accepted_min_estimated_sec"), 0.0) or 0.0,
            3,
        ),
        "accepted_max_estimated_sec": round(
            _as_float(item.get("accepted_max_estimated_sec"), 0.0) or 0.0,
            3,
        ),
        "min_spoken_word_budget": int(
            _as_float(item.get("draft_min_spoken_words"), 0.0) or 0
        ),
        "target_spoken_word_budget": int(
            _as_float(item.get("draft_target_spoken_words"), 0.0) or 0
        ),
        "max_spoken_word_budget": int(
            _as_float(item.get("draft_max_spoken_words"), 0.0) or 0
        ),
        "repair_direction": (
            "expand"
            if candidate_under_band
            else ("compress" if candidate_over_band else "performance")
        ),
    })
    repair_prompt = {
        "repair_kind": kind,
        "validator_errors": errors,
        "allowed_changes": (
            ["segment_narration.text", "segment_performance"]
            if kind == "narration" else ["segment_performance"]
        ),
        "item": _compact_repair_item(item),
        "segment_duration_budget": repair_budget,
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
    if not (
        budget["duration_min_sec"] - 0.001
        <= duration
        <= budget["duration_max_sec"] + 0.001
    ):
        return _confirm_fail("ACTUAL_AUDIO_DURATION_OUT_OF_RANGE")
    result = _as_object_json(step_result_json, "STEP_RESULT")
    narration = result.get("validated_narration") if isinstance(result.get("validated_narration"), dict) else {}
    performance = result.get("validated_performance") if isinstance(result.get("validated_performance"), dict) else {}
    spoken_text = str(result.get("spoken_text") or narration.get("text") or "")
    estimated_duration = _as_float(result.get("estimated_total_sec"))
    try:
        scenes, time_scale, timeline_adjusted = _rescale_visual_timeline(item, duration)
    except ValueError as exc:
        return _confirm_fail(str(exc))
    resolved_visual_facts = item.get("resolved_visual_facts")
    if not isinstance(resolved_visual_facts, list) or not resolved_visual_facts:
        return _confirm_fail("VISUAL_FACTS_NOT_RESOLVED")
    media = {
        "segment_id": budget["segment_id"],
        "order": item.get("order"),
        "section": item.get("section"),
        "planning_role": item.get("planning_role"),
        "scenario_id": item.get("scenario_id"),
        "fact_anchor_ids": copy.deepcopy(item.get("fact_anchor_ids") or []),
        "content_goal": item.get("content_goal"),
        "importance": item.get("importance"),
        "speech_style": item.get("speech_style"),
        "duration_target_sec": budget["target_duration_sec"],
        "duration_min_sec": budget["duration_min_sec"],
        "duration_max_sec": budget["duration_max_sec"],
        "resolved_visual_facts": copy.deepcopy(resolved_visual_facts),
        "visual": copy.deepcopy(item.get("visual")),
        "scenes": scenes,
        "transition_out": copy.deepcopy(
            item.get("transition_out")
            if isinstance(item.get("transition_out"), dict)
            else {"type": "hard_cut", "duration_ms": 0}
        ),
        "audio": {"url": audio_url, "duration_sec": duration},
        "narration": {
            "schema_version": narration.get("schema_version"),
            "segment_id": narration.get("segment_id"),
            "text": narration.get("text"),
            "display_text": narration.get("text"),
            "spoken_text": spoken_text,
        },
        "performance_plan": performance,
        "duration_validation": {
            "target_duration_sec": budget["target_duration_sec"],
            "duration_min_sec": budget["duration_min_sec"],
            "duration_max_sec": budget["duration_max_sec"],
            "actual_duration_sec": duration,
            "estimated_duration_sec": estimated_duration,
            "estimation_error_sec": (
                round(duration - estimated_duration, 3)
                if estimated_duration is not None else None
            ),
            "planned_duration_sec": budget["target_duration_sec"],
            "time_scale": round(time_scale, 6),
            "timeline_adjusted": timeline_adjusted,
            "mode": "actual_audio_authoritative",
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


def resolve_render_step(
    item: dict[str, Any],
    selected_input: dict[str, Any],
    voice_duration_profile: dict[str, Any],
    narrator_profile_id: str,
    master_request_id: str,
) -> dict[str, Any]:
    """Resolve the Dify branch value into one validated pass step.

    The variable aggregator before the backend render call selects either the
    already-passed step response or the one allowed repair LLM candidate.
    """
    selected = selected_input if isinstance(selected_input, dict) else {}
    if selected.get("schema_version") == "segment-narration-step-result-v1":
        if selected.get("action") != "pass" or selected.get("done") is not True:
            return _step_failure("INITIAL_STEP_NOT_RENDERABLE")
        return copy.deepcopy(selected)
    return process_step(
        item=item,
        segment_narration=None,
        segment_performance=None,
        repair_candidate=selected,
        voice_duration_profile=voice_duration_profile,
        narrator_profile_id=narrator_profile_id,
        master_request_id=master_request_id,
    )


def segment_render_failure(item: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "schema_version": "segment-render-result-v1",
        "segment_valid": False,
        "segment_error": str(error or "SEGMENT_RENDER_FAILED"),
        "segment_id": str((item or {}).get("segment_id") or ""),
        "segment_media_input": {},
    }


def segment_render_success(item: dict[str, Any], confirm: dict[str, Any]) -> dict[str, Any]:
    try:
        if confirm.get("action") != "pass" or confirm.get("done") is not True:
            raise ValueError(str(confirm.get("confirm_error") or "TTS_CONFIRM_FAILED"))
        result = _as_object_json(str(confirm.get("result_json") or ""), "CONFIRM_RESULT")
        media = result.get("segment_media_input")
        if not isinstance(media, dict) or not media:
            raise ValueError("SEGMENT_MEDIA_INPUT_REQUIRED")
        return {
            "schema_version": "segment-render-result-v1",
            "segment_valid": True,
            "segment_error": "",
            "segment_id": str((item or {}).get("segment_id") or ""),
            "segment_media_input": media,
        }
    except ValueError as exc:
        return segment_render_failure(item, str(exc))


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
    planned_by_id = {
        str(item.get("segment_id") or ""): item
        for item in planned
        if isinstance(item, dict)
    }
    bad_ids: list[str] = []
    duration_validations: list[dict[str, Any]] = []
    for expected_id in expected_ids:
        media = media_by_id.get(expected_id) or {}
        audio = media.get("audio") if isinstance(media.get("audio"), dict) else {}
        validation = media.get("duration_validation") if isinstance(media.get("duration_validation"), dict) else {}
        planned_budget, planned_errors = _duration_budget(
            planned_by_id.get(expected_id) or {}
        )
        actual_duration = _as_float(audio.get("duration_sec"), 0.0) or 0.0
        actual_duration_valid = (
            not planned_errors
            and planned_budget["duration_min_sec"] - 0.001
            <= actual_duration
            <= planned_budget["duration_max_sec"] + 0.001
        )
        visual_valid = (
            isinstance(media.get("order"), int)
            and media.get("order") > 0
            and isinstance(media.get("visual"), dict)
            and isinstance(media.get("scenes"), list)
            and bool(media.get("scenes"))
            and isinstance(media.get("resolved_visual_facts"), list)
            and bool(media.get("resolved_visual_facts"))
        )
        valid = (
            bool(audio.get("url"))
            and actual_duration > 0
            and actual_duration_valid
            and validation.get("valid") is True
            and visual_valid
        )
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


def finalize_tool08(
    segment_results: list[Any],
    segment_plan_v1_json: str,
    voice_duration_profile: dict[str, Any],
    master_request_id: str,
) -> dict[str, Any]:
    """Parse Iteration HTTP bodies and preserve TOOL-08's top-level contract."""
    master_id = str(master_request_id or "").strip()
    if not master_id:
        base = _complete_failure([], voice_duration_profile, "MASTER_REQUEST_ID_REQUIRED")
        return {"master_request_id": "", **base}

    parsed_results: list[dict[str, Any]] = []
    bad_ids: list[str] = []
    errors: list[str] = []
    for index, raw in enumerate(segment_results or []):
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            value = None
        if not isinstance(value, dict):
            bad_ids.append(f"index_{index}")
            errors.append(f"SEGMENT_RESULT_{index}_INVALID")
            continue
        parsed_results.append(value)
        if value.get("segment_valid") is not True:
            bad_ids.append(str(value.get("segment_id") or f"index_{index}"))
            errors.append(str(value.get("segment_error") or "SEGMENT_RENDER_FAILED"))

    if bad_ids or len(parsed_results) != len(segment_results or []):
        base = _complete_failure(
            list(dict.fromkeys(bad_ids)),
            voice_duration_profile,
            ";".join(dict.fromkeys(errors)) or "SEGMENT_RENDER_FAILED",
        )
        return {"master_request_id": master_id, **base}

    media_inputs = [value.get("segment_media_input") for value in parsed_results]
    base = complete_tool08(media_inputs, segment_plan_v1_json, voice_duration_profile)
    return {"master_request_id": master_id, **base}


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
