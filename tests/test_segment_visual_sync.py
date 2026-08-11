import unittest

from app.chart_renderer import (
    _history_window,
    _latest_prediction_segment_id,
    _partial_polyline,
    _segment_state,
    _segment_visual_path,
)


def candles(count):
    return [
        {
            "time": str(index), "open": index, "high": index + 1,
            "low": index - 1, "close": index + 0.5,
        }
        for index in range(count)
    ]


class SegmentVisualSyncTests(unittest.TestCase):
    def test_parent_segment_id_controls_visual_during_sentence_suffix(self):
        narration = {"subtitle_cues": [{
            "segment_id": "support_break_1",
            "parent_segment_id": "support_break",
            "start_sec": 20,
            "end_sec": 30,
        }]}
        self.assertEqual(_segment_state(narration, 25), ("support_break", 0.5))

    def test_recent_ninety_feed_a_rolling_seventy_candle_window(self):
        history = candles(200)
        start = _history_window(history, 0, 20, 90, 70)
        middle = _history_window(history, 10, 20, 90, 70)
        frozen = _history_window(history, 20, 20, 90, 70)
        self.assertEqual(len(start), 8)
        self.assertEqual(start[0]["time"], "110")
        self.assertGreater(len(middle), len(start))
        self.assertLessEqual(len(middle), 70)
        self.assertEqual(middle[0]["time"], "110")
        self.assertEqual([item["time"] for item in frozen], [str(i) for i in range(130, 200)])

    def test_prediction_path_reveals_by_segment_progress(self):
        points = [(0.0, 0.0), (10.0, 10.0), (20.0, 0.0)]
        half = _partial_polyline(points, 0.5)
        self.assertEqual(half[-1], (10.0, 10.0))
        self.assertEqual(_partial_polyline(points, 1.0), points)

    def test_each_segment_selects_only_its_own_visual_path(self):
        paths = {
            "segment_paths": {
                "resistance_break": [{"resolved_value": 2}],
                "support_break": [{"resolved_value": 1}],
            }
        }
        self.assertEqual(
            _segment_visual_path(paths, "support_break"),
            [{"resolved_value": 1}],
        )
        self.assertEqual(_segment_visual_path(paths, "closing"), [])

    def test_macro_event_keeps_the_latest_prediction_branch(self):
        narration = {"subtitle_cues": [
            {"segment_id": "support_break_1", "parent_segment_id": "support_break", "start_sec": 20},
            {"segment_id": "macro_event_1", "parent_segment_id": "macro_event", "start_sec": 30},
        ]}
        self.assertEqual(
            _latest_prediction_segment_id(narration, 35),
            "support_break",
        )


if __name__ == "__main__":
    unittest.main()
