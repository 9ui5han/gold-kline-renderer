import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import main
from app.tts_profiles import (
    ProfileError,
    build_profile_catalog,
    check_profile_sources,
    compile_provider_settings,
    resolve_profile,
    validate_performance_plan,
)


class TtsProfileTests(unittest.TestCase):
    def test_compiler_only_emits_documented_provider_controls(self):
        plan = {
            "text": "Gold holds support.",
            "delivery": "calm_analysis",
            "emotion": "fluent",
            "speed": 1.02,
            "pitch": 1,
            "energy": 0.75,
            "pause_after_ms": 240,
            "cues": [{"text": "holds support", "action": "emphasize"}],
        }
        minimax = compile_provider_settings(
            resolve_profile("mm_finance_male_01", allow_documented=True),
            plan,
        )
        elevenlabs = compile_provider_settings(
            resolve_profile("el_finance_male_01", allow_documented=True),
            plan,
        )

        self.assertEqual(
            set(minimax),
            {"voice_id", "speed", "vol", "pitch", "emotion"},
        )
        self.assertEqual(elevenlabs, {"voice_id": "JBFqnCBsd6RMkjVDRZzb"})
        self.assertNotIn("cues", minimax)

    def test_catalog_contains_multiple_minimax_and_elevenlabs_candidates(self):
        catalog = build_profile_catalog()

        minimax = [item for item in catalog.values() if item["provider"] == "minimax"]
        elevenlabs = [
            item for item in catalog.values() if item["provider"] == "elevenlabs"
        ]

        self.assertGreaterEqual(len(minimax), 3)
        self.assertGreaterEqual(len(elevenlabs), 3)
        self.assertTrue(all(item["profile_id"] for item in catalog.values()))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaisesRegex(ProfileError, "PROFILE_NOT_FOUND"):
            resolve_profile("does-not-exist")

    def test_documented_profile_is_allowed_only_for_preview(self):
        preview = resolve_profile("mm_finance_male_01", allow_documented=True)
        self.assertEqual(preview["voice_id"], "English_Trustworthy_Man")

        with self.assertRaisesRegex(ProfileError, "PROFILE_NOT_VERIFIED"):
            resolve_profile("mm_finance_male_01", allow_documented=False)

    def test_performance_plan_keeps_text_and_requires_exact_cues(self):
        text = "Gold is testing major resistance, but momentum is weakening."
        valid = validate_performance_plan(
            text,
            {
                "text": text,
                "delivery": "confident_explainer",
                "emotion": "calm",
                "speed": 1.02,
                "pitch": 0,
                "energy": 0.72,
                "pause_after_ms": 260,
                "cues": [
                    {"text": "major resistance", "action": "emphasize"},
                    {"text": "momentum is weakening", "action": "soften"},
                ],
            },
        )
        self.assertEqual(valid["text"], text)

        with self.assertRaisesRegex(ProfileError, "CUE_TEXT_NOT_FOUND"):
            validate_performance_plan(
                text,
                {
                    "text": text,
                    "speed": 1.0,
                    "pause_after_ms": 200,
                    "cues": [{"text": "price will rally", "action": "emphasize"}],
                },
            )

    def test_performance_plan_rejects_changed_source_text(self):
        with self.assertRaisesRegex(ProfileError, "PERFORMANCE_TEXT_MISMATCH"):
            validate_performance_plan(
                "Gold holds support.",
                {
                    "text": "Gold breaks support.",
                    "speed": 1.0,
                    "pause_after_ms": 200,
                    "cues": [],
                },
            )

    def test_free_source_checks_use_302_documented_endpoints(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append((request.method, str(request.url)))
            if str(request.url).endswith("/elevenlabs/voices"):
                return httpx.Response(
                    200,
                    request=request,
                    json={"voices": [{"voice_id": "JBFqnCBsd6RMkjVDRZzb"}]},
                )
            return httpx.Response(
                200,
                request=request,
                json={"data": {"list": [{"voiceId": "30065"}]}},
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = check_profile_sources("test-key", client=client)

        self.assertIn(
            ("GET", "https://api.302.ai/elevenlabs/voices"),
            requests,
        )
        self.assertIn(
            ("POST", "https://api.302.ai/dubbingx/v1/getTTSTimbreList"),
            requests,
        )
        self.assertEqual(result["elevenlabs"]["status"], "available")
        self.assertEqual(result["dubbingx"]["status"], "available")
        self.assertEqual(result["minimax"]["status"], "documentation_only")

    def test_profile_routes_require_bearer_auth(self):
        with (
            patch.object(main, "TOKEN", "unit-test-token"),
            TestClient(main.app) as api,
        ):
            unauthorized = api.get("/v1/tts-profiles")
            authorized = api.get(
                "/v1/tts-profiles",
                headers={"Authorization": "Bearer unit-test-token"},
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertGreaterEqual(authorized.json()["profile_count"], 7)

    def test_request_profile_overrides_legacy_provider_fields(self):
        payload = main.TTSProxyRequest(
            request_id="profile-contract",
            text="Gold holds support.",
            narrator_profile_id="mm_finance_male_01",
            allow_unverified_profile=True,
            tts_provider="dubbingx",
            voice_id="30065",
        )

        resolved = main.resolve_tts_request_profile(payload)

        self.assertEqual(resolved.tts_provider, "minimax")
        self.assertEqual(resolved.minimax_voice_id, "English_Trustworthy_Man")

    def test_paid_route_blocks_documented_but_unverified_profile(self):
        with (
            patch.object(main, "TOKEN", "unit-test-token"),
            patch.object(main, "AI302_API_KEY", "test-key"),
            TestClient(main.app) as api,
        ):
            response = api.post(
                "/v1/tts-jobs",
                headers={"Authorization": "Bearer unit-test-token"},
                json={
                    "request_id": "unverified-profile",
                    "text": "Gold holds support.",
                    "narrator_profile_id": "mm_finance_male_01",
                    "narration_json": {
                        "schema_version": "narration-tts-v2",
                        "segments": [{
                            "segment_id": "segment_01",
                            "text": "Gold holds support.",
                            "performance_plan": {
                                "text": "Gold holds support.",
                                "speed": 1.0,
                                "pause_after_ms": 200,
                                "cues": [],
                            },
                        }],
                    },
                    "target_duration_sec": 5,
                    "duration_tolerance_sec": 1.5,
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"], "PROFILE_NOT_VERIFIED")

    def test_legacy_request_without_profile_keeps_provider(self):
        payload = main.TTSProxyRequest(
            request_id="legacy-contract",
            text="Gold holds support.",
            tts_provider="elevenlabs",
            elevenlabs_voice_id="JBFqnCBsd6RMkjVDRZzb",
        )

        resolved = main.resolve_tts_request_profile(payload)

        self.assertEqual(resolved.tts_provider, "elevenlabs")
        self.assertEqual(resolved.elevenlabs_voice_id, "JBFqnCBsd6RMkjVDRZzb")


if __name__ == "__main__":
    unittest.main()
