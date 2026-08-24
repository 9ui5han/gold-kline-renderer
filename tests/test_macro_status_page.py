import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


class MacroStatusPageTests(unittest.TestCase):
    def test_page_and_assets_are_available_without_embedding_token(self):
        page = (main.MACRO_STATUS_DIR / "index.html").read_text(encoding="utf-8")
        script = (main.MACRO_STATUS_DIR / "status.js").read_text(encoding="utf-8")
        stylesheet = (main.MACRO_STATUS_DIR / "status.css").read_text(encoding="utf-8")

        self.assertIn("宏观事件服务状态", page)
        self.assertNotIn(main.TOKEN, page)
        self.assertIn("/v1/macro-events/status-summary", script)
        self.assertNotIn("sessionStorage", script)
        self.assertNotIn("Authorization", script)
        self.assertNotIn(main.TOKEN, script)
        self.assertIn("!serviceHealthy", script)
        self.assertIn(".source-card", stylesheet)

    def test_page_has_security_headers(self):
        with TestClient(main.app) as api:
            response = api.get("/macro-status/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_existing_source_health_endpoint_remains_protected(self):
        route = next(
            route
            for route in main.app.routes
            if getattr(route, "path", None) == "/v1/macro-events/source-health"
        )
        dependency_names = {
            dependency.call.__name__
            for dependency in route.dependant.dependencies
        }
        self.assertIn("require_token", dependency_names)

    def test_public_summary_is_sanitized_and_cached(self):
        private = {
            "checked_at_utc": "2026-08-24T00:00:00Z",
            "data_status": "complete",
            "source_count": 3,
            "valid_source_count": 3,
            "sources": [{
                "source": "fed",
                "url": "https://private.example.test/calendar",
                "requested_at_utc": "2026-08-24T00:00:00Z",
                "http_status": 200,
                "content_type": "text/html",
                "response_bytes": 100,
                "elapsed_ms": 25,
                "reachable": True,
                "structure_valid": True,
                "error_code": "",
                "error_message": "",
                "response_sample": "secret upstream body",
            }],
        }
        main.MACRO_STATUS_CACHE.update({"expires_at": 0.0, "payload": None})
        with patch.object(main, "probe_all_sources", return_value=private) as probe:
            first = main._public_macro_status()
            second = main._public_macro_status()

        self.assertEqual(probe.call_count, 1)
        self.assertEqual(first, second)
        encoded = str(first)
        self.assertNotIn("private.example", encoded)
        self.assertNotIn("secret upstream body", encoded)
        self.assertNotIn("response_bytes", encoded)
        self.assertEqual(first["schema_version"], "macro-public-status-v1")
        self.assertGreaterEqual(first["cache_ttl_sec"], 60)

    def test_public_summary_route_does_not_require_token(self):
        expected = {
            "schema_version": "macro-public-status-v1",
            "checked_at_utc": "2026-08-24T00:00:00Z",
            "data_status": "unavailable",
            "source_count": 3,
            "valid_source_count": 0,
            "cache_ttl_sec": 60,
            "sources": [],
        }
        with (
            patch.object(main, "_public_macro_status", return_value=expected),
            TestClient(main.app) as api,
        ):
            response = api.get("/v1/macro-events/status-summary")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)

    def test_macro_status_static_mount_is_registered(self):
        route = next(
            route
            for route in main.app.routes
            if getattr(route, "path", None) == "/macro-status"
        )
        self.assertEqual(route.name, "macro-status")

    def test_status_assets_exist_in_packaged_app(self):
        expected = {"index.html", "status.css", "status.js"}
        found = {path.name for path in Path(main.MACRO_STATUS_DIR).iterdir()}
        self.assertTrue(expected.issubset(found))


if __name__ == "__main__":
    unittest.main()
