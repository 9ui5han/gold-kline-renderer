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
        "historical_candles": candles,
        "analysis_forecast": {},
        "narration": {},
        "timeline": {
            "schema_version": "media-timeline-v1",
            "history_ratio": 0.20,
            "stage_word_tolerance": 7,
            "stage_budget_strategy": "shared-total-cap-v1",
        },
    }


class RailwayTimelineContractTests(unittest.TestCase):
    def test_preserves_stage_word_tolerance(self):
        model = RenderRequest.model_validate(request_payload())
        self.assertEqual(model.model_dump()["timeline"]["stage_word_tolerance"], 7)
        self.assertEqual(
            model.model_dump()["timeline"]["stage_budget_strategy"],
            "shared-total-cap-v1",
        )

    def test_rejects_tolerance_over_seven(self):
        payload = request_payload()
        payload["timeline"]["stage_word_tolerance"] = 8
        with self.assertRaises(ValueError):
            RenderRequest.model_validate(payload)

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


if __name__ == "__main__":
    unittest.main()
