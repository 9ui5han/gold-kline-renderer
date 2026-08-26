import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from app.photo.chart_renderer import render_chart
from app.photo.page_renderer import render_page
from app.photo.validator import validate_post


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _non_white_ratio(path: Path) -> float:
    with Image.open(path).convert("RGB") as image:
        pixels = list(image.getdata())
    non_white = sum(1 for pixel in pixels if pixel != (255, 255, 255))
    return non_white / len(pixels)


class PhotoTemplateRendererTests(unittest.TestCase):
    def test_non_english_text_is_replaced_by_deterministic_english_copy(self):
        cases = [
            ("¿Qué es RSI?", "Что такое RSI?"),
            ("什么是RSI？", "RSI是什么？"),
            ("Définition du RSI", "Utilisez le RSI avec prudence."),
            ("Que es RSI?", "Aprenda como usar RSI en el mercado."),
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
                self.assertEqual(result["render_language"], "en")
                self.assertEqual(result["rendered_title"], "RSI Step-by-Step Guide")
                self.assertEqual(
                    result["rendered_body"],
                    "Understand RSI with price context and confirmation.",
                )
                self.assertTrue(result["english_contract_valid"])

    def test_qa_rejects_non_english_or_overlapping_layout_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (1080, 1080), "white").save(image_path)
            plan = {"content_type": "knowledge", "pages": [{
                "page_no": 1, "visual_type": "indicator_panel",
            }]}
            rendered = {"photo_job_id": "photo-test", "images": [{
                "page_no": 1, "path": str(image_path), "width": 1080, "height": 1080,
                "layout_overflow": False, "risk_note_present": True,
                "render_language": "zh-CN", "layout_overlap": True,
                "english_contract_valid": False,
                "disclaimer_count": 2,
            }]}
            qa = validate_post(plan, rendered)
            codes = {item["code"] for item in qa["errors"]}
            self.assertIn("NON_ENGLISH_RENDER", codes)
            self.assertIn("LAYOUT_OVERLAP", codes)
            self.assertIn("DISCLAIMER_COUNT_INVALID", codes)

    def test_qa_rejects_missing_english_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "page.png"
            Image.new("RGB", (1080, 1080), "white").save(image_path)
            plan = {"content_type": "knowledge", "pages": [{
                "page_no": 1, "visual_type": "summary_card",
            }]}
            rendered = {"photo_job_id": "photo-test", "images": [{
                "page_no": 1, "path": str(image_path), "width": 1080, "height": 1080,
                "layout_overflow": False, "risk_note_present": True,
                "render_language": "en", "layout_overlap": False,
                "disclaimer_count": 1,
            }]}
            qa = validate_post(plan, rendered)
            self.assertFalse(qa["passed"])
            self.assertIn("NON_ENGLISH_RENDER", {item["code"] for item in qa["errors"]})

    def test_rsi_visual_types_render_distinct_english_charts(self):
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
                self.assertEqual(result["render_language"], "en")
                self.assertFalse(result["disclaimer_drawn"])
                self.assertEqual(result["template_key"], [
                    "rsi_panel", "rsi_overbought", "rsi_oversold",
                    "checklist", "price_rsi_example",
                ][page_no - 1])
            self.assertEqual(len({_digest(path) for path in outputs}), len(outputs))

    def test_page_roles_use_distinct_layouts_and_one_english_disclaimer(self):
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
                    "title": f"RSI Lesson {page_no}",
                    "body": "Use RSI with price structure and confirmation, not as a standalone signal.",
                    "key_message": "Context matters.",
                    "visual_type": visual_type,
                    "required_elements": ["RSI", "Context", "Confirmation"],
                    "risk_note": "教学示意图｜不代表实时行情",
                }
                chart = None if page_role in {"cover", "summary"} else {
                    "asset_path": str(chart_path), "asset_type": visual_type,
                }
                result = render_page(page, chart, [], output, 1080, 1080)
                results.append(result)
                self.assertEqual(result["render_language"], "en")
                self.assertEqual(result["disclaimer_count"], 1)
                self.assertNotIn("教学", result["rendered_disclaimer"])
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
                "title": "RSI Step-by-Step Guide",
                "body": "Understand momentum without treating one indicator as a trade signal.",
                "key_message": "Read RSI in context.",
                "visual_type": "cover_illustration",
                "required_elements": ["RSI", "Price chart"],
                "risk_note": "教学示意图｜不代表实时行情",
            }, None, [{"asset_key": "teacher_front"}], output, 1080, 1080)
            self.assertTrue(result["character_present"])
            self.assertTrue(result["character_in_safe_area"])
            self.assertFalse(result["layout_overlap"])
            self.assertIsNotNone(result["character_box"])
            self.assertIsNotNone(result["content_box"])
            self.assertFalse(result["character_box_intersects_content"])


if __name__ == "__main__":
    unittest.main()
