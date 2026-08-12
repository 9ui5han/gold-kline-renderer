import importlib.util
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = Path(
    "/Users/qiushan/Documents/GitHub/gold-kline-renderer/app/chart_renderer.py"
)
SPEC = importlib.util.spec_from_file_location(
    "renderer_english_utc", RENDERER_PATH
)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class RendererEnglishUtcTests(unittest.TestCase):
    def test_header_time_is_explicit_utc(self):
        self.assertEqual(
            renderer._header_time_label("2026-08-06T14:30:00Z"),
            "Aug 06, 2026 14:30 UTC",
        )

    def test_offset_time_is_converted_to_utc(self):
        self.assertEqual(
            renderer._header_time_label("2026-08-06T22:30:00+08:00"),
            "Aug 06, 2026 14:30 UTC",
        )

    def test_video_header_uses_dynamic_market_contract(self):
        self.assertEqual(
            renderer._video_title("15m"),
            "Gold 15m Scenario Review",
        )
        self.assertEqual(
            renderer._latest_closed_candle_label(
                "XAUUSD",
                "2026-08-06T22:30:00+08:00",
            ),
            "XAUUSD · Aug 06, 2026 14:30 UTC",
        )

    def test_axis_time_uses_english_month(self):
        self.assertEqual(
            renderer._axis_time_label("2026-08-06T14:30:00Z"),
            "Aug 06 14:30",
        )

    def test_fixed_video_labels_are_english(self):
        self.assertEqual(
            renderer.VIDEO_LABELS["last_closed_candle"],
            "Last Closed Candle",
        )
        self.assertTrue(
            all(
                not re.search(r"[\u4e00-\u9fff]", value)
                for value in renderer.VIDEO_LABELS.values()
            )
        )

    def test_scenario_names_are_english(self):
        self.assertEqual(renderer._scenario_text("sideways"), "Range-bound")
        self.assertEqual(renderer._scenario_text("up"), "Bullish")
        self.assertEqual(renderer._scenario_text("down"), "Bearish")


if __name__ == "__main__":
    unittest.main()
