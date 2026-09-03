import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app import main
from app.kline_render import (
    TEXT_RENDER_SCALE,
    ZONE_FILL,
    ZONE_FILL_PB,
    ZONE_LABEL,
    _body_width,
    _draw_panel,
    _zone_font,
)


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
            self.assertEqual(image.size, (1024, 1024))
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

    def test_uses_hollow_up_candles_dark_down_candles_and_dark_outlines(self):
        payload = kline_payload()
        response = self.client.post(
            "/v1/kline/render",
            headers=AUTH,
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        file_name = response.json()["image_url"].rsplit("/", 1)[-1]
        image_path = Path(main.MEDIA_DIR) / file_name
        self.addCleanup(image_path.unlink, missing_ok=True)

        with Image.open(image_path) as image:
            colors = set(image.getdata())
        self.assertIn((242, 245, 248), colors)
        self.assertIn((48, 70, 126), colors)
        self.assertIn((24, 30, 40), colors)

    def test_candle_body_uses_a_wider_share_of_each_cell(self):
        self.assertGreaterEqual(_body_width(6.0), 4)

    def test_candle_geometry_stays_at_original_canvas_resolution(self):
        draw = Mock()
        from app.kline_render import KlinePanel

        _draw_panel(
            draw,
            KlinePanel.model_validate(kline_payload()["panels"][0]),
            36,
            28,
            1008,
            664,
        )

        self.assertTrue(draw.line.call_args_list)
        self.assertTrue(
            all(call.kwargs["width"] == 1 for call in draw.line.call_args_list)
        )
        self.assertTrue(draw.rectangle.call_args_list)
        self.assertTrue(
            all(
                call.kwargs["width"] == 1
                for call in draw.rectangle.call_args_list
            )
        )
        self.assertTrue(
            all(
                max(call.args[0]) <= 1080
                for call in draw.line.call_args_list
            )
        )

    def test_supersampled_geometry_scales_strokes_without_changing_layout(self):
        draw = Mock()
        from app.kline_render import KlinePanel

        _draw_panel(
            draw,
            KlinePanel.model_validate(kline_payload()["panels"][0]),
            36 * TEXT_RENDER_SCALE,
            28 * TEXT_RENDER_SCALE,
            1008 * TEXT_RENDER_SCALE,
            664 * TEXT_RENDER_SCALE,
            draw_zones=False,
            render_scale=TEXT_RENDER_SCALE,
        )

        self.assertTrue(draw.line.call_args_list)
        self.assertTrue(
            all(
                call.kwargs["width"] == TEXT_RENDER_SCALE
                for call in draw.line.call_args_list
            )
        )
        self.assertTrue(draw.rectangle.call_args_list)
        self.assertTrue(
            all(
                call.kwargs["width"] == TEXT_RENDER_SCALE
                for call in draw.rectangle.call_args_list
            )
        )

    def test_zone_label_font_is_readable(self):
        self.assertEqual(getattr(_zone_font(), "size", 0), 19)

    def test_zone_labels_are_drawn_at_supersampled_resolution(self):
        self.assertGreaterEqual(TEXT_RENDER_SCALE, 3)
        self.assertEqual(
            getattr(_zone_font(TEXT_RENDER_SCALE), "size", 0),
            19 * TEXT_RENDER_SCALE,
        )

    def test_all_kline_text_uses_a_dedicated_text_layer(self):
        base_image = Image.new("RGBA", (1080, 720), (255, 255, 255, 255))
        text_layer = Image.new(
            "RGBA",
            (1080 * TEXT_RENDER_SCALE, 720 * TEXT_RENDER_SCALE),
            (0, 0, 0, 0),
        )
        panel = kline_payload()["panels"][0]
        panel["annotations"] = [{
            "annotation_id": "ob_1",
            "type": "ob",
            "label": "OB",
            "direction": "bullish",
            "start_index": 4,
            "end_index": 12,
            "price_low": 102.0,
            "price_high": 112.0,
        }]

        from app.kline_render import KlinePanel

        _draw_panel(
            ImageDraw.Draw(base_image),
            KlinePanel.model_validate(panel),
            36,
            28,
            1008,
            664,
            draw_zones=False,
            text_layer=text_layer,
        )

        self.assertIsNotNone(text_layer.getbbox())
        self.assertNotIn(ZONE_LABEL, set(base_image.convert("RGB").getdata()))

    def test_zone_label_is_drawn_after_candles(self):
        draw = Mock()
        draw.textbbox.return_value = (0, 0, 20, 20)
        panel = kline_payload()["panels"][0]
        panel["annotations"] = [{
            "annotation_id": "ob_1",
            "type": "ob",
            "label": "OB",
            "direction": "bullish",
            "start_index": 4,
            "end_index": 12,
            "price_low": 102.0,
            "price_high": 112.0,
        }]

        from app.kline_render import KlinePanel

        _draw_panel(
            draw,
            KlinePanel.model_validate(panel),
            36,
            28,
            1008,
            664,
        )

        method_names = [call[0] for call in draw.method_calls]
        text_index = method_names.index("text")
        last_rectangle_index = max(
            index
            for index, name in enumerate(method_names)
            if name == "rectangle"
        )
        self.assertGreater(text_index, last_rectangle_index)
        self.assertEqual(draw.text.call_args.kwargs["stroke_width"], 0)
        self.assertNotIn("stroke_fill", draw.text.call_args.kwargs)

    def test_renders_ob_annotation(self):
        payload = kline_payload()
        payload["panels"][0]["annotations"] = [{
            "annotation_id": "ob_1",
            "type": "ob",
            "label": "OB",
            "direction": "bullish",
            "start_index": 4,
            "end_index": 12,
            "price_low": 102.0,
            "price_high": 112.0,
        }]

        response = self.client.post(
            "/v1/kline/render",
            headers=AUTH,
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        file_name = response.json()["image_url"].rsplit("/", 1)[-1]
        image_path = Path(main.MEDIA_DIR) / file_name
        self.addCleanup(image_path.unlink, missing_ok=True)

        with Image.open(image_path) as image:
            colors = set(image.getdata())

        self.assertIn(ZONE_FILL, colors)
        self.assertTrue(
            any(
                red >= 180 and green <= 130 and blue <= 140
                for red, green, blue in colors
            )
        )

    def test_renders_ob_and_pb_with_different_translucent_colors(self):
        payload = kline_payload()
        payload["panels"][0]["annotations"] = [
            {
                "annotation_id": "ob_1",
                "type": "ob",
                "label": "OB",
                "direction": "bullish",
                "start_index": 1,
                "end_index": 5,
                "price_low": 100.0,
                "price_high": 104.0,
            },
            {
                "annotation_id": "pb_1",
                "type": "pb",
                "label": "PB",
                "direction": "bearish",
                "start_index": 10,
                "end_index": 14,
                "price_low": 113.0,
                "price_high": 117.0,
            },
        ]

        response = self.client.post(
            "/v1/kline/render",
            headers=AUTH,
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        file_name = response.json()["image_url"].rsplit("/", 1)[-1]
        image_path = Path(main.MEDIA_DIR) / file_name
        self.addCleanup(image_path.unlink, missing_ok=True)

        with Image.open(image_path) as image:
            colors = set(image.getdata())

        self.assertNotEqual(ZONE_FILL, ZONE_FILL_PB)
        self.assertIn(ZONE_FILL, colors)
        self.assertIn(ZONE_FILL_PB, colors)

    def test_overlapping_zones_are_alpha_composited(self):
        payload = kline_payload()
        payload["panels"][0]["annotations"] = [
            {
                "annotation_id": "ob_overlap",
                "type": "ob",
                "label": "OB",
                "direction": "bullish",
                "start_index": 2,
                "end_index": 12,
                "price_low": 105.0,
                "price_high": 115.0,
            },
            {
                "annotation_id": "pb_overlap",
                "type": "pb",
                "label": "PB",
                "direction": "bearish",
                "start_index": 7,
                "end_index": 17,
                "price_low": 105.0,
                "price_high": 115.0,
            },
        ]

        response = self.client.post(
            "/v1/kline/render",
            headers=AUTH,
            json=payload,
        )
        self.assertEqual(response.status_code, 200, response.text)
        file_name = response.json()["image_url"].rsplit("/", 1)[-1]
        image_path = Path(main.MEDIA_DIR) / file_name
        self.addCleanup(image_path.unlink, missing_ok=True)

        with Image.open(image_path) as image:
            colors = set(image.getdata())

        base = Image.new("RGBA", (1, 1), (255, 255, 255, 255))
        ob_layer = Image.new("RGBA", (1, 1), (112, 163, 201, 105))
        pb_layer = Image.new("RGBA", (1, 1), (232, 173, 88, 105))
        expected = Image.alpha_composite(
            Image.alpha_composite(base, ob_layer),
            pb_layer,
        ).convert("RGB").getpixel((0, 0))

        self.assertIn(expected, colors)

    def test_rejects_inverted_annotation_price_range(self):
        payload = kline_payload()
        payload["panels"][0]["annotations"] = [{
            "annotation_id": "bad_1",
            "type": "ob",
            "label": "OB",
            "direction": "bullish",
            "start_index": 4,
            "end_index": 12,
            "price_low": 112.0,
            "price_high": 102.0,
        }]

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
