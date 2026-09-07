import json
import sys
import types
import unittest

# These unit tests exercise deterministic TOOL-08 logic only.  The project
# runtime supplies httpx; this lightweight stub keeps the test runnable in a
# minimal local Python installation without changing production imports.
sys.modules.setdefault("httpx", types.ModuleType("httpx"))

from app.segment_narration_validation import (
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
        "planning_role": "technical_context",
        "fact_anchor_ids": ["level.current"],
        "duration_target_sec": 4,
        "duration_min_sec": 2,
        "duration_max_sec": 8,
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
    assert result["segments"][0]["segment_id"] == "seg_01"
    assert isinstance(result["segments"][0]["narration_prompt_json"], str)
    assert result["master_request_id"].startswith("master_01")


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


def test_step_pass_builds_exact_six_field_tts_request():
    result = process_step(
        _item(), _narration(), _performance(), _profile(), "mm_finance_male_02", "master_01"
    )
    parsed = json.loads(result["result_json"])

    assert result["action"] == "pass"
    assert result["done"] is True
    assert set(parsed["tts_request"]) == {
        "request_id", "narrator_profile_id", "text", "narration_json",
        "target_duration_sec", "duration_tolerance_sec",
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


def test_price_spoken_duration_triggers_repair_when_two_prices_do_not_fit_short_segment():
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

    assert result["action"] == "repair_narration"
    assert "PRE_TTS_WORD_DURATION_OUT_OF_RANGE" in json.loads(
        result["repair_prompt_json"]
    )["validator_errors"]


def test_rebalance_keeps_total_duration_and_lends_time_to_spoken_price_segment():
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
    assert scheduled[0]["item"]["duration_target_sec"] > 3.0
    assert scheduled[0]["estimated_spoken_sec"] <= scheduled[0]["item"]["duration_max_sec"]


def test_rebalance_keeps_original_budget_when_spoken_text_requires_repair():
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
    assert result["content_fit_valid"] is False
    assert result["content_fit_error"] == "TOTAL_SPOKEN_DURATION_EXCEEDS_VIDEO_BUDGET"
    assert len(result["scheduled_items"]) == 1
    assert result["scheduled_items"][0]["needs_narration_repair"] is True
    assert result["scheduled_total_sec"] == 3.0


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


def test_step_repairs_short_segment_with_too_many_short_words_before_tts():
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

    assert result["action"] == "repair_narration"
    assert result["done"] is False
    assert "PRE_TTS_WORD_DURATION_OUT_OF_RANGE" in json.loads(
        result["repair_prompt_json"]
    )["validator_errors"]


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


def test_second_invalid_candidate_fails_after_one_repair():
    narration = _narration("You should buy gold now.")
    result = process_step(
        _item(), narration, _performance(narration["text"]), _profile(), "mm_finance_male_02", "master_01",
        repair_count=1, narration_revision=1,
    )

    assert result["action"] == "fail"
    assert result["done"] is True
    assert result["step_error"] == "REPAIR_LIMIT_EXCEEDED"


def test_confirm_duration_outside_budget_fails_after_one_repair_policy():
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
    assert confirmed["action"] == "fail"
    assert confirmed["done"] is True
    assert confirmed["confirm_error"] == "ACTUAL_DURATION_OUT_OF_RANGE"


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
        test_init_rejects_missing_master_request_id,
        test_render_step_accepts_initial_pass_without_repair,
        test_render_step_validates_the_single_repair_candidate,
        test_step_pass_builds_exact_six_field_tts_request,
        test_step_expands_four_digit_prices_for_tts_but_preserves_display_text,
        test_step_uses_spoken_forms_for_prices_percentages_times_timeframes_and_levels,
        test_price_spoken_duration_triggers_repair_when_two_prices_do_not_fit_short_segment,
        test_rebalance_keeps_total_duration_and_lends_time_to_spoken_price_segment,
        test_rebalance_keeps_original_budget_when_spoken_text_requires_repair,
        test_step_requests_narration_repair_before_paid_tts,
        test_step_repairs_short_segment_with_too_many_short_words_before_tts,
        test_second_invalid_candidate_fails_after_one_repair,
        test_confirm_reads_await_wrapper_job_and_packages_media,
        test_confirm_duration_outside_budget_fails_after_one_repair_policy,
        test_complete_rejects_missing_iteration_media_and_returns_external_contract,
        test_init_rejects_invalid_upstream_contract_version,
        test_complete_rejects_duplicate_iteration_output_ids,
        test_finalize_preserves_master_request_id_and_success_contract,
        test_finalize_propagates_iteration_failure_without_losing_master_id,
    ):
        suite.addTest(unittest.FunctionTestCase(test))
    return suite
