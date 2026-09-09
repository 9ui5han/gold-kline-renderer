import unittest

from app.segment_renderer import _camera_focus_price, _camera_view, _ema, _rolling_chart_window


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

    def test_camera_push_is_visibly_stronger_than_a_static_hold(self):
        static_scale, _ = _camera_view("static_hold", 1.0)
        zoom_scale, _ = _camera_view("slow_zoom_in", 1.0)
        focus_scale, _ = _camera_view("focus_zoom", 1.0)

        self.assertEqual(static_scale, 1.0)
        self.assertGreaterEqual(zoom_scale, 1.45)
        self.assertGreater(focus_scale, zoom_scale)

    def test_ema_uses_the_real_closing_prices_in_order(self):
        values = _ema([10.0, 12.0, 14.0], 2)

        self.assertEqual(values, [10.0, 11.333333, 13.111111])

    def test_focus_price_comes_from_the_scene_fact_not_a_fixed_coordinate(self):
        facts = {
            "level:resistance": {"center_price": 2405.5},
            "scenario:up": {"path_points": [{"price": 2401.0}, {"price": 2412.0}]},
        }

        self.assertEqual(_camera_focus_price(facts, ["level:resistance"]), 2405.5)
        self.assertEqual(_camera_focus_price(facts, ["scenario:up"]), 2412.0)


if __name__ == "__main__":
    unittest.main()
