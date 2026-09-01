import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.photo.page_renderer import render_page
from app.photo.routes import materialize_template_assets


class CarouselTemplateCompositionTests(unittest.TestCase):
    def test_cover_template_replaces_generated_cover_decorations_but_keeps_backend_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "cover.png"
            output = root / "page.png"
            Image.new("RGB", (1024, 1024), "#E8EEF2").save(template)
            result = render_page(
                {
                    "page_no": 1,
                    "page_role": "cover",
                    "visual_type": "cover_illustration",
                    "title": "Propulsion Blocks",
                    "body": "A precise step-by-step educational guide.",
                },
                None,
                [{
                    "asset_type": "background",
                    "asset_key": "cover_template",
                    "purpose": "page_template",
                    "asset_path": str(template),
                }],
                output,
                1024,
                1024,
                language="en",
            )
            self.assertEqual(result["cover_visual_type"], "template_background")
            self.assertTrue(result["topic_visual_present"])
            self.assertFalse(result["character_present"])
            self.assertFalse(result["cover_asset_present"])

    def test_remote_template_is_materialized_only_from_302_file_host(self):
        buffer = BytesIO()
        Image.new("RGB", (32, 32), "#123B5D").save(buffer, "PNG")

        class Response:
            status_code = 200
            headers = {"content-type": "image/png"}
            content = buffer.getvalue()

        assets = {"schema_version": "photo-assets-v1", "assets": [{
            "page_no": 2,
            "asset_type": "background",
            "asset_key": "content_template",
            "purpose": "page_template",
            "asset_url": "https://file.302.ai/template.png",
        }]}
        with tempfile.TemporaryDirectory() as tmp, patch("app.photo.routes.httpx.get", return_value=Response()) as request:
            result = materialize_template_assets(assets, Path(tmp), {2})
            path = Path(result["assets"][0]["asset_path"])
            self.assertTrue(path.is_file())
            self.assertNotIn("asset_url", result["assets"][0])
            self.assertFalse(request.call_args.kwargs["follow_redirects"])
        assets["assets"][0]["asset_url"] = "https://evil.example/template.png"
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "TEMPLATE_HOST_INVALID"):
            materialize_template_assets(assets, Path(tmp), {2})

    def test_template_set_missing_url_and_redirect_fail_closed(self):
        assets = {"schema_version": "photo-assets-v1", "assets": [{
            "page_no": 2, "asset_type": "background", "asset_key": "content_template",
            "purpose": "page_template", "asset_url": "",
        }]}
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "TEMPLATE_URL_MISSING"):
            materialize_template_assets(assets, Path(tmp), {2})

        class Redirect:
            status_code = 302
            headers = {"location": "http://169.254.169.254/latest/meta-data", "content-type": "text/html"}
            content = b"redirect"

        assets["assets"][0]["asset_url"] = "https://file.302.ai/template.png"
        with tempfile.TemporaryDirectory() as tmp, patch("app.photo.routes.httpx.get", return_value=Redirect()), self.assertRaisesRegex(ValueError, "TEMPLATE_DOWNLOAD_FAILED"):
            materialize_template_assets(assets, Path(tmp), {2})

    def test_template_background_is_used_before_backend_text_and_chart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            template = root / "template.png"
            chart = root / "chart.png"
            output = root / "page.png"
            Image.new("RGB", (1024, 1024), "#E8EEF2").save(template)
            Image.new("RGB", (1080, 720), "#D8A12E").save(chart)
            result = render_page(
                {
                    "page_no": 2,
                    "page_role": "definition",
                    "visual_type": "market_chart",
                    "title": "Bullish Propulsion Block",
                    "body": "A precise educational example using unchanged chart data.",
                },
                {"asset_path": str(chart), "data_fingerprint": "a" * 64},
                [{
                    "asset_type": "background",
                    "asset_key": "content_template",
                    "purpose": "page_template",
                    "asset_path": str(template),
                }],
                output,
                1024,
                1024,
                language="en",
            )
            self.assertTrue(result["template_background_present"])
            self.assertEqual(result["template_asset_key"], "content_template")
            self.assertTrue(result["chart_present"])
            with Image.open(output) as image:
                self.assertEqual(image.getpixel((10, 10)), (232, 238, 242))


if __name__ == "__main__":
    unittest.main()
