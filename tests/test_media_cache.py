import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app import main


class MediaCacheTests(unittest.TestCase):
    def test_generated_media_has_long_lived_cache_header(self):
        filename = f"cache-test-{uuid.uuid4().hex}.mp4"
        media_path = Path(main.MEDIA_DIR) / filename
        media_path.write_bytes(b"test-media")
        try:
            response = TestClient(main.app).get(f"/media/{filename}")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.headers.get("cache-control"),
                "public, max-age=86400, immutable",
            )
        finally:
            media_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
