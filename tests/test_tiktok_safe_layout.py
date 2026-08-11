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

    def test_youtube_keeps_full_canvas(self):
        self.assertEqual(
            resolve_safe_layout(1920, 1080, "youtube"),
            {"safe_top": 0, "safe_bottom": 1080, "safe_right": 1920, "safe_left": 0},
        )

    def test_persistent_notice_stays_inside_tiktok_safe_width(self):
        image = Image.new("RGB", (1080, 1920), "#ffffff")
        box = _draw_educational_notice(ImageDraw.Draw(image), 65, 852, 1250)
        self.assertEqual(
            EDUCATIONAL_NOTICE,
            "Educational market observation · Conditional scenarios, not trading signals",
        )
        self.assertGreaterEqual(box[0], 65)
        self.assertLessEqual(box[2], 864)


if __name__ == "__main__":
    unittest.main()
