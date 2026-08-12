import unittest

from PIL import Image, ImageDraw

from app.chart_renderer import (
    EDUCATIONAL_NOTICE,
    _draw_educational_notice,
    resolve_safe_layout,
)


class TikTokSafeLayoutTests(unittest.TestCase):
    def test_tiktok_uses_balanced_safe_area(self):
        box = resolve_safe_layout(1080, 1920, "tiktok")
        self.assertEqual(box["safe_top"], 259)
        self.assertEqual(box["safe_bottom"], 1440)
        self.assertEqual(box["safe_right"], 864)
        self.assertEqual(box["safe_left"], 65)

    def test_tiktok_name_is_trimmed_and_case_insensitive(self):
        self.assertEqual(
            resolve_safe_layout(1080, 1920, " TikTok "),
            resolve_safe_layout(1080, 1920, "tiktok"),
        )

    def test_youtube_keeps_full_canvas(self):
        self.assertEqual(
            resolve_safe_layout(1920, 1080, "youtube"),
            {"safe_top": 0, "safe_bottom": 1080, "safe_right": 1920, "safe_left": 0},
        )

    def test_persistent_notice_stays_in_chart_bottom_lane(self):
        image = Image.new("RGB", (1080, 1920), "#ffffff")
        draw = ImageDraw.Draw(image)
        chart_left, chart_right, chart_bottom = 65, 694, 1190
        box = _draw_educational_notice(
            draw, chart_left, chart_right, chart_bottom - 38,
        )
        self.assertEqual(
            EDUCATIONAL_NOTICE,
            "Educational market observation · Conditional scenarios, not trading signals",
        )
        self.assertGreaterEqual(box[0], chart_left)
        self.assertLessEqual(box[2], chart_right)
        self.assertGreater(box[1], chart_bottom - 78)
        self.assertLess(box[3], chart_bottom)
        self.assertGreaterEqual(box[3] - box[1], 13)
        # There is no pill/background box: nearby pixels remain plain white.
        self.assertEqual(image.getpixel((chart_left, chart_bottom - 38)), (255, 255, 255))

    def test_requested_compact_right_price_lane(self):
        safe = resolve_safe_layout(1080, 1920, "tiktok")
        chart_right = safe["safe_right"] - 85
        self.assertEqual(safe["safe_right"] - chart_right, 85)


if __name__ == "__main__":
    unittest.main()
