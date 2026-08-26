import tempfile
import unittest
import copy
from pathlib import Path

from app.photo.chart_renderer import render_chart
from app.photo.indicator_engine import (
    build_teaching_scene,
    identify_indicator,
    resolve_teaching_scene,
    validate_teaching_scene,
)


class IndicatorTeachingEngineTests(unittest.TestCase):
    def test_rsi_generic_lesson_goals_map_to_existing_scenarios(self):
        expected = {
            "overview": "range_overview",
            "state_a": "overbought_reversal",
            "state_b": "oversold_recovery",
            "components": "range_overview",
            "setup": "worked_example",
            "worked_example": "worked_example",
        }

        for lesson_goal, scenario_id in expected.items():
            with self.subTest(lesson_goal=lesson_goal):
                scene = resolve_teaching_scene({
                    "page_no": 2,
                    "visual_type": "indicator_panel",
                    "visual_focus": "RSI teaching chart",
                    "required_elements": ["RSI"],
                    "teaching_spec": {
                        "indicator_id": "rsi",
                        "indicator_kind": "oscillator",
                        "lesson_goal": lesson_goal,
                    },
                }, {"topic_text": "RSI tutorial"})
                self.assertEqual(scene["scenario_id"], scenario_id)
                self.assertTrue(scene["signal_contract_valid"])

    def test_rsi_legacy_lesson_goal_remains_supported(self):
        scene = resolve_teaching_scene({
            "page_no": 3,
            "visual_type": "zone_diagram",
            "visual_focus": "RSI overbought reversal",
            "required_elements": ["RSI", "70"],
            "teaching_spec": {
                "indicator_id": "rsi",
                "indicator_kind": "oscillator",
                "lesson_goal": "overbought_reversal",
            },
        }, {"topic_text": "RSI tutorial"})
        self.assertEqual(scene["scenario_id"], "overbought_reversal")

    def test_ict_generic_lesson_goals_map_to_existing_scenarios(self):
        expected = {
            "overview": "bullish_order_block",
            "state_a": "bullish_order_block",
            "state_b": "bearish_order_block",
            "components": "bullish_fvg",
            "setup": "bullish_liquidity_sweep",
            "worked_example": "bullish_bos",
        }

        for lesson_goal, scenario_id in expected.items():
            with self.subTest(lesson_goal=lesson_goal):
                scene = resolve_teaching_scene({
                    "page_no": 2,
                    "visual_type": "candlestick_demo",
                    "visual_focus": "ICT teaching chart",
                    "required_elements": ["ICT"],
                    "teaching_spec": {
                        "indicator_id": "ict",
                        "indicator_kind": "price_structure",
                        "lesson_goal": lesson_goal,
                    },
                }, {"topic_text": "ICT tutorial"})
                self.assertEqual(scene["scenario_id"], scenario_id)
                self.assertTrue(scene["signal_contract_valid"])

    def test_unknown_explicit_lesson_goal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "LESSON_GOAL_NOT_SUPPORTED"):
            resolve_teaching_scene({
                "page_no": 2,
                "visual_type": "indicator_panel",
                "visual_focus": "RSI teaching chart",
                "required_elements": ["RSI"],
                "teaching_spec": {
                    "indicator_id": "rsi",
                    "indicator_kind": "oscillator",
                    "lesson_goal": "not_a_real_goal",
                },
            }, {"topic_text": "RSI tutorial"})

    def test_generated_candles_have_readable_bodies_without_changing_indicator_contract(self):
        scene = build_teaching_scene("rsi", "oversold_recovery")
        visible_bodies = [
            abs(candle["close"] - candle["open"])
            for candle in scene["ohlc"]
            if abs(candle["close"] - candle["open"]) > 0.05
        ]

        self.assertGreater(len(visible_bodies), 50)
        self.assertGreater(sum(visible_bodies) / len(visible_bodies), 0.45)
        self.assertTrue(scene["signal_contract_valid"])

    def test_rsi_scene_computes_signal_from_same_ohlc_series(self):
        scene = build_teaching_scene("rsi", "oversold_recovery")

        self.assertEqual(scene["indicator_id"], "rsi")
        self.assertEqual(scene["indicator_family"], "panel")
        self.assertGreaterEqual(len(scene["ohlc"]), 40)
        self.assertEqual(len(scene["indicator_values"]), len(scene["ohlc"]))
        signal = scene["signals"][0]
        self.assertEqual(signal["signal_type"], "rsi_oversold_recovery")
        self.assertLess(scene["indicator_values"][signal["indicator_candle_index"]], 30)
        self.assertGreaterEqual(scene["indicator_values"][signal["cross_candle_index"]], 30)
        self.assertGreater(signal["confirmation_candle_index"], signal["cross_candle_index"])
        confirm = signal["confirmation_candle_index"]
        self.assertGreater(scene["ohlc"][confirm]["close"], scene["ohlc"][confirm - 1]["high"])
        self.assertTrue(scene["signal_contract_valid"])

    def test_rsi_tampered_price_confirmation_is_rejected(self):
        scene = build_teaching_scene("rsi", "oversold_recovery")
        tampered = copy.deepcopy(scene)
        tampered["signals"][0]["confirmation_candle_index"] = tampered["signals"][0]["cross_candle_index"]
        self.assertFalse(validate_teaching_scene(tampered))

    def test_rsi_tampered_series_is_recomputed_and_rejected(self):
        scene = build_teaching_scene("rsi", "oversold_recovery")
        tampered = copy.deepcopy(scene)
        tampered["indicator_values"][40] = 99.0
        self.assertFalse(validate_teaching_scene(tampered))

    def test_ict_scene_anchors_structure_to_real_candles(self):
        scene = build_teaching_scene("ict", "bullish_order_block")

        self.assertEqual(scene["indicator_id"], "ict")
        self.assertEqual(scene["indicator_family"], "price_structure")
        self.assertGreaterEqual(len(scene["ohlc"]), 40)
        signal_types = {item["signal_type"] for item in scene["signals"]}
        self.assertTrue({"liquidity_sweep", "bullish_order_block", "break_of_structure"}.issubset(signal_types))
        for signal in scene["signals"]:
            for key, value in signal.items():
                if key.endswith("_index"):
                    self.assertGreaterEqual(value, 0)
                    self.assertLess(value, len(scene["ohlc"]))
        self.assertTrue(scene["signal_contract_valid"])

    def test_ict_bearish_scene_uses_bearish_structure_and_validates(self):
        scene = build_teaching_scene("ict", "bearish_order_block")
        signal_types = {item["signal_type"] for item in scene["signals"]}
        self.assertIn("bearish_order_block", signal_types)
        self.assertNotIn("bullish_order_block", signal_types)
        self.assertTrue(validate_teaching_scene(scene))

    def test_ict_tampered_zone_is_rejected(self):
        scene = build_teaching_scene("ict", "bullish_order_block")
        tampered = copy.deepcopy(scene)
        order = next(item for item in tampered["signals"] if item["signal_type"] == "bullish_order_block")
        order["price_low"] = order["price_high"] + 10
        self.assertFalse(validate_teaching_scene(tampered))
        tampered = copy.deepcopy(scene)
        bos = next(item for item in tampered["signals"] if item["signal_type"] == "break_of_structure")
        bos["price"] = 999.0
        self.assertFalse(validate_teaching_scene(tampered))
        tampered = copy.deepcopy(scene)
        sweep = next(item for item in tampered["signals"] if item["signal_type"] == "liquidity_sweep")
        sweep["price"] = 999.0
        self.assertFalse(validate_teaching_scene(tampered))
        tampered = copy.deepcopy(scene)
        fvg = next(item for item in tampered["signals"] if item["signal_type"] == "fair_value_gap")
        fvg["price_low"], fvg["price_high"] = -999.0, -998.0
        self.assertFalse(validate_teaching_scene(tampered))

    def test_indicator_identification_uses_exact_aliases(self):
        self.assertEqual(identify_indicator("price prediction tutorial", ""), "generic")
        self.assertEqual(identify_indicator("strict risk control", ""), "generic")

    def test_topic_and_visual_focus_select_indicator_and_scenario(self):
        self.assertEqual(identify_indicator("How to use RSI", "below 30"), "rsi")
        self.assertEqual(identify_indicator("ICT order block tutorial", "liquidity sweep"), "ict")

    def test_renderer_returns_traceable_signal_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "rsi.png"
            result = render_chart({
                "page_no": 3,
                "visual_type": "zone_diagram",
                "visual_focus": "RSI below 30 then price confirmation",
                "required_elements": ["RSI", "30", "Price confirmation"],
                "annotations": [],
            }, output, {"topic_text": "How to use RSI"})

            self.assertTrue(output.is_file())
            self.assertEqual(result["teaching_engine_version"], "indicator-teaching-v1")
            self.assertEqual(result["indicator_id"], "rsi")
            self.assertGreaterEqual(result["ohlc_count"], 40)
            self.assertTrue(result["signal_contract_valid"])
            self.assertTrue(result["signal_anchors"])
            self.assertTrue(result["data_fingerprint"])


if __name__ == "__main__":
    unittest.main()
