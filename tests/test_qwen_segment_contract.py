import json
import unittest

from app import main


def payload_for(segments, target_duration_sec=115):
    text = " ".join(item["text"] for item in segments)
    return main.TTSProxyRequest(
        request_id="qwen-contract-test",
        text=text,
        speech_text=text,
        narration_json={"segments": segments},
        tts_provider="qwen3_tts",
        qwen3_voice="Elias",
        target_duration_sec=target_duration_sec,
    )


class QwenSegmentContractTests(unittest.TestCase):
    def test_qwen_request_reads_each_dify_segment_and_pause(self):
        payload = payload_for([
            {"order": 1, "text": "First sentence.", "pause_after_ms": 320},
            {"order": 2, "text": "Second sentence.", "pause_after_ms": 420},
        ])
        segments = main.parse_qwen3_segments(payload)
        self.assertEqual(segments[0]["pause_after_ms"], 320)
        self.assertEqual(segments[1]["text"], "Second sentence.")

    def test_qwen_boundary_cues_are_one_per_segment(self):
        cues = main.build_segment_boundary_subtitle_cues(
            [
                {"text": "First sentence.", "start_sec": 0, "end_sec": 2},
                {"text": "Second sentence.", "start_sec": 2.32, "end_sec": 4},
            ],
            4.42,
            "First sentence. Second sentence.",
        )
        self.assertEqual(len(cues), 2)

    def test_qwen_segment_bounds_include_pause_after_ms(self):
        segments = main.parse_qwen3_segments(
            payload_for([
                {"order": 1, "text": "First sentence.", "pause_after_ms": 320},
                {"order": 2, "text": "Second sentence.", "pause_after_ms": 420},
            ])
        )
        bounds = main.build_qwen_segment_bounds(segments, [2.0, 3.0])
        self.assertEqual(bounds[0]["start_sec"], 0.0)
        self.assertEqual(bounds[0]["end_sec"], 2.0)
        self.assertEqual(bounds[1]["start_sec"], 2.32)
        self.assertEqual(bounds[1]["end_sec"], 5.32)

    def test_qwen_parser_rejects_non_integer_pause(self):
        with self.assertRaisesRegex(ValueError, "不是整数"):
            main.parse_qwen3_segments(
                payload_for([
                    {"order": 1, "text": "First sentence.", "pause_after_ms": 320.0},
                    {"order": 2, "text": "Second sentence.", "pause_after_ms": 420},
                ])
            )

    def test_audio_duration_contract_is_105_to_120_seconds(self):
        main.validate_tts_duration_contract(105, 115)
        main.validate_tts_duration_contract(120, 115)
        with self.assertRaisesRegex(RuntimeError, "TTS_AUDIO_DURATION_OUT_OF_RANGE"):
            main.validate_tts_duration_contract(104.9, 115)


if __name__ == "__main__":
    unittest.main()
