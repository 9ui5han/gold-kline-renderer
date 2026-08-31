import copy
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import main
from test_propulsion_market_render import market_page


URL = "/v1/carousel/charts/render"
AUTH = {"Authorization": "Bearer carousel-local-test"}


def request_payload():
    return {
        "schema_version": "photo-chart-request-v1",
        "content_type": "educational_reconstruction",
        "language": "en",
        "pages": [{"page_no": 2, "visual_type": "market_chart"}],
        "route_payload": {
            "schema_version": "carousel-route-v2",
            "analysis_mode": "educational_reconstruction",
            "market": "XAUUSD",
            "timeframe": "1h",
            "input_meta": {"data_timezone": "not_provided"},
            "analysis_pages": [market_page(2)],
        },
    }


class CarouselRoutesTests(unittest.TestCase):
    def setUp(self):
        self.token = patch.object(main, "TOKEN", "carousel-local-test")
        self.token.start()
        self.addCleanup(self.token.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)

    def test_new_route_requires_existing_backend_token(self):
        for headers in ({}, {"Authorization": "Bearer wrong"}):
            response = self.client.post(URL, json=request_payload(), headers=headers)
            self.assertEqual(response.status_code, 401, response.text)

    def test_renders_real_png_with_existing_tool04_response_contract(self):
        payload = request_payload()
        original = copy.deepcopy(payload)
        response = self.client.post(URL, json=payload, headers=AUTH)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["schema_version"], "photo-chart-v1")
        self.assertEqual(len(result["assets"]), 1)
        asset = result["assets"][0]
        self.assertEqual(asset["page_no"], 2)
        self.assertEqual(asset["source_type"], "educational_reconstruction")
        self.assertEqual(asset["rendered_candle_count"], 8)
        self.assertEqual(asset["coordinate_map"]["zones"][0]["start_index"], 1)
        self.assertIn("carousel-work", Path(asset["asset_path"]).parts)
        self.assertNotIn("photo-work", Path(asset["asset_path"]).parts)
        self.assertEqual(len(asset["data_fingerprint"]), 64)
        with Image.open(asset["asset_path"]) as image:
            self.assertEqual(image.size, (1080, 720))
            self.assertEqual(image.format, "PNG")
        self.assertEqual(payload, original)

    def test_rejects_legacy_content_types(self):
        for content_type in ("knowledge", "market", "forecast"):
            payload = request_payload()
            payload["content_type"] = content_type
            response = self.client.post(URL, json=payload, headers=AUTH)
            self.assertEqual(response.status_code, 422, response.text)

    def test_rejects_wrong_route_version_and_invalid_coordinates(self):
        payload = request_payload()
        payload["route_payload"]["schema_version"] = "carousel-route-v1"
        response = self.client.post(URL, json=payload, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("MARKET_ROUTE_VERSION_INVALID", response.text)
        payload = request_payload()
        payload["route_payload"]["analysis_pages"][0]["zones"][0]["end_index"] = 999
        response = self.client.post(URL, json=payload, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("MARKET_ZONE_INDEX_OUT_OF_RANGE", response.text)

    def test_missing_configuration_remains_fail_closed(self):
        with patch.object(main, "TOKEN", "change-me"):
            response = self.client.post(URL, json=request_payload(), headers=AUTH)
        self.assertEqual(response.status_code, 503, response.text)

    def test_old_photo_route_behavior_remains_available(self):
        response = self.client.post("/v1/photo/charts/render", json={}, headers={})
        self.assertEqual(response.status_code, 401)
        payload = request_payload()
        payload["content_type"] = "market"
        response = self.client.post("/v1/photo/charts/render", json=payload, headers=AUTH)
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("PHOTO_MARKET_CHART_NOT_IMPLEMENTED", response.text)
