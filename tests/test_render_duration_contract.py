import unittest

from pydantic import ValidationError

from app import main
from app.chart_renderer import _subtitle_at


def _history():
    return [
        {
            "time": f"2026-08-04T00:{index:02d}:00Z",
            "open": 4000 + index,
            "high": 4001 + index,
            "low": 3999 + index,
            "close": 4000.5 + index,
            "volume": 100,
        }
        for index in range(20)
    ]


class RenderDurationContractTests(unittest.TestCase):
    def test_render_request_accepts_duration_up_to_900_seconds(self):
        payload = main.RenderRequest(
            request_id="fractional-duration",
            timeframe="15m",
            data_as_of="2026-08-04T04:45:00Z",
            duration_target_sec=900,
            historical_candles=_history(),
            analysis_forecast={"trend": "sideways"},
            narration={"segments": []},
        )

        self.assertAlmostEqual(payload.duration_target_sec, 900)

    def test_render_request_rejects_duration_outside_30_to_900_seconds(self):
        for duration in (29.9, 900.1):
            with self.subTest(duration=duration):
                with self.assertRaises(ValidationError):
                    main.RenderRequest(
                        request_id="out-of-range",
                        timeframe="15m",
                        data_as_of="2026-08-04T04:45:00Z",
                        duration_target_sec=duration,
                        historical_candles=_history(),
                        analysis_forecast={"trend": "sideways"},
                        narration={"segments": []},
                    )

    def test_render_request_accepts_eighty_seconds(self):
        payload = main.RenderRequest(
            request_id="eighty-seconds",
            timeframe="15m",
            data_as_of="2026-08-04T04:45:00Z",
            duration_target_sec=80,
            historical_candles=_history(),
            analysis_forecast={"trend": "sideways"},
            narration={"segments": []},
        )
        self.assertEqual(payload.duration_target_sec, 80)

    def test_audio_video_drift_within_tolerance_is_accepted(self):
        main.validate_audio_video_duration(119.8, 120.0)

    def test_audio_video_drift_over_tolerance_is_rejected(self):
        with self.assertRaisesRegex(
            RuntimeError,
            "AUDIO_VIDEO_DURATION_MISMATCH",
        ):
            main.validate_audio_video_duration(119.8, 120.1)

    def test_subtitle_gap_does_not_fallback_to_logical_segment(self):
        narration = {
            "subtitle_cues": [
                {"start_sec": 0.0, "end_sec": 2.0, "text": "Aligned cue."},
                {"start_sec": 4.0, "end_sec": 6.0, "text": "Second cue."},
            ],
            "segments": [{"order": 1, "text": "WRONG FALLBACK TEXT."}],
            "full_text": "WRONG FULL TEXT.",
        }

        self.assertEqual(
            main.subtitle_for_time(narration, 3.0, scene_index=0),
            "",
        )
        self.assertEqual(
            main.subtitle_for_time(narration, 1.0, scene_index=0),
            "Aligned cue.",
        )

    def test_tradingview_subtitles_keep_the_final_aligned_cue(self):
        narration = {
            "subtitle_cues": [
                {"start_sec": 0.0, "end_sec": 2.0, "text": "First cue."},
                {"start_sec": 4.0, "end_sec": 6.0, "text": "Final risk cue."},
            ],
            "segments": [{"order": 1, "text": "WRONG FALLBACK TEXT."}],
        }

        self.assertEqual(
            _subtitle_at(narration, 5.0, progress=0.9),
            "Final risk cue.",
        )


if __name__ == "__main__":
    unittest.main()
