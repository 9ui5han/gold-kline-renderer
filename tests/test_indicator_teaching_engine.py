import tempfile
import unittest
import copy
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.photo import chart_renderer
from app.photo.chart_renderer import render_chart
from app.photo.indicator_engine import (
    build_teaching_scene,
    identify_indicator,
    resolve_teaching_scene,
    validate_teaching_scene,
)
from app.photo.indicators.contracts import (
    CANDLE_BODY_SCALE,
    TEACHING_CANDLE_COUNT,
    candles_from_closes,
    ohlc_series_valid,
)


class IndicatorTeachingEngineTests(unittest.TestCase):
    def test_generated_scenes_keep_full_valid_candle_series_and_indicator_lengths(self):
        scenes = {
            indicator_id: build_teaching_scene(indicator_id, "overview")
            for indicator_id in ("rsi", "kdj", "macd", "bollinger", "moving_average", "atr", "obv", "ict")
        }

        for indicator_id, scene in scenes.items():
            with self.subTest(indicator_id=indicator_id):
                candles = scene["ohlc"]
                self.assertGreaterEqual(len(candles), TEACHING_CANDLE_COUNT)
                self.assertTrue(ohlc_series_valid(candles))
                candle_shapes = {
                    tuple(candle[key] for key in ("open", "high", "low", "close"))
                    for candle in candles
                }
                self.assertEqual(len(candle_shapes), len(candles))
                self.assertTrue(scene["signal_contract_valid"])

        rsi_values = scenes["rsi"]["indicator_values"]
        self.assertIsInstance(rsi_values, list)
        self.assertEqual(len(rsi_values), len(scenes["rsi"]["ohlc"]))

        macd_values = scenes["macd"]["indicator_values"]
        self.assertIsInstance(macd_values, dict)
        self.assertEqual(set(macd_values), {"main", "signal", "histogram"})
        for values in macd_values.values():
            self.assertEqual(len(values), len(scenes["macd"]["ohlc"]))

    def test_every_indicator_uses_valid_display_ohlc_before_drawing(self):
        for indicator_id in ("rsi", "kdj", "macd", "bollinger", "moving_average", "atr", "obv", "ict"):
            with self.subTest(indicator_id=indicator_id):
                source = build_teaching_scene(indicator_id, "overview")["ohlc"]
                display = chart_renderer._display_candles(source)

                self.assertEqual(len(display), len(source))
                self.assertTrue(ohlc_series_valid(display))
                for index in range(1, len(display)):
                    self.assertAlmostEqual(
                        display[index]["open"], display[index - 1]["close"], places=4,
                    )

    def test_validator_rejects_short_or_invalid_ohlc_before_signal_validation(self):
        for indicator_id in ("rsi", "kdj", "macd", "bollinger", "moving_average", "atr", "obv", "ict"):
            with self.subTest(indicator_id=indicator_id):
                scene = build_teaching_scene(indicator_id, "overview")
                short_scene = copy.deepcopy(scene)
                short_scene["ohlc"] = short_scene["ohlc"][:TEACHING_CANDLE_COUNT - 1]
                values = short_scene["indicator_values"]
                if isinstance(values, dict):
                    short_scene["indicator_values"] = {
                        key: value[:TEACHING_CANDLE_COUNT - 1]
                        for key, value in values.items()
                    }
                elif isinstance(values, list):
                    short_scene["indicator_values"] = values[:TEACHING_CANDLE_COUNT - 1]
                self.assertFalse(validate_teaching_scene(short_scene))

                invalid_scene = copy.deepcopy(scene)
                invalid_scene["ohlc"][0]["low"] = invalid_scene["ohlc"][0]["high"] + 1
                self.assertFalse(validate_teaching_scene(invalid_scene))

    def test_atr_validator_rejects_nonnumeric_ohlc_without_raising(self):
        scene = build_teaching_scene("atr", "overview")
        tampered = copy.deepcopy(scene)
        tampered["ohlc"][0]["high"] = "not-a-number"

        self.assertFalse(validate_teaching_scene(tampered))

    def test_rsi_scenarios_have_distinct_data_and_teaching_contracts(self):
        scenarios = {
            name: build_teaching_scene("rsi", name)
            for name in (
                "range_overview",
                "overbought_reversal",
                "oversold_recovery",
                "worked_example",
            )
        }

        fingerprints = {
            scene["data_fingerprint"] for scene in scenarios.values()
        }
        self.assertEqual(len(fingerprints), 4)
        self.assertEqual(
            scenarios["range_overview"]["signals"][0]["signal_type"],
            "rsi_range_overview",
        )
        self.assertEqual(
            scenarios["range_overview"]["signals"][0]["levels"],
            [30, 50, 70],
        )
        self.assertNotIn(
            "price_confirmation",
            scenarios["range_overview"]["layers"],
        )
        self.assertEqual(
            scenarios["worked_example"]["signals"][0]["signal_type"],
            "rsi_worked_example",
        )
        self.assertIn("lesson_steps", scenarios["worked_example"]["layers"])

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

    def test_rsi_and_macd_charts_use_shared_smooth_full_width_layout(self):
        cases = (
            ("rsi", "RSI below 30 then price confirmation", "zone_diagram"),
            ("macd", "MACD bullish crossover", "indicator_panel"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for page_no, (indicator_id, focus, visual_type) in enumerate(cases, start=20):
                with self.subTest(indicator_id=indicator_id):
                    result = render_chart({
                        "page_no": page_no,
                        "visual_type": visual_type,
                        "visual_focus": focus,
                        "required_elements": [indicator_id.upper()],
                        "annotations": [],
                        "teaching_spec": {
                            "indicator_id": indicator_id,
                            "indicator_kind": "oscillator",
                            "lesson_goal": "state_a",
                        },
                    }, Path(directory) / f"{indicator_id}.png")

                    self.assertEqual(result["line_renderer"], "supersampled_catmull_rom")
                    self.assertGreaterEqual(result["line_supersample"], 4)
                    self.assertFalse(result["left_plot_border"])
                    self.assertFalse(result["right_plot_border"])
                    self.assertEqual(result["ohlc_count"], 68)
                    self.assertFalse(result["label_overlap"])
                    layout = result["chart_layout"]
                    self.assertEqual(layout["plot_edges"], [0, 900])
                    self.assertEqual(layout["price_plot_edges"], [0, 900])
                    self.assertEqual(layout["label_left"], 44)
                    self.assertTrue(all(
                        item["bounds"][0] >= layout["label_left"]
                        for item in layout["title_bounds"]
                    ))
                    self.assertGreater(layout["candle_pitch"], 0)
                    self.assertAlmostEqual(layout["candle_body_width"], 10.0)
                    self.assertEqual(layout["price_plot_edges"], layout["indicator_plot_edges"])

    def test_rsi_signal_annotations_are_bilingual_centered_and_translucent(self):
        with tempfile.TemporaryDirectory() as directory:
            for language, expected_confirmation in (
                ("zh-CN", "价格确认"),
                ("en-US", "Price confirmation"),
            ):
                with self.subTest(language=language):
                    result = render_chart({
                        "page_no": 30,
                        "visual_type": "zone_diagram",
                        "visual_focus": "RSI below 30 then price confirmation",
                        "required_elements": ["RSI", "30", expected_confirmation],
                        "annotations": [],
                        "teaching_spec": {
                            "indicator_id": "rsi",
                            "indicator_kind": "oscillator",
                            "lesson_goal": "state_b",
                        },
                    }, Path(directory) / f"rsi-{language}.png", language=language)

                    annotations = result["annotation_bounds"]
                    confirmation = next(
                        item for item in annotations if item["text"] == expected_confirmation
                    )
                    self.assertTrue(confirmation["text_centered"])
                    self.assertGreater(confirmation["background_alpha"], 0)
                    self.assertLess(confirmation["background_alpha"], 255)
                    self.assertFalse(result["label_overlap"])
                    if language.startswith("en"):
                        self.assertNotIn("K-line", " ".join(result["rendered_labels"]))

    def test_candle_open_equals_previous_close_and_preserves_valid_ohlc(self):
        closes = [100.0, 101.0, 99.5, 102.0]
        candles = candles_from_closes(closes)

        self.assertEqual(CANDLE_BODY_SCALE, 1.0)
        expected_previous = closes[0] - 0.35
        for index, candle in enumerate(candles):
            previous_close = expected_previous if index == 0 else closes[index - 1]
            movement = closes[index] - previous_close
            self.assertAlmostEqual(candle["open"], previous_close, places=4)
            self.assertLessEqual(candle["low"], min(candle["open"], candle["close"]))
            self.assertGreaterEqual(candle["high"], max(candle["open"], candle["close"]))
            self.assertEqual(candle["close"] >= candle["open"], movement >= 0)

    def test_drawn_candle_geometry_uses_the_exact_ohlc_values(self):
        candle = {"open": 102.0, "high": 108.0, "low": 96.0, "close": 99.0}
        geometry = chart_renderer._candle_geometry(candle, lambda value: -value)

        self.assertEqual(geometry["body_top"], -102.0)
        self.assertEqual(geometry["body_bottom"], -99.0)
        self.assertEqual(geometry["wick_top"], -108.0)
        self.assertEqual(geometry["wick_bottom"], -96.0)

    def test_display_candles_preserve_source_open_close_and_valid_ohlc(self):
        source_candles = candles_from_closes([
            100.0, 100.2, 100.0, 100.8, 100.1, 101.4,
            101.2, 102.3, 101.6, 101.9, 100.7, 101.0,
        ])
        candles = chart_renderer._display_candles(source_candles)
        upper_wicks = [round(item["high"] - max(item["open"], item["close"]), 4) for item in candles]
        lower_wicks = [round(min(item["open"], item["close"]) - item["low"], 4) for item in candles]

        self.assertGreaterEqual(len(set(upper_wicks)), 7)
        self.assertGreaterEqual(len(set(lower_wicks)), 7)
        self.assertTrue(any(upper != lower for upper, lower in zip(upper_wicks, lower_wicks)))
        for index, (source, display) in enumerate(zip(source_candles, candles)):
            self.assertEqual(display, source)
            if index:
                self.assertAlmostEqual(display["open"], candles[index - 1]["close"], places=4)
            self.assertLessEqual(display["low"], min(display["open"], display["close"]))
            self.assertGreaterEqual(display["high"], max(display["open"], display["close"]))

    def test_all_supported_indicator_scenarios_render_in_chinese_and_english(self):
        indicator_kinds = {
            "rsi": "oscillator", "kdj": "oscillator", "macd": "oscillator",
            "bollinger": "overlay", "moving_average": "overlay",
            "atr": "oscillator", "obv": "oscillator", "ict": "price_structure",
        }
        lesson_goals = (
            "overview", "state_a", "state_b", "components", "setup", "worked_example",
        )
        known_chinese_fallbacks = (
            "true range and average", "risk distance context", "upper middle lower",
            "touch and confirmation", "line and price", "fast and slow lines",
            "cross and retest", "three lines", "cross with price confirmation",
            "price and obv", "divergence check", "moving average", "bollinger",
        )
        expected_chinese_names = {
            "rsi": "RSI", "kdj": "KDJ", "macd": "MACD", "bollinger": "布林带",
            "moving_average": "移动平均线", "atr": "ATR", "obv": "OBV", "ict": "ICT结构",
        }

        with tempfile.TemporaryDirectory() as directory:
            for indicator_id, indicator_kind in indicator_kinds.items():
                for lesson_goal in lesson_goals:
                    for language in ("zh-CN", "en-US"):
                        with self.subTest(
                            indicator_id=indicator_id,
                            lesson_goal=lesson_goal,
                            language=language,
                        ):
                            result = render_chart({
                                "page_no": 40,
                                "visual_type": "market_chart",
                                "visual_focus": indicator_id,
                                "required_elements": [indicator_id],
                                "annotations": [],
                                "teaching_spec": {
                                    "indicator_id": indicator_id,
                                    "indicator_kind": indicator_kind,
                                    "lesson_goal": lesson_goal,
                                },
                            }, Path(directory) / f"{indicator_id}-{lesson_goal}-{language}.png",
                                language=language)

                            labels = " | ".join(result["rendered_labels"])
                            self.assertFalse(result["label_overlap"])
                            self.assertTrue(labels)
                            layout = result["chart_layout"]
                            for bounds_key in (
                                "title_bounds", "legend_bounds",
                                "y_axis_label_bounds", "annotation_bounds",
                                "caption_bounds",
                            ):
                                for item in layout[bounds_key]:
                                    self.assertGreaterEqual(
                                        item["bounds"][0], layout["label_left"],
                                    )
                                    self.assertLessEqual(
                                        item["bounds"][2], layout["label_right"],
                                    )
                            if language == "zh-CN":
                                lowered = labels.lower()
                                self.assertIn(expected_chinese_names[indicator_id], labels)
                                for fallback in known_chinese_fallbacks:
                                    self.assertNotIn(fallback, lowered)
                                self.assertNotIn("_", labels)

        with tempfile.TemporaryDirectory() as directory:
            moving_zh = render_chart({
                "page_no": 41, "visual_type": "market_chart", "visual_focus": "moving average",
                "required_elements": ["moving average"], "annotations": [],
                "teaching_spec": {
                    "indicator_id": "moving_average", "indicator_kind": "overlay",
                    "lesson_goal": "overview",
                },
            }, Path(directory) / "moving-average-zh.png", language="zh-CN")
        self.assertIn("20周期均线", moving_zh["rendered_labels"])
        self.assertIn("50周期均线", moving_zh["rendered_labels"])

    def test_english_bearish_ict_uses_fallback_placement_before_overlap_error(self):
        with tempfile.TemporaryDirectory() as directory:
            result = render_chart({
                "page_no": 42,
                "visual_type": "market_chart",
                "visual_focus": "ICT bearish order block",
                "required_elements": ["ICT"],
                "annotations": [],
                "teaching_spec": {
                    "indicator_id": "ict",
                    "indicator_kind": "price_structure",
                    "lesson_goal": "state_b",
                },
            }, Path(directory) / "ict-bearish-en.png", language="en-US")

            self.assertFalse(result["label_overlap"])
            self.assertGreaterEqual(len(result["annotation_bounds"]), 5)
            self.assertTrue(all(item["text_centered"] for item in result["annotation_bounds"]))
            self.assertTrue(any(
                item.get("horizontal_offset", 0) != 0 or item.get("font_size", 12) < 12
                for item in result["annotation_bounds"]
            ))

    def test_actual_rsi_and_macd_render_use_catmull_rom_and_lanczos(self):
        original_resize = Image.Image.resize
        resize_filters = []

        def tracked_resize(image, *args, **kwargs):
            resize_filters.append(kwargs.get("resample", args[1] if len(args) > 1 else None))
            return original_resize(image, *args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, patch(
            "app.photo.chart_renderer._catmull_rom_points",
            wraps=chart_renderer._catmull_rom_points,
        ) as interpolate, patch.object(Image.Image, "resize", new=tracked_resize):
            for indicator_id in ("rsi", "macd"):
                render_chart({
                    "page_no": 43,
                    "visual_type": "market_chart",
                    "visual_focus": indicator_id,
                    "required_elements": [indicator_id],
                    "annotations": [],
                    "teaching_spec": {
                        "indicator_id": indicator_id,
                        "indicator_kind": "oscillator",
                        "lesson_goal": "state_a",
                    },
                }, Path(directory) / f"instrumented-{indicator_id}.png", language="en-US")

            self.assertGreaterEqual(interpolate.call_count, 4)
            self.assertGreaterEqual(resize_filters.count(Image.Resampling.LANCZOS), 4)

    def test_content_candles_match_cover_width_and_reduced_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            result = render_chart({
                "page_no": 44,
                "visual_type": "market_chart",
                "visual_focus": "MACD",
                "required_elements": ["MACD"],
                "annotations": [],
                "teaching_spec": {
                    "indicator_id": "macd",
                    "indicator_kind": "oscillator",
                    "lesson_goal": "state_a",
                },
            }, Path(directory) / "pitch.png")

        self.assertEqual(result["ohlc_count"], 68)
        self.assertAlmostEqual(result["chart_layout"]["candle_body_width"], 10.0)
        self.assertAlmostEqual(result["chart_layout"]["candle_body_width"] * 1080 / 900, 12.0)
        rendered_gap = (
            result["chart_layout"]["candle_pitch"]
            - result["chart_layout"]["candle_body_width"]
        ) * 1080 / 900
        self.assertAlmostEqual(rendered_gap, 1080 / 68 - 12, places=3)
        self.assertEqual(chart_renderer._plot_box(900, 48, 275), (0, 48, 900, 275))


if __name__ == "__main__":
    unittest.main()
