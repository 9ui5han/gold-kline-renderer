import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image

from app.chart_renderer import _observation_zones, render_tradingview_scene


def _candles():
    start = datetime(2026, 8, 4, tzinfo=timezone.utc)
    result = []
    for index in range(20):
        open_price = 4040 + index * 0.3
        close_price = open_price + (0.4 if index % 2 == 0 else -0.2)
        result.append({
            "time": (start + timedelta(minutes=15 * index)).isoformat(),
            "open": open_price,
            "high": max(open_price, close_price) + 0.5,
            "low": min(open_price, close_price) - 0.5,
            "close": close_price,
            "volume": 100 + index,
        })
    return result


def _structure_scenario(scenario_id, prior, values):
    return {
        "scenario_id": scenario_id,
        "probability_prior": prior,
        "path_points": [
            {
                "order": index,
                "time_ratio": (index - 1) / (len(values) - 1),
                "resolved_value": value,
                "direction": "flat" if index == 1 else "up",
                "phase": "start" if index == 1 else "test",
            }
            for index, value in enumerate(values, start=1)
        ],
        "touch_branch": None,
    }


class ChartRendererTests(unittest.TestCase):
    def test_singular_support_and_resistance_zones_are_supported(self):
        analysis = {
            "potential_buy_zone": {"low": 4323.31, "high": 4327.54},
            "potential_sell_zone": {"low": 4339.4, "high": 4344.35},
        }
        self.assertEqual(
            _observation_zones(analysis, "potential_buy_zones")[0]["low"],
            4323.31,
        )
        self.assertEqual(
            _observation_zones(analysis, "potential_sell_zones")[0]["high"],
            4344.35,
        )

    def test_real_renderer_writes_non_empty_rgb_frame(self):
        history = _candles()
        payload = {
            "video": {"width": 1080, "height": 1920},
            "duration_target_sec": 85,
            "historical_candles": history,
            "analysis_forecast": {
                "trend": "bullish",
                "support_levels": [4042],
                "resistance_levels": [4050],
                "potential_buy_zones": [{"low": 4040, "high": 4042}],
                "potential_sell_zones": [{"low": 4050, "high": 4052}],
                "scenarios": [{"name": "up", "candles": []}],
            },
            "forecast_paths": {
                "primary_scenario": "up",
                "alternate_scenario": "sideways",
                "scenarios": [
                    _structure_scenario("up", 0.5, [4046, 4050, 4048, 4056]),
                    _structure_scenario("sideways", 0.3, [4046, 4042, 4047]),
                    _structure_scenario("down", 0.2, [4046, 4042, 4036]),
                ],
            },
            "narration": {
                "segments": [{"order": 1, "text": "结构路径开始。"}],
                "subtitle_cues": [
                    {"start_sec": 0, "end_sec": 3, "text": "结构路径开始。"}
                ],
                "full_text": "结构路径开始。",
            },
            "style": {
                "scenario": "up",
                "show_alternate_path": True,
                "show_support_resistance": True,
                "show_observation_zones": True,
            },
            "symbol": "XAUUSD",
            "timeframe": "15m",
            "data_as_of": history[-1]["time"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "frame.png"
            render_tradingview_scene(
                output,
                payload,
                scene_index=84,
                total_scenes=85,
                current_time_sec=80,
            )
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 10_000)
            with Image.open(output) as image:
                self.assertEqual(image.size, (1080, 1920))
                self.assertEqual(image.mode, "RGB")


if __name__ == "__main__":
    unittest.main()
