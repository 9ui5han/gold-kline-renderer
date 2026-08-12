import unittest

from app.chart_renderer import (
    FORECAST_TURN_THRESHOLD_DEG,
    _history_window,
    _latest_prediction_segment_id,
    _partial_polyline,
    _segment_state,
    _segment_visual_path,
    _simplify_visual_polyline,
    _visible_segment_paths,
    _prediction_phase_paths,
    PREDICTION_SEGMENT_COLORS,
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
    def test_prediction_phase_starts_with_every_path_complete(self):
        paths = {"segment_paths": {
            segment_id: [
                {"time_ratio": 0, "resolved_value": 1},
                {"time_ratio": 1, "resolved_value": 2},
            ]
            for segment_id in PREDICTION_SEGMENT_COLORS
        }}
        visible = _prediction_phase_paths(paths)
        self.assertEqual(
            [item[0] for item in visible],
            ["resistance_break", "resistance_hold", "support_break", "support_hold"],
        )
        self.assertEqual([item[2] for item in visible], [1.0, 1.0, 1.0, 1.0])

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

    def test_started_paths_accumulate_and_use_four_fixed_colors(self):
        narration = {"subtitle_cues": [
            {"segment_id": segment_id, "start_sec": index * 10, "end_sec": index * 10 + 8}
            for index, segment_id in enumerate([
                "resistance_break", "resistance_hold", "support_break", "support_hold",
            ], start=1)
        ]}
        paths = {"segment_paths": {
            segment_id: [
                {"time_ratio": 0, "resolved_value": 1},
                {"time_ratio": 1, "resolved_value": 2},
            ]
            for segment_id in PREDICTION_SEGMENT_COLORS
        }}
        during_third = _visible_segment_paths(paths, narration, 35)
        self.assertEqual(
            [item[0] for item in during_third],
            ["resistance_break", "resistance_hold", "support_break"],
        )
        self.assertEqual([item[2] for item in during_third[:2]], [1.0, 1.0])
        self.assertEqual(during_third[2][2], 1.0)
        after_all = _visible_segment_paths(paths, narration, 60)
        self.assertEqual(len(after_all), 4)
        self.assertEqual(len(set(PREDICTION_SEGMENT_COLORS.values())), 4)

    def test_split_segment_pause_holds_path_progress(self):
        narration = {"subtitle_cues": [
            {"segment_id": "support_break_1", "parent_segment_id": "support_break",
             "start_sec": 10, "end_sec": 12},
            {"segment_id": "support_break_2", "parent_segment_id": "support_break",
             "start_sec": 14, "end_sec": 16},
        ]}
        paths = {"segment_paths": {"support_break": [
            {"time_ratio": 0, "resolved_value": 2},
            {"time_ratio": 1, "resolved_value": 1},
        ]}}
        pause_start = _visible_segment_paths(paths, narration, 12)[0][2]
        pause_middle = _visible_segment_paths(paths, narration, 13)[0][2]
        self.assertEqual(pause_start, 1.0)
        self.assertEqual(pause_middle, pause_start)

    def test_path_is_complete_immediately_when_segment_starts(self):
        narration = {"subtitle_cues": [{
            "segment_id": "support_break", "start_sec": 10, "end_sec": 20,
        }]}
        paths = {"segment_paths": {"support_break": [
            {"time_ratio": 0, "resolved_value": 2},
            {"time_ratio": 1, "resolved_value": 1},
        ]}}
        halfway = _visible_segment_paths(
            paths, narration, 10.01,
        )[0][2]
        complete = _visible_segment_paths(
            paths, narration, 10.02,
        )[0][2]
        self.assertEqual(halfway, 1.0)
        self.assertEqual(complete, 1.0)

    def test_turns_below_thirteen_degrees_follow_the_existing_trend(self):
        self.assertEqual(FORECAST_TURN_THRESHOLD_DEG, 13.0)
        shallow = [(0.0, 0.0), (100.0, 0.0), (200.0, 20.0)]
        clear = [(0.0, 0.0), (100.0, 0.0), (150.0, 50.0)]
        self.assertEqual(_simplify_visual_polyline(shallow), [shallow[0], shallow[2]])
        self.assertEqual(_simplify_visual_polyline(clear), clear)


if __name__ == "__main__":
    unittest.main()
