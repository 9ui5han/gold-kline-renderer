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
    initialize_tool08,
    process_step,
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


def test_init_generates_internal_request_id_when_workflow_has_none():
    contracts = _init_contracts()
    contracts["master_request_id"] = ""
    result = initialize_tool08(**contracts)

    assert result["init_valid"] is True
    assert result["master_request_id"].startswith("tool08-")


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
    assert parsed["tts_request"]["narration_json"]["segments"][0]["performance_plan"]["text"] == _narration()["text"]


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


def load_tests(loader, tests, pattern):
    """Make these compact function-style contract tests runnable by unittest."""
    suite = unittest.TestSuite()
    for test in (
        test_init_returns_direct_iteration_array_and_profile,
        test_init_generates_internal_request_id_when_workflow_has_none,
        test_step_pass_builds_exact_six_field_tts_request,
        test_step_requests_narration_repair_before_paid_tts,
        test_confirm_reads_await_wrapper_job_and_packages_media,
        test_complete_rejects_missing_iteration_media_and_returns_external_contract,
        test_init_rejects_invalid_upstream_contract_version,
        test_complete_rejects_duplicate_iteration_output_ids,
    ):
        suite.addTest(unittest.FunctionTestCase(test))
    return suite
