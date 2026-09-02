import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from app import main


AUTH = {"Authorization": "Bearer kline-test-token"}


def kline_payload() -> dict:
    bars = []
    closes = [100, 102, 101, 105, 103, 107, 106, 109, 108, 112, 110, 114, 113, 116, 115, 118, 117, 120, 119, 122]
    for index, close in enumerate(closes):
        open_price = close - 0.8 if index % 2 == 0 else close + 0.8
        bars.append({
            "t": index,
            "o": open_price,
            "h": max(open_price, close) + 1.2,
            "l": min(open_price, close) - 1.0,
            "c": close,
        })
    return {
        "schema_version": "generated-kline-v1",
        "panels": [{
            "panel_id": "main",
            "visual_type": "candlestick",
            "bars": bars,
        }],
    }


class KlineRenderTests(unittest.TestCase):
    def setUp(self):
        self.token = patch.object(main, "TOKEN", "kline-test-token")
        self.token.start()
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        self.addCleanup(self.token.stop)

    def test_route_requires_existing_backend_token(self):
        response = self.client.post("/v1/kline/render", json=kline_payload())
        self.assertEqual(response.status_code, 401, response.text)

    def test_renders_generated_kline_payload_to_png(self):
        response = self.client.post(
            "/v1/kline/render",
            headers=AUTH,
            json=kline_payload(),
        )
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["schema_version"], "kline-render-v1")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["bar_count"], 20)
        self.assertTrue(result["image_url"].endswith(".png"))
        self.assertIn("/media/", result["image_url"])

        image_path = Path(main.MEDIA_DIR) / result["image_url"].rsplit("/", 1)[-1]
        self.addCleanup(image_path.unlink, missing_ok=True)
        with Image.open(image_path) as image:
            self.assertEqual(image.size, (1080, 720))
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.mode, "RGB")

        media_response = self.client.get(
            "/media/" + result["image_url"].rsplit("/", 1)[-1]
        )
        self.assertEqual(media_response.status_code, 200, media_response.text)
        self.assertEqual(media_response.headers["content-type"], "image/png")

    def test_rejects_wrong_schema_version(self):
        payload = kline_payload()
        payload["schema_version"] = "other-v1"
        response = self.client.post(
            "/v1/kline/render",
            headers=AUTH,
            json=payload,
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_missing_configuration_remains_fail_closed(self):
        with patch.object(main, "TOKEN", "change-me"):
            response = self.client.post(
                "/v1/kline/render",
                headers=AUTH,
                json=kline_payload(),
            )
        self.assertEqual(response.status_code, 503, response.text)


if __name__ == "__main__":
    unittest.main()
