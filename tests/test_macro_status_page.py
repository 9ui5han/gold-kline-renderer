import unittest
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import main


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MacroStatusPageTests(unittest.TestCase):
    def test_page_and_assets_are_available_without_embedding_token(self):
        page = (main.MACRO_STATUS_DIR / "index.html").read_text(encoding="utf-8")
        script = (main.MACRO_STATUS_DIR / "status.js").read_text(encoding="utf-8")
        stylesheet = (main.MACRO_STATUS_DIR / "status.css").read_text(encoding="utf-8")

        self.assertIn("宏观事件服务状态", page)
        self.assertNotIn(main.TOKEN, page)
        self.assertIn("/v1/macro-events/status-summary", script)
        for event_name in (
            "CPI", "PPI", "非农", "PCE", "FOMC", "Powell",
            "美国国债发行与拍卖", "美国国债回购", "美国财政部债务公告",
        ):
            self.assertIn(event_name, page)
        self.assertIn("event_types", script)
        self.assertIn("Asia/Shanghai", script)
        self.assertIn("检查时间（北京时间）", page)
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
            "source_count": 7,
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
                "error_message": "Bearer secret-token leaked",
                "response_sample": "secret upstream body",
            }],
        }
        main.MACRO_STATUS_CACHE.update({"expires_at": 0.0, "payload": None})
        with patch.object(main, "probe_all_sources", return_value=private) as probe:
            with patch.object(
                main.MACRO_CONTEXT_SERVICE,
                "get_cached_event_type_summary",
                return_value=[{
                    "event_code": "cpi",
                    "source": "bls",
                    "configured": True,
                    "event_count": 12,
                }],
            ):
                first = main._public_macro_status()
                second = main._public_macro_status()

        self.assertEqual(probe.call_count, 1)
        self.assertEqual(first, second)
        encoded = str(first)
        self.assertNotIn("private.example", encoded)
        self.assertNotIn("secret upstream body", encoded)
        self.assertNotIn("response_bytes", encoded)
        self.assertNotIn("secret-token", encoded)
        self.assertNotIn("error_message", encoded)
        self.assertEqual(first["schema_version"], "macro-public-status-v2")
        self.assertEqual(first["event_types"][0]["event_code"], "cpi")
        self.assertNotIn("source_url", encoded)
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
            "event_types": [],
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

    def test_cached_event_summary_lists_all_configured_event_types(self):
        with patch.object(
            main.MACRO_CONTEXT_SERVICE,
            "_load_cache",
            return_value={
                "schema_version": "macro-source-cache-v1",
                "sources": {
                    "bls": {
                        "fetched_at_utc": "2026-08-24T00:00:00Z",
                        "events": [
                            {"event_code": "cpi", "scheduled_time_utc": "2026-08-12T12:30:00Z"},
                            {"event_code": "ppi", "scheduled_time_utc": "2026-09-10T12:30:00Z"},
                            {"event_code": "employment", "scheduled_date": "2026-09-04"},
                        ],
                    },
                    "bea": {
                        "fetched_at_utc": "2026-08-24T00:00:00Z",
                        "events": [{"event_code": "pce", "scheduled_time_utc": "2026-08-28T12:30:00Z"}],
                    },
                    "fed": {
                        "fetched_at_utc": "2026-08-24T00:00:00Z",
                        "events": [{"event_code": "fomc", "scheduled_date": "2026-09-16"}],
                    },
                    "fed_speeches": {
                        "fetched_at_utc": "2026-08-24T00:00:00Z",
                        "events": [{"event_code": "fed_speech", "scheduled_time_utc": "2026-08-20T12:00:00Z"}],
                    },
                    "treasury_auctions": {
                        "fetched_at_utc": "2026-08-24T00:00:00Z",
                        "events": [{"event_code": "treasury_auction", "scheduled_time_utc": "2026-08-27T17:00:00Z"}],
                    },
                    "treasury_buybacks": {
                        "fetched_at_utc": "2026-08-24T00:00:00Z",
                        "events": [{"event_code": "treasury_buyback", "scheduled_time_utc": "2026-08-25T17:40:00Z"}],
                    },
                    "treasury_press": {
                        "fetched_at_utc": "2026-08-24T00:00:00Z",
                        "events": [{"event_code": "treasury_announcement", "scheduled_time_utc": "2026-08-19T08:30:00Z"}],
                    },
                },
            },
        ):
            summaries = main.MACRO_CONTEXT_SERVICE.get_cached_event_type_summary(
                main.PUBLIC_MACRO_EVENT_TYPES,
                now=main.datetime(2026, 8, 24, tzinfo=main.timezone.utc),
            )

        self.assertEqual(
            {item["event_code"] for item in summaries},
            {
                "cpi", "ppi", "employment", "pce", "fomc", "fed_speech",
                "treasury_auction", "treasury_buyback", "treasury_announcement",
            },
        )
        self.assertTrue(all(item["configured"] for item in summaries))
        self.assertEqual(
            next(item for item in summaries if item["event_code"] == "cpi")["event_count"],
            1,
        )

    def test_event_cards_cover_all_four_display_states(self):
        cases = [
            ({"source_healthy": False, "cache_state": "missing", "event_count": 0}, ["bad", "来源异常"]),
            ({"source_healthy": True, "cache_state": "missing", "event_count": 0}, ["partial", "等待缓存"]),
            ({"source_healthy": True, "cache_state": "cached", "event_count": 0}, ["partial", "未识别到事件"]),
            ({"source_healthy": True, "cache_state": "cached", "event_count": 12}, ["good", "已接入"]),
        ]
        script = """
global.document = {
  querySelector: () => ({ addEventListener: () => {} }),
  querySelectorAll: () => []
};
const { eventState } = require('./app/macro_status/status.js');
const cases = JSON.parse(process.argv[1]);
process.stdout.write(JSON.stringify(cases.map((item) => eventState(item))));
"""
        completed = subprocess.run(
            ["node", "-e", script, json.dumps([item for item, _ in cases])],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(
            json.loads(completed.stdout),
            [expected for _, expected in cases],
        )

    def test_checked_time_is_formatted_as_beijing_time(self):
        script = """
global.document = {
  querySelector: () => ({ addEventListener: () => {} }),
  querySelectorAll: () => []
};
const { formatBeijingTime } = require('./app/macro_status/status.js');
process.stdout.write(formatBeijingTime('2026-08-24T09:38:04Z'));
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.stdout, "2026-08-24 17:38:04")


if __name__ == "__main__":
    unittest.main()
