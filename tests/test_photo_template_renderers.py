import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from app.photo.chart_renderer import render_chart
from app.photo.page_renderer import _wrapped_lines, render_page
from app.photo.validator import validate_post


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _non_white_ratio(path: Path) -> float:
    with Image.open(path).convert("RGB") as image:
        pixels = list(image.getdata())
    non_white = sum(1 for pixel in pixels if pixel != (255, 255, 255))
    return non_white / len(pixels)


class PhotoTemplateRendererTests(unittest.TestCase):
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
