import json
import sys
import types
import unittest

# These unit tests exercise deterministic TOOL-08 logic only.  The project
# runtime supplies httpx; this lightweight stub keeps the test runnable in a
# minimal local Python installation without changing production imports.
sys.modules.setdefault("httpx", types.ModuleType("httpx"))

from app.segment_narration_validation import (
    _estimated_spoken_seconds,
    complete_tool08,
    confirm_tts_result,
    finalize_tool08,
    initialize_tool08,
    process_step,
    rebalance_tool08,
    resolve_render_step,
    segment_render_success,
)


def _item() -> dict:
    return {
        "segment_id": "seg_01",
        "order": 1,
        "section": "analysis",
        "planning_role": "technical_context",
        "scenario_id": None,
        "fact_anchor_ids": ["level.current"],
        "content_goal": "Explain the current level.",
        "importance": "high",
        "speech_style": "calm_analysis",
        "duration_target_sec": 4,
        "duration_min_sec": 2,
        "duration_max_sec": 8,
        "visual": {
            "visual_mode": "technical_analysis",
            "source_timeframe": "1h",
            "camera_motion": "pan_right",
            "highlight_levels": ["level.current"],
            "show_volume": True,
            "show_macro_marker": False,
        },
        "scenes": [
            {
                "scene_id": "seg_01_sc_01",
                "template_id": "chart_push",
                "start_sec": 0.0,
                "duration_sec": 4.0,
                "camera_motion": "pan_right",
                "overlay_events": [
                    {
                        "event_id": "seg_01_level",
                        "event_type": "price_level",
                        "start_sec": 0.5,
                        "duration_sec": 2.0,
                        "fact_anchor_ids": ["level.current"],
                    }
                ],
                "transition_out": {"type": "hard_cut", "duration_ms": 0},
            }
        ],
        "transition_out": {"type": "fade", "duration_ms": 250},
        "resolved_visual_facts": [
            {
                "anchor_id": "level.current",
                "fact_type": "price_point",
                "price": 2400.0,
                "display_text": "2400.00",
            }
        ],
    }


def _narration(text: str = "Gold holds near 2400 while confirmation remains important.") -> dict:
    return {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_01",
        "planning_role": "technical_context",
        "fact_anchor_ids": ["level.current"],
        "text": text,
    }


def _performance(text: str | None = None) -> dict:
    text = text or _narration()["text"]
    return {
        "schema_version": "tts-performance-v1",
        "segment_id": "seg_01",
        "text": text,
        "delivery": "calm_analysis",
        "emotion": "calm",
        "speed": 1.0,
        "pitch": 0,
        "energy": 0.6,
        "pause_after_ms": 100,
        "cues": [],
    }


def _profile() -> dict:
    return {
        "schema_version": "voice-duration-profile-v1",
        "narrator_profile_id": "mm_finance_male_02",
        "base_chars_per_second": 14.0,
        "safe_speed_min": 0.9,
        "safe_speed_max": 1.05,
        "pause_model": {"comma_ms": 160, "semicolon_ms": 220, "period_ms": 320, "question_ms": 360},
    }


def _init_contracts() -> dict:
    return {
        "market_input_v1_json": json.dumps({"schema_version": "market-input-contract-v1"}),
        "levels_v1_json": json.dumps({"schema_version": "levels-contract-v1"}),
        "technical_v1_json": json.dumps({"schema_version": "technical-contract-v1"}),
        "macro_context_v1_json": json.dumps({"schema_version": "macro-context-contract-v1"}),
        "market_analysis_v1_json": json.dumps({"schema_version": "market-analysis-contract-v1"}),
        "forecast_v1_json": json.dumps({"schema_version": "forecast-contract-v1"}),
        "segment_plan_v1_json": json.dumps({
            "schema_version": "segment-plan-contract-v1",
            "visual_fact_catalog": {
                "schema_version": "visual-fact-catalog-v1",
                "facts": [
                    {
                        "anchor_id": "level.current",
                        "fact_type": "price_point",
                        "price": 2400.0,
                        "display_text": "2400.00",
                    }
                ],
            },
            "segment_plan_valid": True,
            "segment_plan": {"segments": [_item()]},
        }),
        "narrator_profile_id": "mm_finance_male_02",
        "master_request_id": "master_01",
    }


def test_init_returns_direct_iteration_array_and_profile():
    result = initialize_tool08(**_init_contracts())

    assert result["init_valid"] is True
    assert result["init_error"] == ""
    assert result["voice_duration_profile"]["narrator_profile_id"] == "mm_finance_male_02"
    assert result["voice_duration_profile"]["base_chars_per_second"] == 20.0
    assert result["voice_duration_profile"]["base_words_per_second"] == 2.6
    assert result["segments"][0]["segment_id"] == "seg_01"
    assert isinstance(result["segments"][0]["narration_prompt_json"], str)
    assert result["master_request_id"].startswith("master_01")


def test_init_adds_conservative_draft_spoken_word_budget():
    contracts = _init_contracts()
    contracts["forecast_v1_json"] = json.dumps({
        "schema_version": "forecast-contract-v1",
        "active_levels": {
            "authoritative_price_map": {
                "OPEN_UPSIDE": 4416.0,
                "OPEN_DOWNSIDE": 4391.42,
            }
        },
    })
    result = initialize_tool08(**contracts)

    assert result["init_valid"] is True
    item = result["segments"][0]
    assert item["draft_duration_cap_sec"] == 4.0
    assert item["draft_delivery"] == "calm_analysis"
    assert item["draft_emotion"] == "neutral"
    assert item["draft_speed"] == 0.98
    assert item["draft_sentence_count"] == 2
    assert item["draft_sentence_pause_ms"] == 120
    assert item["duration_calibration_factor"] == 0.83
    assert item["draft_min_spoken_words"] < item["draft_target_spoken_words"]
    assert item["draft_target_spoken_words"] < item["draft_max_spoken_words"]
    assert item["draft_punctuation_sec"] == 0.64
    assert item["draft_sentence_pause_sec"] == 0.12
    assert item["draft_safety_margin_sec"] == 0.3
    assert item["numeric_spoken_costs"]["2400.00"] > 1
    assert item["numeric_spoken_costs"]["4416.0"] > 1
    assert item["numeric_spoken_costs"]["4391.42"] > 1

    prompt = json.loads(item["narration_prompt_json"])
    assert prompt["item"]["draft_min_spoken_words"] == item["draft_min_spoken_words"]
    assert prompt["item"]["draft_target_spoken_words"] == item["draft_target_spoken_words"]
    assert prompt["item"]["draft_max_spoken_words"] == item["draft_max_spoken_words"]
    assert prompt["item"]["numeric_spoken_costs"]["2400.00"] > 1


def test_step_soft_triggers_repair_for_candidate_outside_duration_band():
    contracts = _init_contracts()
    result = initialize_tool08(**contracts)
    item = result["segments"][0]
    narration = _narration("Gold holds.")
    performance = _performance(narration["text"])
    performance.update(delivery=item["draft_delivery"], emotion=item["draft_emotion"], speed=item["draft_speed"], pause_after_ms=0)

    step = process_step(
        item,
        narration,
        performance,
        result["voice_duration_profile"],
        "mm_finance_male_02",
        "master_01",
    )

    assert step["action"] == "repair_narration"
    assert step["done"] is False
    repair = json.loads(step["repair_prompt_json"])
    assert "PRE_TTS_DURATION_UNDER_RANGE" not in repair["validator_errors"]
    assert repair["segment_duration_budget"]["repair_direction"] == "narration"


def test_step_rejects_performance_that_changes_pregeneration_preset():
    contracts = _init_contracts()
    result = initialize_tool08(**contracts)
    item = result["segments"][0]
    narration = _narration()
    performance = _performance(narration["text"])
    performance["delivery"] = "compact"
    performance["emotion"] = "calm"
    performance["speed"] = 1.05
    performance["pause_after_ms"] = 0

    step = process_step(
        item,
        narration,
        performance,
        result["voice_duration_profile"],
        "mm_finance_male_02",
        "master_01",
    )

    assert step["action"] == "repair_performance"
    repair = json.loads(step["repair_prompt_json"])
    assert "PRESET_PERFORMANCE_MISMATCH" in repair["validator_errors"]


def test_init_exposes_two_decimal_kline_values_to_narration_llm():
    contracts = _init_contracts()
    contracts["technical_v1_json"] = json.dumps({
        "schema_version": "technical-contract-v1",
        "technical_facts": {
            "last_close": 4434.876,
            "timeframes": {"1h": {"ema20": 4444.0351, "closed_count": 199}},
        },
    })
    contracts["forecast_v1_json"] = json.dumps({
        "schema_version": "forecast-contract-v1",
        "active_levels": {
            "authoritative_price_map": {"OPEN_UPSIDE": 4452.4382686525},
        },
    })

    result = initialize_tool08(**contracts)
    prompt = json.loads(result["segments"][0]["narration_prompt_json"])
    assert prompt["voice_duration_profile"]["base_words_per_second"] == 2.6

    assert prompt["technical"]["technical_facts"]["last_close"] == 4434.88
    assert prompt["technical"]["technical_facts"]["timeframes"]["1h"]["ema20"] == 4444.04
    assert prompt["forecast"]["active_levels"]["authoritative_price_map"]["OPEN_UPSIDE"] == 4452.44


def test_init_rejects_missing_master_request_id():
    contracts = _init_contracts()
    contracts["master_request_id"] = ""
    result = initialize_tool08(**contracts)

    assert result["init_valid"] is False
    assert result["init_error"] == "MASTER_REQUEST_ID_REQUIRED"
    assert result["master_request_id"] == ""


def test_init_rejects_missing_visual_fact_catalog():
    contracts = _init_contracts()
    plan = json.loads(contracts["segment_plan_v1_json"])
    plan.pop("visual_fact_catalog")
    contracts["segment_plan_v1_json"] = json.dumps(plan)

    result = initialize_tool08(**contracts)

    assert result["init_valid"] is False
    assert result["init_error"] == "VISUAL_FACT_CATALOG_REQUIRED"


def test_init_maps_visual_level_ids_to_catalog_level_anchors():
    contracts = _init_contracts()
    plan = json.loads(contracts["segment_plan_v1_json"])
    plan["visual_fact_catalog"]["facts"] = [{
        "anchor_id": "level:R1",
        "fact_type": "price_zone",
        "lower_price": 4448.0,
        "upper_price": 4452.0,
        "center_price": 4450.0,
        "display_text": "R1",
    }]
    plan["segment_plan"]["segments"][0]["fact_anchor_ids"] = ["level:R1"]
    plan["segment_plan"]["segments"][0]["visual"]["highlight_levels"] = ["R1"]
    for event in plan["segment_plan"]["segments"][0]["scenes"][0]["overlay_events"]:
        event["fact_anchor_ids"] = ["level:R1"]
    contracts["segment_plan_v1_json"] = json.dumps(plan)

    result = initialize_tool08(**contracts)

    assert result["init_valid"] is True
    assert result["segments"][0]["resolved_visual_facts"][0]["anchor_id"] == "level:R1"


def test_render_step_accepts_initial_pass_without_repair():
    initial = process_step(
        _item(), _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    resolved = resolve_render_step(
        _item(), initial, _profile(), "mm_finance_male_02", "master_01"
    )

    assert resolved == initial


def test_render_step_validates_the_single_repair_candidate():
    bad_narration = _narration("You should buy gold now.")
    initial = process_step(
        _item(), bad_narration, _performance(bad_narration["text"]),
        _profile(), "mm_finance_male_02", "master_01",
    )
    repair = json.loads(initial["repair_prompt_json"])
    repair_candidate = {
        "segment_narration": _narration(),
        "segment_performance": _performance(),
        "state_json": repair["state_json"],
    }
    resolved = resolve_render_step(
        _item(), repair_candidate, _profile(), "mm_finance_male_02", "master_01"
    )

    assert resolved["action"] == "pass"
    assert json.loads(resolved["next_state_json"])["repair_count"] == 1


def test_step_pass_builds_tts_request_without_a_per_segment_duration_target():
    result = process_step(
        _item(), _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    parsed = json.loads(result["result_json"])

    assert result["action"] == "pass"
    assert result["done"] is True
    assert set(parsed["tts_request"]) == {
        "request_id", "narrator_profile_id", "text", "narration_json",
    }
    assert parsed["tts_request"]["narration_json"]["segments"][0]["performance_plan"]["text"] == (
        "Gold holds near two thousand four hundred while confirmation remains important."
    )


def test_step_expands_four_digit_prices_for_tts_but_preserves_display_text():
    narration = _narration("Gold held near 4434.88 while 4452.44 remained unconfirmed.")
    performance = _performance(narration["text"])
    performance["cues"] = [{"text": "4434.88", "action": "emphasize"}]
    item = {**_item(), "duration_target_sec": 8, "duration_min_sec": 2, "duration_max_sec": 12}

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )
    parsed = json.loads(result["result_json"])
    request = parsed["tts_request"]

    assert result["action"] == "pass"
    assert parsed["display_text"] == narration["text"]
    assert parsed["spoken_text"] == (
        "Gold held near four thousand four hundred thirty-four point eight eight "
        "while four thousand four hundred fifty-two point four four remained unconfirmed."
    )
    assert request["narration_json"]["segments"][0]["text"] == parsed["spoken_text"]
    assert request["narration_json"]["segments"][0]["performance_plan"]["cues"][0]["text"] == (
        "four thousand four hundred thirty-four point eight eight"
    )


def test_step_preserves_requested_pause_for_estimation_and_tts_request():
    narration = _narration("Gold holds near 2400.")
    performance = _performance(narration["text"])
    performance["pause_after_ms"] = 40
    item = {**_item(), "duration_target_sec": 5, "duration_min_sec": 1, "duration_max_sec": 8}

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )
    parsed = json.loads(result["result_json"])

    assert result["action"] == "pass"
    assert parsed["tts_request"]["narration_json"]["segments"][0]["performance_plan"]["pause_after_ms"] == 40


def test_step_uses_spoken_forms_for_prices_percentages_times_timeframes_and_levels():
    narration = _narration(
        "Gold held near 4434.88, up +0.65%, at 2:30 PM on 15m below R1."
    )
    performance = _performance(narration["text"])
    performance["cues"] = [
        {"text": "+0.65%", "action": "emphasize"},
        {"text": "2:30 PM", "action": "emphasize"},
    ]
    item = {**_item(), "duration_target_sec": 12, "duration_min_sec": 8, "duration_max_sec": 16}

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )
    parsed = json.loads(result["result_json"])
    request = parsed["tts_request"]

    assert parsed["display_text"] == narration["text"]
    assert parsed["spoken_text"] == (
        "Gold held near four thousand four hundred thirty-four point eight eight, "
        "up plus zero point six five percent, at two thirty p m on fifteen minutes below R one."
    )
    assert request["narration_json"]["segments"][0]["performance_plan"]["cues"] == [
        {"text": "plus zero point six five percent", "action": "emphasize"},
        {"text": "two thirty p m", "action": "emphasize"},
    ]


def test_price_spoken_duration_does_not_trigger_repair_when_two_prices_exceed_visual_budget():
    item = {
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "text": "4434.88 faces 4452.44?",
    }
    performance = _performance(narration["text"])
    performance["segment_id"] = "seg_01_intro"

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )

    assert result["action"] == "pass"
    assert result["done"] is True


def test_rebalance_preserves_original_segment_budgets_instead_of_stretching_audio():
    short_item = {
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
    }
    long_item = {
        "segment_id": "seg_02_analysis",
        "planning_role": "technical_context",
        "fact_anchor_ids": ["level.context"],
        "duration_target_sec": 10,
        "duration_min_sec": 8.5,
        "duration_max_sec": 11.5,
    }
    short_narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "text": "Which side gains confirmation as price action develops?",
    }
    long_narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_02_analysis",
        "planning_role": "technical_context",
        "fact_anchor_ids": ["level.context"],
        "text": "Gold remains within a mixed structure while price action needs clearer confirmation before direction becomes established.",
    }
    short_performance = _performance(short_narration["text"])
    short_performance.update(segment_id="seg_01_intro", speed=1.05, pause_after_ms=0)
    long_performance = _performance(long_narration["text"])
    long_performance.update(segment_id="seg_02_analysis", pause_after_ms=0)

    result = rebalance_tool08([
        {"item": short_item, "segment_narration": short_narration, "segment_performance": short_performance},
        {"item": long_item, "segment_narration": long_narration, "segment_performance": long_performance},
    ], _profile())

    assert result["schedule_valid"] is True
    scheduled = result["scheduled_items"]
    assert round(sum(item["item"]["duration_target_sec"] for item in scheduled), 3) == 13.0
    assert scheduled[0]["item"]["duration_target_sec"] == 3.0
    assert scheduled[1]["item"]["duration_target_sec"] == 10.0
    assert scheduled[0]["original_target_sec"] == 3.0
    assert scheduled[1]["original_target_sec"] == 10.0


def test_rebalance_marks_unfit_global_budget_for_narration_repair():
    item = {
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "text": "Gold holds near 4434.88 while 4452.44 and 4417.32 remain the next important conditions.",
    }
    performance = _performance(narration["text"])
    performance.update(segment_id="seg_01_intro", pause_after_ms=0)

    result = rebalance_tool08([
        {"item": item, "segment_narration": narration, "segment_performance": performance},
    ], _profile())

    assert result["schedule_valid"] is True
    assert result["schedule_error"] == ""
    assert result["content_fit_valid"] is True
    assert result["content_fit_error"] == ""
    assert len(result["scheduled_items"]) == 1
    assert result["scheduled_items"][0]["needs_narration_repair"] is True
    assert result["scheduled_items"][0]["content_fit_error"] == (
        "TOTAL_SPOKEN_DURATION_EXCEEDS_VIDEO_BUDGET"
    )
    assert result["scheduled_items"][0]["item"]["duration_target_sec"] == 3.0
    assert result["scheduled_total_sec"] == 3.0

    scheduled = result["scheduled_items"][0]
    step = process_step(
        scheduled["item"],
        scheduled["segment_narration"],
        scheduled["segment_performance"],
        _profile(),
        "mm_finance_male_02",
        "master_01",
    )
    assert step["action"] == "repair_narration"
    assert "PRE_TTS_DURATION_OUT_OF_RANGE" in json.loads(
        step["repair_prompt_json"]
    )["validator_errors"]

    second_step = process_step(
        scheduled["item"],
        None,
        None,
        _profile(),
        "mm_finance_male_02",
        "master_01",
        repair_candidate={
            "state_json": '{"repair_count":1,"narration_revision":1}',
            "segment_narration": scheduled["segment_narration"],
            "segment_performance": scheduled["segment_performance"],
        },
    )
    assert second_step["action"] == "fail"
    assert second_step["step_error"].startswith("REPAIR_LIMIT_EXCEEDED;")
    assert "PRE_TTS_DURATION" in second_step["step_error"]


def test_spoken_word_duration_estimate_scales_with_speed():
    text = "Gold closed at four thousand four hundred thirty four point eight eight."

    slow_total, slow_words = _estimated_spoken_seconds(
        text, 20.0, 0.95, _profile()["pause_model"], 0, 2.6
    )
    fast_total, fast_words = _estimated_spoken_seconds(
        text, 20.0, 1.05, _profile()["pause_model"], 0, 2.6
    )

    assert slow_words > fast_words
    assert slow_total > fast_total


def test_rebalance_expands_budget_for_spoken_overrun():
    item = {
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 5,
        "duration_min_sec": 3.5,
        "duration_max_sec": 6.5,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "text": "Gold closed at 4434.88, while confirmation remains important.",
    }
    performance = _performance(narration["text"])
    performance.update(segment_id="seg_01_intro", speed=1.0, pause_after_ms=0)

    result = rebalance_tool08(
        [{
            "item": item,
            "segment_narration": narration,
            "segment_performance": performance,
        }],
        _profile(),
    )

    assert result["schedule_valid"] is True
    scheduled_item = result["scheduled_items"][0]
    assert scheduled_item["item"]["duration_target_sec"] == 5.0
    assert scheduled_item["needs_narration_repair"] is False
    assert result["scheduled_total_sec"] == 5.0


def test_rebalance_allows_individual_overrun_within_global_tolerance():
    item = {
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 5,
        "duration_min_sec": 3.5,
        "duration_max_sec": 6.5,
        "_video_hard_max_sec": 8.0,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "text": "Gold closed at 4434.88, while confirmation remains important.",
    }
    performance = _performance(narration["text"])
    performance.update(segment_id="seg_01_intro", speed=1.0, pause_after_ms=0)

    result = rebalance_tool08(
        [{"item": item, "segment_narration": narration, "segment_performance": performance}],
        _profile(),
    )

    scheduled = result["scheduled_items"][0]
    assert result["global_tolerance_sec"] == 3.0
    assert result["global_overrun_sec"] > 0
    assert result["global_overrun_sec"] <= result["global_tolerance_sec"]
    assert scheduled["needs_narration_repair"] is False
    assert "_force_duration_repair" not in scheduled["item"]
    assert scheduled["item"]["duration_target_sec"] == 5.0


def test_rebalance_repairs_only_overrun_segments_after_global_tolerance_is_exceeded():
    within_item = {
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
    }
    within_narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "text": "Gold holds.",
    }
    within_performance = _performance(within_narration["text"])
    within_performance.update(segment_id="seg_01_intro", speed=1.05, pause_after_ms=0)

    over_item = {
        "segment_id": "seg_02_analysis",
        "planning_role": "technical_context",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
    }
    over_narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_02_analysis",
        "planning_role": "technical_context",
        "fact_anchor_ids": ["level.current"],
        "text": "Gold closed at 4434.88 while 4452.44 and 4417.32 remain important conditions for confirmation and invalidation.",
    }
    over_performance = _performance(over_narration["text"])
    over_performance.update(segment_id="seg_02_analysis", speed=1.0, pause_after_ms=0)

    result = rebalance_tool08(
        [
            {"item": within_item, "segment_narration": within_narration, "segment_performance": within_performance},
            {"item": over_item, "segment_narration": over_narration, "segment_performance": over_performance},
        ],
        _profile(),
    )

    assert result["global_overrun_sec"] > result["global_tolerance_sec"]
    scheduled = result["scheduled_items"]
    assert scheduled[0]["needs_narration_repair"] is False
    assert "_force_duration_repair" not in scheduled[0]["item"]
    assert scheduled[1]["needs_narration_repair"] is True
    assert scheduled[1]["item"]["_force_duration_repair"] is True
    assert round(sum(
        item["duration_reduction_required_sec"] for item in scheduled
    ), 3) == result["duration_reduction_required_sec"]


def test_rebalance_offsets_middle_overrun_with_other_segments_spare_time():
    short_item = {
        "segment_id": "seg_02_short",
        "planning_role": "technical_context",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
        "_video_hard_max_sec": 10,
    }
    long_item = {
        **short_item,
        "segment_id": "seg_03_long",
        "planning_role": "primary_forecast",
        "fact_anchor_ids": ["forecast.framework"],
    }
    short_narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_02_short",
        "planning_role": "technical_context",
        "fact_anchor_ids": ["level.current"],
        "text": "Gold holds.",
    }
    long_narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_03_long",
        "planning_role": "primary_forecast",
        "fact_anchor_ids": ["forecast.framework"],
        "text": "Gold 4403.71 needs confirmation while the conditional path remains uncertain.",
    }
    short_performance = _performance(short_narration["text"])
    short_performance.update(segment_id="seg_02_short", pause_after_ms=0)
    long_performance = _performance(long_narration["text"])
    long_performance.update(segment_id="seg_03_long", pause_after_ms=0)

    result = rebalance_tool08([
        {"item": short_item, "segment_narration": short_narration, "segment_performance": short_performance},
        {"item": long_item, "segment_narration": long_narration, "segment_performance": long_performance},
    ], _profile())

    assert result["estimated_spoken_total_sec"] <= result["video_hard_max_sec"]
    assert result["duration_reduction_required_sec"] == 0
    assert result["duration_repair_required"] is False
    assert all(not item["needs_narration_repair"] for item in result["scheduled_items"])


def test_rebalance_assigns_one_explicit_repair_target_for_real_global_overrun():
    item = {
        "segment_id": "seg_03_primary",
        "section": "primary_path",
        "planning_role": "primary_forecast",
        "fact_anchor_ids": ["forecast.framework"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
        "_video_hard_max_sec": 4,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_03_primary",
        "planning_role": "primary_forecast",
        "fact_anchor_ids": ["forecast.framework"],
        "text": "Gold 4403.71 needs confirmation while the conditional path remains uncertain.",
    }
    performance = _performance(narration["text"])
    performance.update(segment_id="seg_03_primary", pause_after_ms=0)

    result = rebalance_tool08([
        {"item": item, "segment_narration": narration, "segment_performance": performance},
    ], _profile())

    scheduled = result["scheduled_items"][0]
    assert result["duration_reduction_required_sec"] > 0
    assert scheduled["needs_narration_repair"] is True
    assert scheduled["accepted_max_estimated_sec"] < scheduled["estimated_spoken_sec"]
    assert scheduled["item"]["_accepted_max_estimated_sec"] == scheduled["accepted_max_estimated_sec"]


def test_rebalance_uses_preferred_video_maximum_before_hard_maximum():
    item = {
        "segment_id": "seg_03_primary",
        "section": "primary_path",
        "planning_role": "primary_forecast",
        "fact_anchor_ids": ["forecast.framework"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
        "_video_hard_max_sec": 70,
        "_video_preferred_max_sec": 4,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_03_primary",
        "planning_role": "primary_forecast",
        "fact_anchor_ids": ["forecast.framework"],
        "text": "Gold 4403.71 needs confirmation while the conditional path remains uncertain.",
    }
    performance = _performance(narration["text"])
    performance.update(segment_id="seg_03_primary", pause_after_ms=0)

    result = rebalance_tool08([
        {"item": item, "segment_narration": narration, "segment_performance": performance},
    ], _profile())

    assert result["estimated_spoken_total_sec"] < 70
    assert result["pre_tts_max_sec"] == 4.0
    assert result["duration_repair_required"] is True


def test_rebalance_always_repairs_an_overlong_edge_segment():
    item = {
        "segment_id": "seg_04_outro",
        "section": "outro",
        "planning_role": "closing_question",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 5.5,
        "duration_min_sec": 4.0,
        "duration_max_sec": 7.0,
        "_video_hard_max_sec": 70,
        "_video_preferred_max_sec": 63,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_04_outro",
        "planning_role": "closing_question",
        "fact_anchor_ids": ["level.current"],
        "text": "With price near 4403.71, which side confirms first beyond the current boundaries?",
    }
    performance = _performance(narration["text"])
    performance.update(segment_id="seg_04_outro", pause_after_ms=0)

    result = rebalance_tool08([
        {"item": item, "segment_narration": narration, "segment_performance": performance},
    ], _profile())

    scheduled = result["scheduled_items"][0]
    assert result["duration_reduction_required_sec"] == 0
    assert scheduled["needs_narration_repair"] is True
    assert scheduled["accepted_max_estimated_sec"] == 7.0


def test_step_requests_narration_repair_before_paid_tts():
    narration = _narration("You should buy gold now.")
    result = process_step(
        _item(), narration, _performance(narration["text"]), _profile(), "mm_finance_male_02", "master_01"
    )

    assert result["action"] == "repair_narration"
    assert result["done"] is False
    repair = json.loads(result["repair_prompt_json"])
    assert repair["repair_kind"] == "narration"
    assert "PERSONALIZED_TRADE_DIRECTIVE" in repair["validator_errors"]


def test_duration_repair_prompt_contains_provider_spoken_budget():
    item = {
        **_item(),
        "duration_target_sec": 4.0,
        "duration_min_sec": 2.5,
        "duration_max_sec": 4.5,
        "_global_overrun_sec": 3.2,
        "_global_tolerance_sec": 3.0,
        "_segment_overrun_sec": 0.649,
        "_duration_repair_authorized": True,
        "_force_duration_repair": True,
    }
    narration = _narration("Gold 4403.71 mixed.")
    performance = _performance(narration["text"])
    performance["speed"] = 1.05
    performance["pause_after_ms"] = 0

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )

    assert result["action"] == "repair_narration"
    repair = json.loads(result["repair_prompt_json"])
    budget = repair["segment_duration_budget"]
    assert budget["spoken_text"] == (
        "Gold four thousand four hundred three point seven one mixed."
    )
    assert budget["estimated_spoken_duration_sec"] == 4.649
    assert budget["safe_duration_max_sec"] == 4.2
    assert budget["duration_overrun_sec"] == 0.649
    assert budget["spoken_word_count"] == 10
    assert budget["global_overrun_sec"] == 3.2
    assert budget["global_tolerance_sec"] == 3.0
    assert budget["segment_overrun_sec"] == 0.649
    assert budget["duration_repair_authorized"] is True


def test_duration_repair_prompt_omits_duplicated_generation_context():
    item = {
        **_item(),
        "duration_target_sec": 4.0,
        "duration_min_sec": 2.5,
        "duration_max_sec": 4.5,
        "narration_prompt_json": "{" + "x" * 20000 + "}",
        "performance_context_json": "{" + "y" * 20000 + "}",
        "_duration_repair_authorized": True,
        "_force_duration_repair": True,
    }
    narration = _narration("Gold 4403.71 mixed.")
    performance = _performance(narration["text"])
    performance["speed"] = 1.05
    performance["pause_after_ms"] = 0

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )

    repair = json.loads(result["repair_prompt_json"])
    assert "narration_prompt_json" not in repair["item"]
    assert "performance_context_json" not in repair["item"]
    assert repair["item"]["content_goal"] == item["content_goal"]
    assert repair["item"]["resolved_visual_facts"] == item["resolved_visual_facts"]


def test_authorized_repair_allows_reduced_candidate_above_segment_target():
    item = {
        **_item(),
        "duration_target_sec": 3.0,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
        "_force_duration_repair": True,
        "_duration_repair_authorized": True,
        "_segment_overrun_sec": 1.876,
        "_pre_repair_estimated_sec": 4.876,
    }
    repaired_text = "Gold 4403.71."
    repaired = _narration(repaired_text)
    performance = _performance(repaired_text)
    performance["segment_id"] = item["segment_id"]

    result = process_step(
        item,
        None,
        None,
        _profile(),
        "mm_finance_male_02",
        "master_01",
        repair_candidate={
            "state_json": '{"repair_count":1,"narration_revision":1}',
            "segment_narration": repaired,
            "segment_performance": performance,
        },
    )

    assert result["action"] == "pass"
    assert result["done"] is True
    assert result["step_error"] == ""


def test_authorized_repair_reads_baseline_from_repair_state():
    item = {
        **_item(),
        "duration_target_sec": 3.0,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
        "_force_duration_repair": True,
        "_duration_repair_authorized": True,
    }
    repaired_text = "Gold 4403.71."
    repaired = _narration(repaired_text)
    performance = _performance(repaired_text)
    performance["segment_id"] = item["segment_id"]

    result = process_step(
        item,
        None,
        None,
        _profile(),
        "mm_finance_male_02",
        "master_01",
        repair_candidate={
            "state_json": '{"repair_count":1,"narration_revision":1,"pre_repair_estimated_sec":4.876}',
            "segment_narration": repaired,
            "segment_performance": performance,
        },
    )

    assert result["action"] == "pass"
    assert result["done"] is True


def test_authorized_repair_must_meet_backend_assigned_maximum():
    item = {
        **_item(),
        "duration_target_sec": 3.0,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
        "_force_duration_repair": True,
        "_duration_repair_authorized": True,
        "_accepted_max_estimated_sec": 4.0,
        "_duration_reduction_required_sec": 0.876,
    }
    repaired_text = "Gold 4403.71."
    repaired = _narration(repaired_text)
    performance = _performance(repaired_text)
    performance["speed"] = 1.05
    performance["pause_after_ms"] = 0

    result = process_step(
        item,
        None,
        None,
        _profile(),
        "mm_finance_male_02",
        "master_01",
        repair_candidate={
            "state_json": '{"repair_count":1,"narration_revision":1,"pre_repair_estimated_sec":4.876}',
            "segment_narration": repaired,
            "segment_performance": performance,
        },
    )

    assert result["action"] == "fail"
    assert result["step_error"].startswith("REPAIR_LIMIT_EXCEEDED;")
    assert "PRE_TTS_DURATION_REPAIR_TARGET_NOT_MET" in result["step_error"]
    assert "PRE_TTS_DURATION_REPAIR_TARGET_NOT_MET" in json.loads(
        result["result_json"]
    )["performance_error"]


def test_step_does_not_repair_short_segment_only_to_meet_a_word_duration_estimate():
    item = {
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_01_intro",
        "planning_role": "opening_hook",
        "fact_anchor_ids": ["level.current"],
        "text": "Gold is up now but risk remains near key levels.",
    }
    performance = _performance(narration["text"])
    performance["segment_id"] = "seg_01_intro"

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )

    assert result["action"] == "pass"
    assert result["done"] is True


def test_step_does_not_pad_a_short_draft_to_match_an_authored_visual_budget():
    item = {
        "segment_id": "seg_03_primary",
        "planning_role": "primary_forecast",
        "fact_anchor_ids": ["forecast.framework"],
        "duration_target_sec": 22,
        "duration_min_sec": 20.5,
        "duration_max_sec": 23.5,
    }
    narration = {
        "schema_version": "segment-narration-v2",
        "segment_id": "seg_03_primary",
        "planning_role": "primary_forecast",
        "fact_anchor_ids": ["forecast.framework"],
        "text": "If price breaks above 4452.44 and holds, the bullish continuation scenario remains valid.",
    }
    performance = _performance(narration["text"])
    performance["segment_id"] = "seg_03_primary"

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )

    assert result["action"] == "pass"
    assert result["done"] is True


def test_confirm_reads_await_wrapper_job_and_packages_media():
    step = process_step(
        _item(), _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    await_result = {
        "wait_status": "completed",
        "job": {"status": "completed", "audio_url": "https://example.test/audio.mp3", "duration_sec": 4.2},
    }
    confirmed = confirm_tts_result(_item(), step["result_json"], await_result)
    packed = json.loads(confirmed["result_json"])

    assert confirmed["action"] == "pass"
    assert packed["segment_media_input"]["audio"]["duration_sec"] == 4.2
    assert packed["segment_media_input"]["narration"]["display_text"] == _narration()["text"]
    assert packed["segment_media_input"]["narration"]["spoken_text"] == (
        "Gold holds near two thousand four hundred while confirmation remains important."
    )
    validation = packed["segment_media_input"]["duration_validation"]
    assert validation["estimated_duration_sec"] > 0
    assert validation["estimation_error_sec"] == round(4.2 - validation["estimated_duration_sec"], 3)


def test_confirm_preserves_segment_transition_for_tool09():
    item = {
        **_item(),
        "transition_out": {"type": "fade", "duration_ms": 250},
    }
    step = process_step(
        item, _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    confirmed = confirm_tts_result(
        item,
        step["result_json"],
        {"wait_status": "completed", "job": {"status": "completed", "audio_url": "https://example.test/audio.mp3", "duration_sec": 4.2}},
    )

    media = json.loads(confirmed["result_json"])["segment_media_input"]

    assert media["transition_out"] == {"type": "fade", "duration_ms": 250}


def test_confirm_preserves_complete_visual_plan_and_rescales_scene_timeline():
    item = _item()
    step = process_step(
        item, _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    confirmed = confirm_tts_result(
        item,
        step["result_json"],
        {
            "wait_status": "completed",
            "job": {
                "status": "completed",
                "audio_url": "https://example.test/audio.mp3",
                "duration_sec": 6.0,
            },
        },
    )

    media = json.loads(confirmed["result_json"])["segment_media_input"]
    assert media["order"] == 1
    assert media["visual"] == item["visual"]
    assert media["transition_out"] == item["transition_out"]
    assert media["resolved_visual_facts"][0]["anchor_id"] == "level.current"
    assert media["scenes"][0]["start_sec"] == 0.0
    assert media["scenes"][0]["duration_sec"] == 6.0
    assert media["scenes"][0]["overlay_events"][0]["start_sec"] == 0.75
    assert media["scenes"][0]["overlay_events"][0]["duration_sec"] == 3.0
    assert media["duration_validation"]["time_scale"] == 1.5
    assert media["duration_validation"]["timeline_adjusted"] is True


def test_confirm_rejects_unresolved_visual_facts():
    item = {key: value for key, value in _item().items() if key != "resolved_visual_facts"}
    step = process_step(
        item, _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    confirmed = confirm_tts_result(
        item,
        step["result_json"],
        {
            "wait_status": "completed",
            "job": {
                "status": "completed",
                "audio_url": "https://example.test/audio.mp3",
                "duration_sec": 4.0,
            },
        },
    )

    assert confirmed["action"] == "fail"
    assert confirmed["confirm_error"] == "VISUAL_FACTS_NOT_RESOLVED"


def test_second_invalid_candidate_fails_after_one_repair():
    narration = _narration("You should buy gold now.")
    result = process_step(
        _item(), narration, _performance(narration["text"]), _profile(), "mm_finance_male_02", "master_01",
        repair_count=1, narration_revision=1,
    )

    assert result["action"] == "fail"
    assert result["done"] is True
    assert result["step_error"].startswith("REPAIR_LIMIT_EXCEEDED;")
    assert "PERSONALIZED_TRADE_DIRECTIVE" in result["step_error"]


def test_confirm_accepts_actual_audio_outside_segment_duration_budget():
    step = process_step(
        _item(), _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01",
        repair_count=1, narration_revision=1,
    )
    confirmed = confirm_tts_result(
        _item(),
        step["result_json"],
        {"wait_status": "completed", "job": {"status": "completed", "audio_url": "https://example.test/audio.mp3", "duration_sec": 20}},
        state_json=step["next_state_json"],
    )
    assert confirmed["action"] == "pass"
    assert confirmed["done"] is True
    assert confirmed["confirm_error"] == ""
    media = json.loads(confirmed["result_json"])["segment_media_input"]
    assert media["audio"]["duration_sec"] == 20.0
    assert media["duration_validation"]["valid"] is True


def test_complete_accepts_segment_outside_its_budget_when_video_total_is_within_tolerance():
    item = _item()
    forged_media = {
        **item,
        "audio": {"url": "https://example.test/audio.mp3", "duration_sec": 16.5},
        "duration_validation": {
            "duration_min_sec": 2.0,
            "duration_max_sec": 8.0,
            "actual_duration_sec": 16.5,
            "valid": True,
        },
    }

    result = complete_tool08(
        [forged_media],
        _init_contracts()["segment_plan_v1_json"],
        _profile(),
    )

    assert result["complete_valid"] is True
    payload = json.loads(result["segment_media_v1_json"])
    assert payload["video_duration_validation"]["target_duration_sec"] == 4.0
    assert payload["video_duration_validation"]["actual_duration_sec"] == 16.5
    assert payload["video_duration_validation"]["tolerance_sec"] == 13.0
    assert payload["video_duration_validation"]["valid"] is True


def test_complete_rejects_video_total_outside_thirteen_second_tolerance():
    item = _item()
    forged_media = {
        **item,
        "audio": {"url": "https://example.test/audio.mp3", "duration_sec": 17.1},
        "duration_validation": {"valid": True},
    }

    result = complete_tool08(
        [forged_media],
        _init_contracts()["segment_plan_v1_json"],
        _profile(),
    )

    assert result["complete_valid"] is False
    assert result["complete_error"] == "ACTUAL_VIDEO_DURATION_OUT_OF_RANGE"
    assert json.loads(result["bad_segment_ids_json"]) == ["seg_01"]


def test_step_does_not_repair_a_long_narration_only_for_an_authored_segment_budget():
    item = {
        **_item(),
        "duration_target_sec": 3,
        "duration_min_sec": 1.5,
        "duration_max_sec": 4.5,
        "_global_overrun_sec": 2.0,
        "_global_tolerance_sec": 3.0,
        "_duration_repair_authorized": False,
        "_force_duration_repair": True,
    }
    narration = _narration(
        "Gold holds near 4434.88 while 4452.44 and 4417.32 remain important conditions."
    )
    performance = _performance(narration["text"])
    performance["pause_after_ms"] = 0

    result = process_step(
        item, narration, performance, _profile(), "mm_finance_male_02", "master_01"
    )

    assert result["action"] == "pass"
    assert result["done"] is True


def test_complete_rejects_missing_iteration_media_and_returns_external_contract():
    result = complete_tool08([], _init_contracts()["segment_plan_v1_json"], _profile())

    assert result["complete_valid"] is False
    assert result["segment_audio_valid"] is False
    assert json.loads(result["bad_segment_ids_json"]) == ["seg_01"]


def test_init_rejects_invalid_upstream_contract_version():
    contracts = _init_contracts()
    contracts["forecast_v1_json"] = "{}"
    result = initialize_tool08(**contracts)

    assert result["init_valid"] is False
    assert result["init_error"] == "FORECAST_VERSION_INVALID"


def test_complete_rejects_duplicate_iteration_output_ids():
    step = process_step(
        _item(), _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    confirmed = confirm_tts_result(
        _item(), step["result_json"],
        {"wait_status": "completed", "job": {"status": "completed", "audio_url": "https://example.test/a.mp3", "duration_sec": 4.2}},
    )
    media = json.loads(confirmed["result_json"])["segment_media_input"]
    result = complete_tool08([media, media], _init_contracts()["segment_plan_v1_json"], _profile())

    assert result["complete_valid"] is False
    assert result["complete_error"] == "SEGMENT_MEDIA_IDS_INVALID"


def test_finalize_preserves_master_request_id_and_success_contract():
    step = process_step(
        _item(), _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    confirm = confirm_tts_result(
        _item(), step["result_json"],
        {"wait_status": "completed", "job": {"status": "completed", "audio_url": "https://example.test/a.mp3", "duration_sec": 4.2}},
    )
    rendered = segment_render_success(_item(), confirm)
    result = finalize_tool08(
        [json.dumps(rendered)],
        _init_contracts()["segment_plan_v1_json"],
        _profile(),
        "master_01",
    )

    assert result["complete_valid"] is True
    assert result["master_request_id"] == "master_01"
    assert result["segment_audio_valid"] is True
    assert result["bad_segment_ids_json"] == "[]"


def test_finalize_propagates_iteration_failure_without_losing_master_id():
    result = finalize_tool08(
        [json.dumps({
            "schema_version": "segment-render-result-v1",
            "segment_valid": False,
            "segment_error": "REPAIR_LIMIT_EXCEEDED",
            "segment_id": "seg_01",
            "segment_media_input": {},
        })],
        _init_contracts()["segment_plan_v1_json"],
        _profile(),
        "master_01",
    )

    assert result["complete_valid"] is False
    assert result["master_request_id"] == "master_01"
    assert result["complete_error"] == "REPAIR_LIMIT_EXCEEDED"
    assert json.loads(result["bad_segment_ids_json"]) == ["seg_01"]


def load_tests(loader, tests, pattern):
    """Make these compact function-style contract tests runnable by unittest."""
    suite = unittest.TestSuite()
    for test in (
        test_init_returns_direct_iteration_array_and_profile,
        test_init_adds_conservative_draft_spoken_word_budget,
        test_step_soft_triggers_repair_for_candidate_outside_duration_band,
        test_step_rejects_performance_that_changes_pregeneration_preset,
        test_init_rejects_missing_master_request_id,
        test_init_rejects_missing_visual_fact_catalog,
        test_init_maps_visual_level_ids_to_catalog_level_anchors,
        test_render_step_accepts_initial_pass_without_repair,
        test_render_step_validates_the_single_repair_candidate,
        test_step_pass_builds_tts_request_without_a_per_segment_duration_target,
        test_step_expands_four_digit_prices_for_tts_but_preserves_display_text,
        test_step_preserves_requested_pause_for_estimation_and_tts_request,
        test_step_uses_spoken_forms_for_prices_percentages_times_timeframes_and_levels,
        test_price_spoken_duration_does_not_trigger_repair_when_two_prices_exceed_visual_budget,
        test_rebalance_preserves_original_segment_budgets_instead_of_stretching_audio,
        test_rebalance_marks_unfit_global_budget_for_narration_repair,
        test_spoken_word_duration_estimate_scales_with_speed,
        test_rebalance_expands_budget_for_spoken_overrun,
        test_rebalance_allows_individual_overrun_within_global_tolerance,
        test_rebalance_repairs_only_overrun_segments_after_global_tolerance_is_exceeded,
        test_rebalance_offsets_middle_overrun_with_other_segments_spare_time,
        test_rebalance_assigns_one_explicit_repair_target_for_real_global_overrun,
        test_rebalance_uses_preferred_video_maximum_before_hard_maximum,
        test_rebalance_always_repairs_an_overlong_edge_segment,
        test_step_requests_narration_repair_before_paid_tts,
        test_duration_repair_prompt_contains_provider_spoken_budget,
        test_duration_repair_prompt_omits_duplicated_generation_context,
        test_authorized_repair_allows_reduced_candidate_above_segment_target,
        test_authorized_repair_reads_baseline_from_repair_state,
        test_authorized_repair_must_meet_backend_assigned_maximum,
        test_step_does_not_repair_short_segment_only_to_meet_a_word_duration_estimate,
        test_step_does_not_pad_a_short_draft_to_match_an_authored_visual_budget,
        test_second_invalid_candidate_fails_after_one_repair,
        test_confirm_reads_await_wrapper_job_and_packages_media,
        test_confirm_preserves_complete_visual_plan_and_rescales_scene_timeline,
        test_confirm_rejects_unresolved_visual_facts,
        test_confirm_accepts_actual_audio_outside_segment_duration_budget,
        test_complete_accepts_segment_outside_its_budget_when_video_total_is_within_tolerance,
        test_complete_rejects_video_total_outside_thirteen_second_tolerance,
        test_step_does_not_repair_a_long_narration_only_for_an_authored_segment_budget,
        test_complete_rejects_missing_iteration_media_and_returns_external_contract,
        test_init_rejects_invalid_upstream_contract_version,
        test_complete_rejects_duplicate_iteration_output_ids,
        test_finalize_preserves_master_request_id_and_success_contract,
        test_finalize_propagates_iteration_failure_without_losing_master_id,
    ):
        suite.addTest(unittest.FunctionTestCase(test))
    return suite
