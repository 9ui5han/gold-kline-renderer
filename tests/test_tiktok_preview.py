import unittest

from fastapi.testclient import TestClient

from app import main


class TikTokPreviewTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def test_preview_page_is_available_without_authentication(self):
        response = self.client.get("/preview/tiktok/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("TikTok 上传效果预览", response.text)
        self.assertIn("不会修改、导出或覆盖原始 MP4", response.text)
        self.assertIn('class="preview-stage"', response.text)
        self.assertIn('id="settings-panel"', response.text)
        self.assertIn('id="toggle-settings"', response.text)

    def test_preview_assets_are_available(self):
        css = self.client.get("/preview/tiktok/preview.css")
        javascript = self.client.get("/preview/tiktok/preview.js")

        self.assertEqual(css.status_code, 200)
        self.assertIn("height: 100dvh", css.text)
        self.assertIn("max-height: 100dvh", css.text)
        self.assertEqual(javascript.status_code, 200)
        self.assertIn("URL.createObjectURL", javascript.text)
        self.assertIn("aria-expanded", javascript.text)

    def test_missing_preview_file_returns_not_found(self):
        response = self.client.get("/preview/tiktok/not-found.js")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
