import unittest

from app import main


class DubbingxSegmentContractTests(unittest.TestCase):
    def test_parser_keeps_speed_and_real_pause_for_each_segment(self):
        payload = main.TTSProxyRequest(
            request_id="dubbingx-segments",
            text="Gold holds support. Momentum is weakening.",
            tts_provider="dubbingx",
            narration_json={
                "segments": [
                    {
                        "order": 1,
                        "text": "Gold holds support.",
                        "speed": 1.02,
                        "pause_after_ms": 260,
                    },
                    {
                        "order": 2,
                        "text": "Momentum is weakening.",
                        "speed": 0.98,
                        "pause_after_ms": 420,
                    },
                ]
            },
        )

        segments = main.parse_dubbingx_segments(payload)

        self.assertEqual([item["speed"] for item in segments], [1.02, 0.98])
        self.assertEqual(
            [item["pause_after_ms"] for item in segments],
            [260, 420],
        )

    def test_parser_rejects_text_drift_before_paid_request(self):
        payload = main.TTSProxyRequest(
            request_id="dubbingx-drift",
            text="Gold holds support.",
            tts_provider="dubbingx",
            narration_json={
                "segments": [
                    {
                        "order": 1,
                        "text": "Gold breaks support.",
                        "speed": 1.0,
                        "pause_after_ms": 200,
                    }
                ]
            },
        )

        with self.assertRaisesRegex(ValueError, "完整旁白不一致"):
            main.parse_dubbingx_segments(payload)


if __name__ == "__main__":
    unittest.main()
