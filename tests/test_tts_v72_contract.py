import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import main


TEXT = "Gold holds support."
PERFORMANCE = {
    "schema_version": "tts-performance-v1",
    "segment_id": "segment_01",
    "text": TEXT,
    "delivery": "calm_analysis",
    "emotion": "calm",
    "speed": 0.96,
    "pitch": 1,
    "energy": 0.7,
    "pause_after_ms": 240,
    "cues": [],
}
NARRATION = {
    "schema_version": "narration-tts-v2",
    "segments": [{
        "segment_id": "segment_01",
        "text": TEXT,
        "performance_plan": PERFORMANCE,
    }],
}


class TtsV72ContractTests(unittest.TestCase):
    def payload(self, provider: str) -> main.TTSProxyRequest:
        return main.TTSProxyRequest(
            request_id=f"nested-{provider}",
            text=TEXT,
            narration_json=NARRATION,
            tts_provider=provider,
            allow_unverified_profile=True,
        )

    def test_all_provider_parsers_read_nested_performance_speed_and_pause(self):
        minimax = main.parse_minimax_sentence_units(self.payload("minimax"))
        dubbingx = main.parse_dubbingx_segments(self.payload("dubbingx"))
        elevenlabs = main.parse_elevenlabs_segments(self.payload("elevenlabs"))

        self.assertEqual(minimax[0]["speed"], 0.96)
        self.assertEqual(minimax[0]["pause_after_ms"], 240)
        self.assertEqual(dubbingx[0]["speed"], 0.96)
        self.assertEqual(dubbingx[0]["pause_after_ms"], 240)
        self.assertEqual(elevenlabs[0]["speed"], 0.96)
        self.assertEqual(elevenlabs[0]["pause_after_ms"], 240)

    def test_v72_model_requires_exactly_six_fields(self):
        valid = {
            "request_id": "master-001-segment_01-r0",
            "narrator_profile_id": "mm_finance_male_01",
            "text": TEXT,
            "narration_json": NARRATION,
            "target_duration_sec": 5.0,
            "duration_tolerance_sec": 1.5,
        }
        parsed = main.TTSJobV72Request.model_validate(valid)
        self.assertEqual(set(parsed.model_dump()), set(valid))

        with self.assertRaises(ValidationError):
            main.TTSJobV72Request.model_validate({**valid, "tts_provider": "minimax"})
        for required in valid:
            with self.subTest(required=required), self.assertRaises(ValidationError):
                main.TTSJobV72Request.model_validate(
                    {key: value for key, value in valid.items() if key != required}
                )

    def test_legacy_model_and_deprecated_job_route_remain_available(self):
        schema = main.app.openapi()
        legacy = schema["paths"]["/v1/tts-jobs/legacy"]["post"]
        self.assertTrue(legacy["deprecated"])
        payload = main.TTSProxyRequest(
            request_id="legacy-request",
            text=TEXT,
            tts_provider="elevenlabs",
            elevenlabs_voice_id="JBFqnCBsd6RMkjVDRZzb",
        )
        self.assertEqual(payload.tts_provider, "elevenlabs")

    def test_same_request_id_returns_existing_job_without_second_thread(self):
        request = {
            "request_id": "master-001-segment_01-r0",
            "narrator_profile_id": "verified-profile",
            "text": TEXT,
            "narration_json": NARRATION,
            "target_duration_sec": 5.0,
            "duration_tolerance_sec": 1.5,
        }
        resolved = main.TTSProxyRequest.model_validate(request)
        main.TTS_JOBS.clear()
        with (
            patch.object(main, "TOKEN", "unit-test-token"),
            patch.object(main, "AI302_API_KEY", "test-key"),
            patch.object(main, "resolve_tts_request_profile", return_value=resolved),
            patch.object(main.threading, "Thread") as thread,
            TestClient(main.app) as api,
        ):
            first = api.post(
                "/v1/tts-jobs",
                headers={"Authorization": "Bearer unit-test-token"},
                json=request,
            )
            second = api.post(
                "/v1/tts-jobs",
                headers={"Authorization": "Bearer unit-test-token"},
                json=request,
            )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(thread.call_count, 1)


if __name__ == "__main__":
    unittest.main()
