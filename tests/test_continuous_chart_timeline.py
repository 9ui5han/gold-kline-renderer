import unittest

from app.segment_renderer import (
    _camera_focus_price,
    _camera_view,
    _ema,
    _indicator_opacity,
    _render_dynamic_frame,
    _rolling_chart_window,
)


class ContinuousChartTimelineTests(unittest.TestCase):
    def test_candles_build_to_the_right_before_a_full_window_scrolls_left(self):
        candles = [{"time": str(index)} for index in range(100)]

        start, start_left, capacity = _rolling_chart_window(candles, 0.0, 70)
        middle, middle_left, _ = _rolling_chart_window(candles, 0.5, 70)
        end, end_left, _ = _rolling_chart_window(candles, 1.0, 70)

        self.assertEqual(capacity, 70)
        self.assertEqual(start[0][0], 0)
        self.assertEqual(len(start), 1)
        self.assertGreater(len(middle), len(start))
        self.assertEqual(middle[0][0], 0)
        self.assertEqual(end[0][0], 30)
        self.assertGreater(end_left, middle_left)
        self.assertEqual(start_left, 0.0)

    def test_fractional_progress_produces_fractional_horizontal_motion(self):
        candles = [{"time": str(index)} for index in range(100)]

        _visible, left_a, _ = _rolling_chart_window(candles, 0.801, 70)
        _visible, left_b, _ = _rolling_chart_window(candles, 0.802, 70)

        self.assertGreater(left_b, left_a)
        self.assertLess(left_b - left_a, 0.1)

    def test_30fps_rolling_chart_changes_every_adjacent_frame(self):
        candles = [
            {
                "open": 100 + index * 0.2,
                "high": 101 + index * 0.2,
                "low": 99 + index * 0.2,
                "close": 100.5 + index * 0.2,
                "volume": 100 + index,
            }
            for index in range(120)
        ]
        timeline = {
            "base_duration_sec": 2.0,
            "camera_plan": [{
                "start_sec": 0.0,
                "end_sec": 2.0,
                "motion": "static_hold",
            }],
            "overlay_plan": [],
            "continuous_chart": {
                "global_start_sec": 10.0,
                "global_duration_sec": 30.0,
                "window_candles": 70,
            },
            "indicator_visibility": {
                "enabled": False,
                "rendered_indicator_ids": [],
            },
        }

        frames = [
            _render_dynamic_frame(candles, 320, 320, timeline, [], index / 30.0)
            for index in range(60)
        ]

        self.assertTrue(all(first != second for first, second in zip(frames, frames[1:])))

    def test_camera_push_is_visibly_stronger_than_a_static_hold(self):
        static_scale, _ = _camera_view("static_hold", 1.0)
        zoom_scale, _ = _camera_view("slow_zoom_in", 1.0)
        focus_scale, _ = _camera_view("focus_zoom", 1.0)

        self.assertEqual(static_scale, 1.0)
        self.assertGreaterEqual(zoom_scale, 1.45)
        self.assertGreater(focus_scale, zoom_scale)

    def test_ema_uses_sma_seed_and_leaves_warmup_empty(self):
        values = _ema([10.0, 12.0, 14.0], 2)

        self.assertEqual(values, [None, 11.0, 13.0])

    def test_indicator_opacity_starts_hidden_then_fades_once(self):
        timeline = {
            "indicator_visibility": {
                "enabled": True,
                "fade_in_ms": 250,
            }
        }

        self.assertEqual(_indicator_opacity(timeline, 0.0), 0)
        self.assertGreater(_indicator_opacity(timeline, 0.125), 0)
        self.assertLess(_indicator_opacity(timeline, 0.125), 255)
        self.assertEqual(_indicator_opacity(timeline, 0.25), 255)

        timeline["indicator_visibility"]["fade_in_ms"] = 0
        self.assertEqual(_indicator_opacity(timeline, 0.0), 255)

    def test_focus_price_comes_from_the_scene_fact_not_a_fixed_coordinate(self):
        facts = {
            "level:resistance": {"center_price": 2405.5},
            "scenario:up": {"path_points": [{"price": 2401.0}, {"price": 2412.0}]},
        }

        self.assertEqual(_camera_focus_price(facts, ["level:resistance"]), 2405.5)
        self.assertEqual(_camera_focus_price(facts, ["scenario:up"]), 2412.0)

    def test_prediction_frame_fades_ema_in_without_showing_it_before_start(self):
        candles = [
            {
                "open": 100 + index * 0.1,
                "high": 101 + index * 0.1,
                "low": 99 + index * 0.1,
                "close": 100.5 + index * 0.1,
            }
            for index in range(60)
        ]
        base = {
            "base_duration_sec": 2.0,
            "show_volume": True,
            "camera_plan": [{"start_sec": 0.0, "end_sec": 2.0, "motion": "static_hold"}],
            "overlay_plan": [],
            "continuous_chart": {
                "global_start_sec": 10.0,
                "global_duration_sec": 20.0,
                "window_candles": 70,
            },
        }
        hidden = {
            **base,
            "indicator_visibility": {
                "enabled": False,
                "fade_in_ms": 0,
                "rendered_indicator_ids": [],
            },
        }
        prediction = {
            **base,
            "indicator_visibility": {
                "enabled": True,
                "fade_in_ms": 250,
                "rendered_indicator_ids": ["dual_ema"],
            },
        }

        hidden_start = _render_dynamic_frame(candles, 240, 240, hidden, [], 0.0)
        prediction_start = _render_dynamic_frame(candles, 240, 240, prediction, [], 0.0)
        hidden_after = _render_dynamic_frame(candles, 240, 240, hidden, [], 0.3)
        prediction_after = _render_dynamic_frame(candles, 240, 240, prediction, [], 0.3)

        self.assertEqual(prediction_start, hidden_start)
        self.assertNotEqual(prediction_after, hidden_after)

    def test_focus_zoom_without_a_price_anchor_falls_back_to_stable_view(self):
        candles = [
            {"open": 100 + i, "high": 102 + i, "low": 98 + i, "close": 101 + i}
            for i in range(60)
        ]
        base = {
            "base_duration_sec": 2.0,
            "overlay_plan": [],
            "indicator_visibility": {"enabled": False, "rendered_indicator_ids": []},
        }
        focus = {
            **base,
            "camera_plan": [{
                "start_sec": 0.0,
                "end_sec": 2.0,
                "motion": "focus_zoom",
                "focus_anchor_ids": ["missing"],
            }],
        }
        stable = {
            **base,
            "camera_plan": [{"start_sec": 0.0, "end_sec": 2.0, "motion": "static_hold"}],
        }

        self.assertEqual(
            _render_dynamic_frame(candles, 240, 240, focus, [], 1.0),
            _render_dynamic_frame(candles, 240, 240, stable, [], 1.0),
        )


if __name__ == "__main__":
    unittest.main()
