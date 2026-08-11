import unittest

from app.chart_renderer import _chinese_subtitle_at


class BilingualSubtitleTests(unittest.TestCase):
    def narration(self):
        return {
            "subtitle_cues": [
                {
                    "start_sec": 0.0,
                    "end_sec": 2.0,
                    "text": "Gold tests resistance.",
                    "segment_id": "resistance_break_1",
                    "parent_segment_id": "resistance_break",
                },
                {
                    "start_sec": 2.0,
                    "end_sec": 4.0,
                    "text": "A closed candle confirms.",
                    "segment_id": "resistance_break_2",
                    "parent_segment_id": "resistance_break",
                },
            ],
            "bilingual_segments": [{
                "segment_id": "resistance_break",
                "spoken_text": "Gold tests resistance. A closed candle confirms.",
                "chinese_text": "黄金测试压力位，已收盘K线用于确认。",
            }],
        }

    def test_chinese_follows_active_parent_segment(self):
        narration = self.narration()
        expected = "黄金测试压力位，已收盘K线用于确认。"
        self.assertEqual(_chinese_subtitle_at(narration, 0.5), expected)
        self.assertEqual(_chinese_subtitle_at(narration, 2.5), expected)

    def test_chinese_is_empty_outside_aligned_cues(self):
        self.assertEqual(_chinese_subtitle_at(self.narration(), 4.5), "")

    def test_missing_translation_keeps_chinese_empty(self):
        narration = self.narration()
        narration["bilingual_segments"] = []
        self.assertEqual(_chinese_subtitle_at(narration, 1.0), "")


if __name__ == "__main__":
    unittest.main()
