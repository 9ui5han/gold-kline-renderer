import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.photo.chart_renderer import render_chart
from app.photo.page_renderer import (
    _draw_english_header, _draw_header, _draw_summary, _wrapped_lines, render_page,
)
from app.photo.validator import validate_post


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _non_white_ratio(path: Path) -> float:
    with Image.open(path).convert("RGB") as image:
        pixels = list(image.getdata())
    non_white = sum(1 for pixel in pixels if pixel != (255, 255, 255))
    return non_white / len(pixels)


def _non_white_count(path: Path, box: tuple[int, int, int, int]) -> int:
    with Image.open(path).convert("RGB") as image:
        pixels = list(image.crop(box).getdata())
    return sum(1 for pixel in pixels if pixel != (255, 255, 255))


class PhotoTemplateRendererTests(unittest.TestCase):
    def test_page_layout_reports_content_regions_without_decorative_left_bar(self):
        """Removing the page ornament must not remove the measurable content contract."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "page.png"
            result = render_page({
                "page_no": 1, "page_role": "definition",
                "title": "什么是RSI？",
                "body": "RSI用于观察近期价格上涨和下跌力量的相对强弱。",
                "key_message": "", "visual_type": "none",
                "required_elements": [],
                "risk_note": "教学示意图｜不代表实时行情",
            }, None, [], output, 1080, 1080)

            self.assertFalse(result["decorative_left_bar"])
            self.assertFalse(result["layout_overlap"])
            self.assertEqual(
                set(result["layout_regions"]),
                {"header", "title", "body", "footer"},
            )
            for bounds in result["layout_regions"].values():
                self.assertEqual(len(bounds), 4)
                self.assertLess(bounds[0], bounds[2])
                self.assertLess(bounds[1], bounds[3])

    def test_overlong_english_cover_raises_page_layout_overflow(self):
        """Text beyond the approved wrapping and size limits must not be clipped."""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "overflow.png"
            with self.assertRaisesRegex(ValueError, "PAGE_1_LAYOUT_OVERFLOW"):
                render_page({
                    "page_no": 1, "page_role": "cover",
                    "title": "A practical introduction to RSI for new traders learning how momentum signals interact with price structure",
                    "body": "This deliberately long explanation keeps adding words so that it exceeds the approved cover copy area after the renderer has wrapped the text and applied its approved English font sizing limits safely.",
                    "key_message": "", "visual_type": "cover_illustration",
                    "required_elements": [],
                    "risk_note": "Educational illustration | Not real-time market data",
                }, None, [], output, 1080, 1080, language="en")

    def test_english_release_typography_uses_montserrat_centered_highlight_and_shadow(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "english.png"
            result = render_page({
                "page_no": 1,
                "page_role": "cover",
                "title": "What is RSI?",
                "body": "Learn how the Relative Strength Index works.",
                "key_message": "Understand RSI before using it.",
                "visual_type": "cover_illustration",
                "visual_focus": "RSI indicator",
                "required_elements": ["RSI", "price chart"],
                "risk_note": "Educational illustration | Not real-time market data",
            }, None, [], output, 1080, 1080, language="en")

            self.assertEqual(result["render_language"], "en")
            self.assertEqual(result["rendered_title"], "WHAT IS RSI?")
            self.assertEqual(result["typography_metrics"]["font_family"], "Montserrat")
            self.assertEqual(result["typography_metrics"]["title_weight"], 800)
            self.assertEqual(result["typography_metrics"]["body_weight"], 400)
            self.assertEqual(result["typography_metrics"]["alignment"], "center")
            self.assertEqual(result["typography_metrics"]["highlight_words"], ["RSI?"])
            self.assertEqual(result["typography_metrics"]["title_shadow"], {
                "offset_x": 2, "offset_y": 3, "blur": 3, "opacity": 0.16,
            })
            self.assertTrue(result["typography_metrics"]["body_shadow"])
            self.assertTrue(result["copy_contract_valid"])
            self.assertGreater(_non_white_ratio(output), 0.055)

    def test_english_content_header_adapts_to_long_teaching_copy(self):
        image = Image.new("RGB", (1024, 1024), "white")
        draw = ImageDraw.Draw(image)
        _, overflow, _, metrics = _draw_english_header(
            draw,
            {
                "title": "What Is a Propulsion Block?",
                "body": (
                    "A propulsion block is a candlestick that trades into an order "
                    "block, after which price moves away from that area. It helps show "
                    "how price can react after revisiting an order block in this "
                    "educational example. Review the candle, the order block, and the "
                    "subsequent move as separate observations before drawing a conclusion. "
                    "This is not a historical market record."
                ),
            },
            1024,
            cover=False,
        )
        self.assertFalse(overflow)
        self.assertLessEqual(metrics["body_line_count"], 6)

    def test_chinese_header_uses_compact_readable_typography(self):
        image = Image.new("RGB", (1080, 1080), "white")
        draw = ImageDraw.Draw(image)
        bottom, overflow, box, metrics = _draw_header(
            draw,
            "复习总结",
            "RSI工具为技术分析带来便捷，配合价格表现可有效判断市场状态。",
            1080,
            False,
        )
        self.assertFalse(overflow)
        self.assertLessEqual(metrics["title_size"], 48)
        self.assertLessEqual(metrics["body_size"], 29)
        self.assertLessEqual(metrics["body_line_width"], 820)
        self.assertGreater(bottom, box[1])

    def test_chinese_copy_wraps_by_rendered_pixel_width(self):
        image = Image.new("RGB", (1080, 1080), "white")
        draw = ImageDraw.Draw(image)
        font = __import__("app.photo.chart_renderer", fromlist=["_font"])._font(35)
        lines = _wrapped_lines(
            "RSI是相对强弱指标，数值在0到100之间，用来观察近期价格上涨与下跌动能的变化。",
            936,
        )
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(draw.textbbox((0, 0), line, font=font)[2] <= 936 for line in lines))

    def test_chinese_page_copy_is_preserved_instead_of_replaced(self):
        cases = [
            ("什么是RSI？", "RSI用于观察近期价格上涨和下跌力量的相对强弱。"),
            ("如何理解超买？", "RSI进入高位区域后，还要继续观察价格是否转弱。"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for index, (title, body) in enumerate(cases, start=1):
                output = Path(directory) / f"language_{index}.png"
                result = render_page({
                    "page_no": index, "page_role": "definition",
                    "title": title, "body": body, "key_message": "",
                    "visual_type": "none", "required_elements": [],
                    "risk_note": "教学示意图｜不代表实时行情",
                }, None, [], output, 1080, 1080)
                self.assertEqual(result["render_language"], "zh-CN")
                self.assertEqual(result["rendered_title"], title)
                self.assertEqual(result["rendered_body"], body)
                self.assertEqual(result["rendered_disclaimer"], "教学示意图｜不代表实时行情")
                self.assertTrue(result["chinese_contract_valid"])

    def test_missing_page_copy_is_rejected_instead_of_using_shared_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "PAGE_2_CHINESE_COPY_REQUIRED"):
                render_page({
                    "page_no": 2, "page_role": "definition",
                    "title": "", "body": "", "key_message": "",
                    "visual_type": "none", "required_elements": [],
                    "risk_note": "教学示意图｜不代表实时行情",
                }, None, [], Path(directory) / "missing.png", 1080, 1080)

    def test_qa_rejects_non_chinese_or_overlapping_layout_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (1080, 1080), "white").save(image_path)
            plan = {"content_type": "knowledge", "pages": [{
                "page_no": 1, "visual_type": "indicator_panel",
            }]}
            rendered = {"photo_job_id": "photo-test", "images": [{
                "page_no": 1, "path": str(image_path), "width": 1080, "height": 1080,
                "layout_overflow": False, "risk_note_present": True,
                "render_language": "en", "layout_overlap": True,
                "chinese_contract_valid": False,
                "disclaimer_count": 2,
            }]}
            qa = validate_post(plan, rendered)
            codes = {item["code"] for item in qa["errors"]}
            self.assertIn("NON_CHINESE_RENDER", codes)
            self.assertIn("LAYOUT_OVERLAP", codes)
            self.assertIn("DISCLAIMER_COUNT_INVALID", codes)

    def test_qa_rejects_missing_chinese_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (1080, 1080), "white").save(image_path)
            plan = {"content_type": "knowledge", "pages": [{
                "page_no": 1, "visual_type": "summary_card",
            }]}
            rendered = {"photo_job_id": "photo-test", "images": [{
                "page_no": 1, "path": str(image_path), "width": 1080, "height": 1080,
                "layout_overflow": False, "risk_note_present": True,
                "render_language": "zh-CN", "layout_overlap": False,
                "disclaimer_count": 1,
            }]}
            qa = validate_post(plan, rendered)
            self.assertFalse(qa["passed"])
            self.assertIn("NON_CHINESE_RENDER", {item["code"] for item in qa["errors"]})

    def test_qa_rejects_layout_overflow_and_missing_mandatory_regions(self):
        """QA must reject renderer layout failures even when copy validation succeeds."""
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (1080, 1080), "white").save(image_path)
            plan = {"content_type": "knowledge", "pages": [{
                "page_no": 1, "page_role": "definition", "visual_type": "none",
            }]}
            rendered = {"photo_job_id": "photo-test", "images": [{
                "page_no": 1, "path": str(image_path), "width": 1080, "height": 1080,
                "layout_overflow": True, "risk_note_present": True,
                "render_language": "zh-CN", "layout_overlap": False,
                "chinese_contract_valid": True, "copy_contract_valid": True,
                "disclaimer_count": 1, "layout_regions": {"header": [1, 1, 2, 2]},
            }]}
            qa = validate_post(plan, rendered)
            codes = {item["code"] for item in qa["errors"]}
            self.assertIn("LAYOUT_OVERFLOW", codes)
            self.assertIn("LAYOUT_REGIONS_MISSING", codes)

    def test_qa_rejects_empty_checklist_and_empty_cover_topic_visual(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for page_no in (1, 2):
                path = Path(directory) / f"page_{page_no}.png"
                Image.new("RGB", (1080, 1080), "white").save(path)
                paths.append(path)
            plan = {"content_type": "knowledge", "pages": [
                {"page_no": 1, "page_role": "cover", "visual_type": "cover_illustration"},
                {"page_no": 2, "page_role": "checklist", "visual_type": "checklist"},
            ]}
            images = []
            for page_no, path in enumerate(paths, start=1):
                images.append({
                    "page_no": page_no, "path": str(path), "width": 1080, "height": 1080,
                    "layout_overflow": False, "risk_note_present": True,
                    "render_language": "zh-CN", "layout_overlap": False,
                    "chinese_contract_valid": True, "disclaimer_count": 1,
                    "topic_visual_present": False, "checklist_present": False,
                    "checklist_item_count": 0,
                })
            qa = validate_post(plan, {"photo_job_id": "photo-test", "images": images})
            codes = {item["code"] for item in qa["errors"]}
            self.assertIn("COVER_TOPIC_VISUAL_MISSING", codes)
            self.assertIn("CHECKLIST_CONTENT_MISSING", codes)

    def test_rsi_visual_types_render_distinct_chinese_charts(self):
        cases = [
            ("indicator_panel", "RSI scale from 0 to 100", ["RSI", "30", "70"]),
            ("zone_diagram", "RSI above 70 overbought zone", ["Overbought", "70"]),
            ("zone_diagram", "RSI below 30 oversold zone", ["Oversold", "30"]),
            ("checklist", "Common RSI mistakes", ["Confirm trend", "Use context"]),
            ("candlestick_demo", "Price and RSI rebound example", ["Price", "RSI"]),
        ]
        with tempfile.TemporaryDirectory() as directory:
            outputs = []
            for page_no, (visual_type, visual_focus, elements) in enumerate(cases, start=1):
                path = Path(directory) / f"chart_{page_no}.png"
                result = render_chart({
                    "page_no": page_no,
                    "visual_type": visual_type,
                    "visual_focus": visual_focus,
                    "required_elements": elements,
                    "annotations": [],
                }, path)
                outputs.append(path)
                self.assertEqual(result["render_language"], "zh-CN")
                self.assertFalse(result["disclaimer_drawn"])
                self.assertEqual(result["template_key"], [
                    "rsi_range_overview", "rsi_overbought_reversal",
                    "rsi_oversold_recovery", "checklist", "rsi_worked_example",
                ][page_no - 1])
            self.assertEqual(len({_digest(path) for path in outputs}), len(outputs))

    def test_page_roles_use_distinct_layouts_and_one_chinese_disclaimer(self):
        pages = [
            ("cover", "cover_illustration"),
            ("definition", "indicator_panel"),
            ("mistakes", "checklist"),
            ("example", "candlestick_demo"),
            ("summary", "summary_card"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            chart_path = Path(directory) / "chart.png"
            render_chart({
                "page_no": 2,
                "visual_type": "indicator_panel",
                "visual_focus": "RSI scale",
                "required_elements": ["RSI", "30", "70"],
                "annotations": [],
            }, chart_path)
            results = []
            for page_no, (page_role, visual_type) in enumerate(pages, start=1):
                output = Path(directory) / f"page_{page_no}.png"
                page = {
                    "page_no": page_no,
                    "page_role": page_role,
                    "title": f"RSI第{page_no}课",
                    "body": "结合价格结构与确认信号理解RSI，不把单一指标作为买卖依据。",
                    "key_message": "必须结合市场背景。",
                    "visual_type": visual_type,
                    "required_elements": ["RSI", "Context", "Confirmation"],
                    "risk_note": "教学示意图｜不代表实时行情",
                }
                chart = None if page_role in {"cover", "summary"} else {
                    "asset_path": str(chart_path), "asset_type": visual_type,
                }
                result = render_page(page, chart, [], output, 1080, 1080)
                results.append(result)
                self.assertEqual(result["render_language"], "zh-CN")
                self.assertEqual(result["disclaimer_count"], 1)
                self.assertEqual(result["rendered_disclaimer"], "教学示意图｜不代表实时行情")
                self.assertGreater(_non_white_ratio(output), 0.035)
            self.assertEqual(
                [item["layout_template"] for item in results],
                ["cover", "standard", "checklist", "example", "summary"],
            )

    def test_cover_builds_topic_related_indicator_visual_without_character(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cover.png"
            result = render_page({
                "page_no": 1,
                "page_role": "cover",
                "title": "什么是RSI指标？",
                "body": "零基础使用指南",
                "key_message": "认识RSI。",
                "visual_type": "cover_illustration",
                "visual_focus": "RSI指标与价格走势",
                "required_elements": ["K线图", "RSI曲线", "30与70区间"],
                "risk_note": "教学示意图｜不代表实时行情",
            }, None, [], output, 1080, 1080)
            self.assertEqual(result["cover_visual_type"], "indicator_rsi")
            self.assertEqual(result["cover_focus_label"], "RSI")
            self.assertGreaterEqual(result["typography_metrics"]["focus_size"], 80)
            self.assertEqual(result["typography_metrics"]["title_weight"], "regular")
            self.assertEqual(result["cover_candle_count"], 68)
            self.assertAlmostEqual(
                result["cover_candle_body_width"], 12.0, delta=.2,
            )
            self.assertAlmostEqual(result["cover_candle_gap_ratio"], .2444, delta=.01)
            self.assertNotIn("cover_chart_edges", result)
            self.assertGreater(_non_white_count(output, (0, 350, 24, 715)), 0)
            self.assertGreater(_non_white_count(output, (1056, 350, 1080, 715)), 0)
            self.assertGreater(_non_white_count(output, (44, 720, 180, 758)), 0)
            self.assertGreaterEqual(result["cover_indicator_point_count"], 450)
            self.assertEqual(result["cover_indicator_supersample"], 8)
            self.assertTrue(result["topic_visual_present"])
            self.assertFalse(result["character_present"])
            self.assertGreater(_non_white_ratio(output), 0.055)

    def test_english_cover_fits_valid_long_copy_without_layout_overflow(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "english-cover.png"
            result = render_page({
                "page_no": 1,
                "page_role": "cover",
                "title": "RSI Basics Illustrated",
                "body": (
                    "This set of slides only covers the basic way to read RSI. "
                    "It is a common technical indicator used to observe the pace "
                    "of price changes, but it cannot replace a complete judgment "
                    "on its own."
                ),
                "key_message": "Learn RSI first, then learn how to read its range states",
                "visual_type": "cover_illustration",
                "visual_focus": "Teaching cover with a mobile chart interface and RSI text",
                "required_elements": ["RSI title text", "Simplified price chart background"],
                "risk_note": "Teaching illustration | Not real-time market data",
            }, None, [], output, 1080, 1080, language="en")

            self.assertTrue(output.is_file())
            self.assertFalse(result["layout_overflow"])
            self.assertFalse(result["layout_overlap"])
            self.assertEqual(result["render_language"], "en")
            self.assertEqual(result["typography_metrics"]["body_line_count"], 4)

    def test_english_content_page_fits_valid_four_line_body(self):
        with tempfile.TemporaryDirectory() as directory:
            chart_path = Path(directory) / "chart.png"
            render_chart({
                "page_no": 2,
                "visual_type": "indicator_panel",
                "visual_focus": "RSI range overview",
                "required_elements": ["RSI"],
                "annotations": [],
                "teaching_spec": {
                    "indicator_id": "rsi",
                    "indicator_kind": "oscillator",
                    "lesson_goal": "overview",
                },
            }, chart_path, {"topic_text": "RSI tutorial"}, language="en")
            output = Path(directory) / "english-content.png"
            result = render_page({
                "page_no": 2,
                "page_role": "definition",
                "title": "What Is RSI",
                "body": (
                    "RSI is a technical indicator. In simple terms, it turns the "
                    "changes in price strength over a period into a line, helping "
                    "readers observe roughly which range the market is currently in."
                ),
                "key_message": "RSI turns price strength changes into a readable line",
                "visual_type": "indicator_panel",
                "visual_focus": "Upper price area and lower RSI panel",
                "required_elements": ["Upper price area", "Lower RSI panel", "RSI line"],
                "risk_note": "Teaching illustration | Not real-time market data",
            }, {"asset_path": str(chart_path)}, [], output, 1080, 1080, language="en")

            self.assertFalse(result["layout_overflow"])
            self.assertFalse(result["layout_overlap"])
            self.assertEqual(result["typography_metrics"]["body_line_count"], 4)

    def test_english_content_page_reduces_body_font_before_overflow(self):
        image = Image.new("RGB", (1080, 1080), "white")
        draw = ImageDraw.Draw(image)
        _, overflow, _, metrics = _draw_english_header(
            draw,
            {
                "title": "Teaching Example: How to Read a Segment of RSI",
                "body": (
                    "This example does not judge whether price will rise or fall. "
                    "It only demonstrates the reading order: first match the price "
                    "area with the indicator area, then see which range the RSI line "
                    "enters, and finally check whether it keeps staying there or leaves quickly."
                ),
                "visual_focus": "RSI teaching example",
            },
            1080,
            cover=False,
        )

        self.assertFalse(overflow)
        self.assertEqual(metrics["body_line_count"], 4)
        self.assertEqual(metrics["body_size"], 26)

    def test_english_summary_supports_four_schema_valid_points(self):
        image = Image.new("RGB", (1080, 1080), "white")
        draw = ImageDraw.Draw(image)
        box, overflow = _draw_summary(
            draw,
            {
                "required_elements": [
                    "Point 1: read the range first",
                    "Point 2: then read changes",
                    "Point 3: combine with price",
                    "Risk reminder",
                ],
            },
            1080,
            985,
            language="en",
        )

        self.assertFalse(overflow)
        self.assertIsNotNone(box)

    def test_english_summary_starts_below_a_four_line_header(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "long-summary.png"
            result = render_page({
                "page_no": 7,
                "page_role": "summary",
                "title": "Reminders for Learning RSI Ranges",
                "body": (
                    "RSI is better used as an observation tool and should not be used alone "
                    "as a basis for decisions. When reading range states, try to understand "
                    "them together with state persistence, the shift process, and the use case."
                ),
                "key_message": "Treat RSI as an observation tool, not a standalone source of conclusions.",
                "visual_type": "summary_card",
                "visual_focus": "A summary card listing key learning points and risk reminders",
                "required_elements": [
                    "Key learning points",
                    "State observation tips",
                    "Risk reminder",
                ],
                "risk_note": "Teaching diagram | Does not represent live market conditions",
            }, None, [], output, 1080, 1080, language="en")

            self.assertEqual(result["layout_template"], "summary")
            self.assertFalse(result["layout_overlap"])
            self.assertEqual(result["typography_metrics"]["body_line_count"], 4)

    def test_content_chart_fills_the_page_from_left_edge_to_right_edge(self):
        with tempfile.TemporaryDirectory() as directory:
            chart_path = Path(directory) / "chart.png"
            render_chart({
                "page_no": 2,
                "visual_type": "indicator_panel",
                "visual_focus": "RSI scale",
                "required_elements": ["RSI", "30", "70"],
                "annotations": [],
            }, chart_path)
            output = Path(directory) / "page.png"
            result = render_page({
                "page_no": 2,
                "page_role": "definition",
                "title": "RSI区间",
                "body": "结合价格表现理解指标区间。",
                "key_message": "先看价格，再看指标。",
                "visual_type": "indicator_panel",
                "required_elements": ["RSI", "30", "70"],
                "risk_note": "教学示意图｜不代表实时行情",
            }, {"asset_path": str(chart_path)}, [], output, 1080, 1080)

            self.assertEqual(result["layout_regions"]["chart"][0], 0)
            self.assertEqual(result["layout_regions"]["chart"][2], 1080)

    def test_cover_composites_registered_svg_illustration(self):
        project_root = Path(__file__).resolve().parents[1]
        svg_path = project_root / "assets" / "photo" / "illustrations" / "undraw" / "undraw_predictive-analytics_6gsu.svg"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cover-svg.png"
            result = render_page({
                "page_no": 1, "page_role": "cover",
                "title": "RSI指标", "body": "零基础使用指南", "key_message": "认识RSI",
                "visual_type": "cover_illustration", "visual_focus": "RSI指标",
                "required_elements": ["RSI曲线"],
                "risk_note": "教学示意图｜不代表实时行情",
            }, None, [{
                "asset_key": "undraw_predictive_analytics",
                "asset_type": "background",
                "asset_path": str(svg_path),
                "source": "undraw",
                "license": "UNDRAW-2026",
            }], output, 1080, 1080)
            self.assertTrue(result["cover_asset_present"])
            self.assertEqual(result["cover_asset_key"], "undraw_predictive_analytics")
            self.assertEqual(result["cover_asset_opacity"], 1.0)

    def test_checklist_renders_from_page_elements_without_chart_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "checklist.png"
            result = render_page({
                "page_no": 5,
                "page_role": "checklist",
                "title": "使用RSI的检查清单",
                "body": "按顺序完成以下检查。",
                "key_message": "不要只看一个数值。",
                "visual_type": "checklist",
                "visual_focus": "RSI使用步骤",
                "required_elements": ["确认30与70区间", "观察价格是否确认", "检查趋势背景"],
                "risk_note": "教学示意图｜不代表实时行情",
            }, None, [], output, 1080, 1080)
            self.assertTrue(result["checklist_present"])
            self.assertEqual(result["checklist_item_count"], 3)
            self.assertFalse(result["chart_present"])
            self.assertGreater(_non_white_ratio(output), 0.035)

    def test_required_financial_chart_missing_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "PAGE_2_CHART_REQUIRED"):
                render_page({
                    "page_no": 2,
                    "page_role": "definition",
                    "title": "RSI是什么？",
                    "body": "RSI用于衡量价格强弱。",
                    "key_message": "观察强弱。",
                    "visual_type": "indicator_panel",
                    "required_elements": ["RSI曲线"],
                    "risk_note": "教学示意图｜不代表实时行情",
                }, None, [], Path(directory) / "missing-chart.png", 1080, 1080)

    def test_visible_page_number_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "summary.png"
            result = render_page({
                "page_no": 7,
                "page_role": "summary",
                "title": "复习总结",
                "body": "结合价格确认指标信号。",
                "key_message": "完成复习。",
                "visual_type": "summary_card",
                "required_elements": ["指标状态", "价格确认"],
                "risk_note": "教学示意图｜不代表实时行情",
            }, None, [], output, 1080, 1080)
            self.assertFalse(result["visible_page_number"])

    def test_character_is_confined_to_reserved_cover_area(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "cover.png"
            result = render_page({
                "page_no": 1,
                "page_role": "cover",
                "title": "RSI零基础教学",
                "body": "理解价格动能，同时不要把单一指标直接当作交易信号。",
                "key_message": "结合价格背景理解RSI。",
                "visual_type": "cover_illustration",
                "required_elements": ["RSI", "Price chart"],
                "risk_note": "教学示意图｜不代表实时行情",
            }, None, [{
                "asset_key": "teacher_front",
                "asset_path": str(Path(__file__).resolve().parents[1] / "assets" / "photo" / "characters" / "teacher_front_premium.png"),
            }], output, 1080, 1080)
            self.assertTrue(result["character_present"])
            self.assertTrue(result["character_in_safe_area"])
            self.assertFalse(result["layout_overlap"])
            self.assertIsNotNone(result["character_box"])
            self.assertIsNotNone(result["content_box"])
            self.assertFalse(result["character_box_intersects_content"])


if __name__ == "__main__":
    unittest.main()
