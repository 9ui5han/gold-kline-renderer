import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

from app.photo.chart_renderer import render_chart
from app.photo.indicators.registry import (
    ENGINE_REGISTRY,
    IndicatorRegistry,
)


class GenericIndicatorRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = IndicatorRegistry.default()

    def test_six_generic_engines_are_registered(self):
        self.assertEqual(
            set(ENGINE_REGISTRY),
            {
                "range_oscillator",
                "line_crossover",
                "price_overlay",
                "volatility",
                "volume",
                "price_structure",
            },
        )

    def test_first_indicator_configs_are_loadable(self):
        expected = {
            "rsi": "range_oscillator",
            "ict": "price_structure",
            "macd": "line_crossover",
            "moving_average": "price_overlay",
            "bollinger": "price_overlay",
            "atr": "volatility",
            "kdj": "range_oscillator",
            "obv": "volume",
        }
        for indicator_id, engine_id in expected.items():
            with self.subTest(indicator_id=indicator_id):
                config = self.registry.get_config(indicator_id)
                self.assertEqual(config["engine_id"], engine_id)

    def test_every_first_indicator_builds_a_valid_overview_scene(self):
        for indicator_id in (
            "rsi", "ict", "macd", "moving_average",
            "bollinger", "atr", "kdj", "obv",
        ):
            with self.subTest(indicator_id=indicator_id):
                config = self.registry.get_config(indicator_id)
                scene = self.registry.build_scene(
                    indicator_id,
                    "overview",
                    {
                        "page_no": 2,
                        "visual_type": "indicator_panel",
                        "required_elements": [indicator_id],
                    },
                    {"topic_text": f"{indicator_id} tutorial"},
                )
                self.assertEqual(scene["indicator_id"], indicator_id)
                self.assertEqual(scene["engine_id"], config["engine_id"])
                self.assertTrue(scene["signal_contract_valid"])
                self.assertGreaterEqual(len(scene["ohlc"]), 40)
                self.assertTrue(scene["data_fingerprint"])

    def test_rsi_four_scenarios_are_distinct_and_semantically_bound(self):
        goals = {
            "overview": "rsi_range_overview",
            "state_a": "rsi_overbought_reversal",
            "state_b": "rsi_oversold_recovery",
            "worked_example": "rsi_worked_example",
        }
        scenes = {}
        for lesson_goal, signal_type in goals.items():
            scene = self.registry.build_scene("rsi", lesson_goal, {}, {})
            scenes[lesson_goal] = scene
            self.assertEqual(scene["signals"][0]["signal_type"], signal_type)
            self.assertTrue(scene["signal_contract_valid"])

        self.assertEqual(
            len({scene["data_fingerprint"] for scene in scenes.values()}),
            4,
        )
        self.assertEqual(scenes["overview"]["signals"][0]["levels"], [30, 50, 70])
        self.assertNotIn("price_confirmation", scenes["overview"]["layers"])
        self.assertIn("lesson_steps", scenes["worked_example"]["layers"])

    def test_unknown_indicator_does_not_fall_back_to_rsi(self):
        with self.assertRaisesRegex(ValueError, "INDICATOR_NOT_REGISTERED:unknown_magic"):
            self.registry.build_scene("unknown_magic", "overview", {}, {})

    def test_indicator_kind_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "INDICATOR_KIND_MISMATCH"):
            self.registry.build_scene(
                "rsi",
                "overview",
                {"teaching_spec": {"indicator_kind": "overlay"}},
                {},
            )

    def test_registered_indicators_render_through_the_shared_chart_contract(self):
        kinds = {
            "rsi": "oscillator",
            "ict": "price_structure",
            "macd": "oscillator",
            "moving_average": "overlay",
            "bollinger": "overlay",
            "atr": "oscillator",
            "kdj": "oscillator",
            "obv": "oscillator",
        }
        with tempfile.TemporaryDirectory() as directory:
            for page_no, (indicator_id, kind) in enumerate(kinds.items(), start=1):
                with self.subTest(indicator_id=indicator_id):
                    output = Path(directory) / f"{indicator_id}.png"
                    result = render_chart({
                        "page_no": page_no,
                        "visual_type": "candlestick_demo" if kind in {"overlay", "price_structure"} else "indicator_panel",
                        "visual_focus": f"{indicator_id} overview",
                        "required_elements": [indicator_id],
                        "teaching_spec": {
                            "indicator_id": indicator_id,
                            "indicator_kind": kind,
                            "lesson_goal": "overview",
                        },
                    }, output, {"topic_text": f"{indicator_id} tutorial"})
                    self.assertTrue(output.is_file())
                    self.assertEqual(result["indicator_id"], indicator_id)
                    self.assertEqual(result["engine_id"], self.registry.get_config(indicator_id)["engine_id"])
                    self.assertTrue(result["signal_contract_valid"])

    def test_rsi_rendered_scenarios_have_distinct_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            digests = set()
            for page_no, goal in enumerate(("overview", "state_a", "state_b", "worked_example"), start=2):
                output = Path(directory) / f"{goal}.png"
                render_chart({
                    "page_no": page_no,
                    "visual_type": "candlestick_demo" if goal == "worked_example" else "indicator_panel",
                    "visual_focus": goal,
                    "required_elements": ["RSI"],
                    "teaching_spec": {
                        "indicator_id": "rsi",
                        "indicator_kind": "oscillator",
                        "lesson_goal": goal,
                    },
                }, output, {"topic_text": "RSI tutorial"})
                digests.add(hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(len(digests), 4)

    def test_generic_engines_reject_tampered_indicator_values(self):
        for indicator_id in ("macd", "moving_average", "bollinger", "atr", "kdj", "obv"):
            with self.subTest(indicator_id=indicator_id):
                scene = self.registry.build_scene(indicator_id, "overview", {}, {})
                tampered = copy.deepcopy(scene)
                values = tampered["indicator_values"]
                if isinstance(values, dict):
                    series = next(item for item in values.values() if isinstance(item, list))
                else:
                    series = values
                index = next(i for i, value in enumerate(series) if value is not None)
                series[index] = float(series[index]) + 99.0
                self.assertFalse(self.registry.validate_scene(tampered))

    def test_generic_engines_reject_out_of_range_signal_anchor(self):
        for indicator_id in ("macd", "moving_average", "bollinger", "atr", "kdj", "obv"):
            with self.subTest(indicator_id=indicator_id):
                scene = self.registry.build_scene(indicator_id, "overview", {}, {})
                tampered = copy.deepcopy(scene)
                tampered["signals"][0]["event_index"] = len(scene["ohlc"]) + 10
                self.assertFalse(self.registry.validate_scene(tampered))


if __name__ == "__main__":
    unittest.main()
