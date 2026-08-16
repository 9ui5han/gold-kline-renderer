import unittest

from app.main import RenderRequest


def request_payload():
    candles = [
        {"time": f"2026-08-10T{i:02d}:00:00Z", "open": 1, "high": 2, "low": 0.5, "close": 1.5}
        for i in range(20)
    ]
    return {
        "request_id": "timeline-contract-test",
        "timeframe": "15m",
        "data_as_of": "2026-08-10T19:00:00Z",
        "platform_profile": "tiktok",
        "historical_candles": candles,
        "analysis_forecast": {},
        "narration": {"subtitle_cues": [
            {"start_sec": index, "end_sec": index + 1, "text": segment_id,
             "segment_id": segment_id, "parent_segment_id": segment_id}
            for index, segment_id in enumerate([
                "resistance_break", "resistance_hold", "support_break", "support_hold",
            ])
        ]},
        "timeline": {
            "schema_version": "media-timeline-v1",
            "history_ratio": 0.20,
            "stage_word_tolerance": 7,
            "stage_budget_strategy": "adaptive-shared-total-v2",
            "visual_sync_strategy": "segment-id-v1",
            "history_source_candles": 90,
            "history_window_candles": 70,
            "history_freeze_segment": "technical_evidence",
            "prediction_segment_ids": [
                "resistance_break", "resistance_hold",
                "support_break", "support_hold",
            ],
        },
    }


class RailwayTimelineContractTests(unittest.TestCase):
    def test_preserves_tiktok_platform_for_safe_layout(self):
        model = RenderRequest.model_validate(request_payload())
        self.assertEqual(model.platform_profile, "tiktok")
        self.assertEqual(model.model_dump()["platform_profile"], "tiktok")

    def test_normalizes_tiktok_platform_case_and_whitespace(self):
        payload = request_payload()
        payload["platform_profile"] = " TikTok "
        model = RenderRequest.model_validate(payload)
        self.assertEqual(model.model_dump()["platform_profile"], "tiktok")

    def test_rejects_tiktok_with_non_tiktok_dimensions(self):
        payload = request_payload()
        payload["video"] = {"width": 1080, "height": 1280}
        with self.assertRaisesRegex(ValueError, "1080x1920"):
            RenderRequest.model_validate(payload)

    def test_preserves_stage_word_tolerance(self):
        model = RenderRequest.model_validate(request_payload())
        self.assertEqual(model.model_dump()["timeline"]["stage_word_tolerance"], 7)
        self.assertEqual(
            model.model_dump()["timeline"]["stage_budget_strategy"],
            "adaptive-shared-total-v2",
        )
        self.assertEqual(
            model.model_dump()["timeline"]["visual_sync_strategy"],
            "segment-id-v1",
        )

    def test_rejects_tolerance_over_seven(self):
        payload = request_payload()
        payload["timeline"]["stage_word_tolerance"] = 8
        with self.assertRaises(ValueError):
            RenderRequest.model_validate(payload)

    def test_accepts_duration_calibrated_unified_v2(self):
        payload = request_payload()
        payload["timeline"]["stage_word_tolerance"] = 5
        payload["timeline"]["stage_budget_strategy"] = (
            "duration-calibrated-unified-v2"
        )
        payload["timeline"]["target_duration_sec"] = 80
        payload["timeline"]["duration_tolerance_sec"] = 5
        payload["timeline"]["min_audio_duration_sec"] = 75
        payload["timeline"]["max_audio_duration_sec"] = 85

        model = RenderRequest.model_validate(payload)
        timeline = model.model_dump()["timeline"]

        self.assertEqual(timeline["stage_word_tolerance"], 5)
        self.assertEqual(
            timeline["stage_budget_strategy"],
            "duration-calibrated-unified-v2",
        )
        self.assertEqual(timeline["min_audio_duration_sec"], 75)
        self.assertEqual(timeline["max_audio_duration_sec"], 85)

    def test_old_request_without_timeline_still_works(self):
        payload = request_payload()
        del payload["timeline"]
        model = RenderRequest.model_validate(payload)
        self.assertEqual(model.timeline, {})

    def test_rejects_unknown_budget_strategy(self):
        payload = request_payload()
        payload["timeline"]["stage_budget_strategy"] = "independent-stage-caps"
        with self.assertRaises(ValueError):
            RenderRequest.model_validate(payload)

    def test_rejects_invalid_segment_visual_window(self):
        payload = request_payload()
        payload["timeline"]["history_window_candles"] = 71
        with self.assertRaises(ValueError):
            RenderRequest.model_validate(payload)

    def test_rejects_incomplete_prediction_segment_map(self):
        payload = request_payload()
        payload["timeline"]["prediction_segment_ids"] = ["support_break"]
        with self.assertRaises(ValueError):
            RenderRequest.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
