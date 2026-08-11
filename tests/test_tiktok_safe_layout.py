import unittest

from app.chart_renderer import resolve_safe_layout


class TikTokSafeLayoutTests(unittest.TestCase):
    def test_tiktok_uses_balanced_safe_area(self):
        box = resolve_safe_layout(1080, 1920, "tiktok")
        self.assertEqual(box["safe_top"], 259)
        self.assertEqual(box["safe_bottom"], 1440)
        self.assertEqual(box["safe_right"], 864)
        self.assertEqual(box["safe_left"], 65)

    def test_youtube_keeps_full_canvas(self):
        self.assertEqual(
            resolve_safe_layout(1920, 1080, "youtube"),
            {"safe_top": 0, "safe_bottom": 1080, "safe_right": 1920, "safe_left": 0},
        )


if __name__ == "__main__":
    unittest.main()
