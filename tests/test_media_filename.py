import unittest

from app.main import media_file_stem


class MediaFilenameTests(unittest.TestCase):
    def test_filename_matches_short_dynamic_video_title(self):
        stem = media_file_stem(
            {
                "timeframe": "15m",
                "data_as_of": "2026-08-11T14:45:00Z",
            },
            "a1b2c3d4-e5f6-7890",
        )
        self.assertEqual(
            stem,
            "gold-15m-scenario-review-20260811-1445-a1b2c3d4",
        )
        self.assertLessEqual(len(stem), 64)

    def test_filename_converts_offset_time_to_utc(self):
        stem = media_file_stem(
            {
                "timeframe": "1H",
                "data_as_of": "2026-08-11T22:45:00+08:00",
            },
            "ABCDEF12-3456",
        )
        self.assertEqual(
            stem,
            "gold-1h-scenario-review-20260811-1445-abcdef12",
        )


if __name__ == "__main__":
    unittest.main()
