import unittest

from app.segment_renderer import _camera_view, _ema, _rolling_chart_window


class ContinuousChartTimelineTests(unittest.TestCase):
    def test_full_window_moves_older_candles_to_the_left(self):
        candles = [{"time": str(index)} for index in range(100)]

        start, start_left, capacity = _rolling_chart_window(candles, 0.0, 70)
        middle, middle_left, _ = _rolling_chart_window(candles, 0.5, 70)
        end, end_left, _ = _rolling_chart_window(candles, 1.0, 70)

        self.assertEqual(capacity, 70)
        self.assertEqual(start[0][0], 0)
        self.assertGreater(middle[0][0], start[0][0])
        self.assertEqual(end[0][0], 30)
        self.assertGreater(end_left, middle_left)
        self.assertEqual(start_left, 0.0)

    def test_fractional_progress_produces_fractional_horizontal_motion(self):
        candles = [{"time": str(index)} for index in range(100)]

        _visible, left_a, _ = _rolling_chart_window(candles, 0.501, 70)
        _visible, left_b, _ = _rolling_chart_window(candles, 0.502, 70)

        self.assertGreater(left_b, left_a)
        self.assertLess(left_b - left_a, 0.1)

    def test_camera_push_is_visibly_stronger_than_a_static_hold(self):
        static_scale, _ = _camera_view("static_hold", 1.0)
        zoom_scale, _ = _camera_view("slow_zoom_in", 1.0)
        focus_scale, _ = _camera_view("focus_zoom", 1.0)

        self.assertEqual(static_scale, 1.0)
        self.assertGreaterEqual(zoom_scale, 1.22)
        self.assertGreater(focus_scale, zoom_scale)

    def test_ema_uses_the_real_closing_prices_in_order(self):
        values = _ema([10.0, 12.0, 14.0], 2)

        self.assertEqual(values, [10.0, 11.333333, 13.111111])


if __name__ == "__main__":
    unittest.main()
