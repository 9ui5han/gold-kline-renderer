import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
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

    def test_v72_model_requires_nested_performance_plan_for_every_segment(self):
        with self.assertRaises(ValidationError):
            main.TTSJobV72Request.model_validate({
                "request_id": "missing-performance",
                "narrator_profile_id": "mm_finance_male_01",
                "text": TEXT,
                "narration_json": {
                    "schema_version": "narration-tts-v2",
                    "segments": [{"segment_id": "segment_01", "text": TEXT}],
                },
                "target_duration_sec": 5.0,
                "duration_tolerance_sec": 1.5,
            })

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
        with TemporaryDirectory() as directory:
            registry_path = Path(directory) / "tts-idempotency.json"
            main.TTS_JOBS.clear()
            with (
                patch.object(main, "TOKEN", "unit-test-token"),
                patch.object(main, "AI302_API_KEY", "test-key"),
                patch.object(main, "TTS_IDEMPOTENCY_PATH", registry_path),
                patch.object(main, "resolve_tts_request_profile", return_value=resolved),
                patch.object(main, "start_tts_job_worker") as start_worker,
                TestClient(main.app) as api,
            ):
                first = api.post(
                    "/v1/tts-jobs",
                    headers={"Authorization": "Bearer unit-test-token"},
                    json=request,
                )
                main.TTS_JOBS.clear()
                second = api.post(
                    "/v1/tts-jobs",
                    headers={"Authorization": "Bearer unit-test-token"},
                    json=request,
                )

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        self.assertEqual(start_worker.call_count, 1)

    def test_same_request_id_with_different_payload_returns_conflict(self):
        first = main.TTSProxyRequest(
            request_id="stable-id",
            text=TEXT,
            narration_json=NARRATION,
            narrator_profile_id="verified-profile",
            target_duration_sec=5,
            duration_tolerance_sec=1.5,
        )
        changed = first.model_copy(update={"text": "Gold breaks support."})
        with TemporaryDirectory() as directory:
            with (
                patch.object(
                    main,
                    "TTS_IDEMPOTENCY_PATH",
                    Path(directory) / "tts-idempotency.json",
                ),
                patch.object(main, "resolve_tts_request_profile", side_effect=lambda x: x),
                patch.object(main, "AI302_API_KEY", "test-key"),
                patch.object(main, "start_tts_job_worker"),
            ):
                main.TTS_JOBS.clear()
                main.enqueue_tts_job(first)
                main.TTS_JOBS.clear()
                with self.assertRaisesRegex(main.HTTPException, "REQUEST_ID_CONFLICT"):
                    main.enqueue_tts_job(changed)

    def test_await_route_returns_completed_job_without_a_dify_poll_loop(self):
        request = {
            "request_id": "await-completed",
            "narrator_profile_id": "verified-profile",
            "text": TEXT,
            "narration_json": NARRATION,
            "target_duration_sec": 5.0,
            "duration_tolerance_sec": 1.5,
        }
        completed_job = {
            "job_id": "job-completed",
            "request_id": request["request_id"],
            "status": "completed",
            "audio_url": "https://example.invalid/audio.mp3",
            "duration_sec": 5.1,
        }
        with (
            patch.object(main, "TOKEN", "unit-test-token"),
            patch.object(main, "enqueue_tts_job", return_value=completed_job),
            TestClient(main.app) as api,
        ):
            main.TTS_JOBS["job-completed"] = dict(completed_job)
            response = api.post(
                "/v1/tts-jobs/await",
                headers={"Authorization": "Bearer unit-test-token"},
                json=request,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["wait_status"], "completed")
        self.assertEqual(response.json()["job"]["duration_sec"], 5.1)

    def test_await_route_returns_timeout_without_changing_background_job(self):
        request = {
            "request_id": "await-timeout",
            "narrator_profile_id": "verified-profile",
            "text": TEXT,
            "narration_json": NARRATION,
            "target_duration_sec": 5.0,
            "duration_tolerance_sec": 1.5,
        }
        pending_job = {
            "job_id": "job-pending",
            "request_id": request["request_id"],
            "status": "processing",
            "audio_url": "",
            "duration_sec": 0,
        }
        with (
            patch.object(main, "TOKEN", "unit-test-token"),
            patch.object(main, "TTS_AWAIT_TIMEOUT_SEC", 0),
            patch.object(main, "enqueue_tts_job", return_value=pending_job),
            TestClient(main.app) as api,
        ):
            main.TTS_JOBS["job-pending"] = dict(pending_job)
            response = api.post(
                "/v1/tts-jobs/await",
                headers={"Authorization": "Bearer unit-test-token"},
                json=request,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["wait_status"], "timeout")
        self.assertEqual(response.json()["error_code"], "TTS_WAIT_TIMEOUT")
        self.assertEqual(main.TTS_JOBS["job-pending"]["status"], "processing")


if __name__ == "__main__":
    unittest.main()
