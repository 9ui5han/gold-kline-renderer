import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import main


URL = "/v1/carousel/reference/render"
AUTH = {"Authorization": "Bearer carousel-local-test"}


def payload():
    return {
        "schema_version": "reference-carousel-render-v1",
        "language": "zh-CN",
        "pages": [{
            "page_no": 2,
            "layout_spec": {
                "canvas_ratio": "1:1",
                "title_box": {"x": .08, "y": .06, "w": .84, "h": .10},
                "body_box": {"x": .08, "y": .18, "w": .84, "h": .12},
                "visual_box": {"x": .05, "y": .36, "w": .90, "h": .58},
            },
            "visible_elements": [
                {"element_id": "e1", "type": "candlestick_chart", "box": {"x": .05, "y": .36, "w": .90, "h": .58}},
                {"element_id": "e2", "type": "zone", "label_text": "OB", "parent_id": "e1"},
            ],
            "concept_spec": {"concept_id": "order_block", "direction": "bullish", "lesson_intent": "definition", "rule_version": "order-block-v1"},
            "copy": {"title": "Order Block", "body": "A teaching example."},
            "visual_data": {"ohlc": [{"o": 10, "h": 12, "l": 9, "c": 11}], "indicator_series": None},
            "annotations": [{"type": "zone", "label": "OB", "source_element_id": "e2", "start_bar": 0, "end_bar": 0, "price_low": 9, "price_high": 10, "rule_version": "order-block-v1"}],
        }],
    }


class ReferenceCarouselRoutesTests(unittest.TestCase):
    def setUp(self):
        self.token = patch.object(main, "TOKEN", "carousel-local-test")
        self.token.start()
        self.addCleanup(self.token.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_renders_only_reference_authorized_elements(self):
        response = self.client.post(URL, json=payload(), headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        page = response.json()["assets"][0]
        self.assertEqual(page["rendered_element_ids"], ["e1", "e2"])
        self.assertEqual(page["rendered_copy_boxes"], ["title_box", "body_box"])

    def test_rejects_annotation_not_present_in_reference(self):
        request = payload()
        request["pages"][0]["annotations"][0]["source_element_id"] = "missing-pb"
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("ANNOTATION_NOT_IN_REFERENCE:2", response.text)

    def test_rejects_unknown_reference_element(self):
        request = payload()
        request["pages"][0]["visible_elements"].append({"element_id": "e3", "type": "unknown"})
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("REFERENCE_ELEMENT_INVALID:2", response.text)

    def test_line_or_point_not_in_reference_is_not_drawn(self):
        request = payload()
        request["pages"][0]["annotations"] = [{
            "type": "line",
            "label": "trend",
            "source_element_id": "e1",
            "start_bar": 0,
            "end_bar": 0,
            "price_low": 9,
            "price_high": 12,
            "rule_version": "order-block-v1",
        }]
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("ANNOTATION_NOT_IN_REFERENCE:2", response.text)

    def test_layout_box_outside_canvas_is_rejected(self):
        request = payload()
        request["pages"][0]["layout_spec"]["title_box"] = {"x": .9, "y": .06, "w": .2, "h": .1}
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("REFERENCE_ELEMENT_INVALID:2", response.text)

    def test_text_overflow_is_rejected(self):
        request = payload()
        request["pages"][0]["layout_spec"]["title_box"] = {"x": .08, "y": .06, "w": .08, "h": .1}
        request["pages"][0]["copy"]["title"] = "A title that is too wide"
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("LAYOUT_OVERFLOW:2", response.text)

    def test_line_point_and_diagram_are_actually_rendered(self):
        request = payload()
        request["pages"][0]["visual_data"]["ohlc"] = []
        request["pages"][0]["visible_elements"] = [
            {"element_id": "line-1", "type": "line", "box": {"x": .10, "y": .34, "w": .20, "h": .10}},
            {"element_id": "point-1", "type": "point", "box": {"x": .42, "y": .44, "w": .08, "h": .08}},
            {"element_id": "diagram-1", "type": "diagram", "box": {"x": .62, "y": .58, "w": .20, "h": .12}},
        ]
        request["pages"][0]["annotations"] = []
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        asset = response.json()["assets"][0]
        self.assertEqual(asset["rendered_element_ids"], ["line-1", "point-1", "diagram-1"])
        with Image.open(asset["asset_path"]) as image:
            self.assertNotEqual(image.getpixel((216, 421)), (255, 255, 255))
            self.assertNotEqual(image.getpixel((497, 518)), (255, 255, 255))
            self.assertNotEqual(image.getpixel((670, 648)), (255, 255, 255))

    def test_indicator_panel_requires_series(self):
        request = payload()
        request["pages"][0]["visible_elements"].append({
            "element_id": "e3",
            "type": "indicator_panel",
            "box": {"x": .05, "y": .78, "w": .90, "h": .16},
        })
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("INDICATOR_DATA_INSUFFICIENT:2", response.text)

    def test_reference_page_without_grid_or_footer_has_no_default_injection(self):
        request = payload()
        request["pages"][0]["visible_elements"] = []
        request["pages"][0]["visual_data"] = {"ohlc": [], "indicator_series": None}
        request["pages"][0]["annotations"] = []
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        asset = response.json()["assets"][0]
        with Image.open(asset["asset_path"]) as image:
            self.assertTrue(all(image.getpixel((x, 1040)) == (255, 255, 255) for x in (20, 300, 600, 1000)))
        self.assertEqual(asset["rendered_indicator_panels"], [])
        self.assertEqual(asset["rendered_element_ids"], [])

    def test_indicator_panel_is_rendered_only_when_reference_authorizes_it(self):
        request = payload()
        request["pages"][0]["visible_elements"].append({"element_id": "e3", "type": "indicator_panel", "box": {"x": .05, "y": .78, "w": .90, "h": .16}})
        request["pages"][0]["visual_data"]["indicator_series"] = [30, 45, 62, 55]
        response = self.client.post(URL, json=request, headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["assets"][0]["rendered_element_ids"], ["e1", "e2", "e3"])
        self.assertEqual(response.json()["assets"][0]["rendered_indicator_panels"], ["e3"])
