import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import main
from app.macro_context import MacroContextError, MacroContextService
from app.macro_source_probe import SOURCE_SPECS


NOW = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
REQUEST = {
    "request_id": "macro-test-1",
    "symbol": "XAUUSD",
    "data_as_of": "2026-09-16T11:45:00Z",
    "forecast_horizon": {
        "schema_version": "forecast-horizon-v1",
        "timeframe": "15m",
        "start_time": "2026-09-16T12:00:00Z",
        "end_time": "2026-09-16T20:00:00Z",
        "duration_minutes": 480,
    },
}

FED_HTML = """<h4>2026 FOMC Meetings</h4>
<div class="row fomc-meeting">
  <div class="fomc-meeting__month">September</div>
  <div class="fomc-meeting__date">15-16*</div>
</div>"""
BLS_ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:cpi-test@bls.gov
DTSTART;TZID=America/New_York:20260916T083000
SUMMARY:Consumer Price Index
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""
BEA_JSON = {
    "Personal Income and Outlays": {
        "release_dates": ["2026-09-16T12:30:00+00:00"],
    }
}


def response_map(overrides=None):
    responses = {
        "fed": (200, "text/html; charset=utf-8", FED_HTML),
        "bls": (200, "text/calendar; charset=utf-8", BLS_ICS),
        "bea": (200, "application/json", json.dumps(BEA_JSON)),
    }
    responses.update(overrides or {})
    return responses


def mock_client(responses, request_counter=None):
    def handler(request: httpx.Request) -> httpx.Response:
        source = next(
            spec.source for spec in SOURCE_SPECS if spec.url == str(request.url)
        )
        if request_counter is not None:
            request_counter[source] = request_counter.get(source, 0) + 1
        configured = responses[source]
        if isinstance(configured, Exception):
            raise configured
        status, content_type, body = configured
        return httpx.Response(
            status,
            headers={"content-type": content_type},
            text=body,
            request=request,
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


class MacroContextTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self.temp_dir.name) / "macro-cache.json"

    def tearDown(self):
        self.temp_dir.cleanup()

    def service(self, **kwargs):
        return MacroContextService(self.cache_path, **kwargs)

    def test_all_sources_complete_and_direction_is_not_calculated(self):
        with mock_client(response_map()) as client:
            result = self.service().get_context(REQUEST, client=client, now=NOW)

        self.assertEqual(result["data_status"], "complete")
        self.assertEqual(result["directional_bias"], "not_calculated")
        self.assertEqual(len(result["events"]), 4)
        self.assertEqual(
            {event["event_code"] for event in result["events"]},
            {"cpi", "pce", "fomc"},
        )
        self.assertTrue(
            all(status["available"] for status in result["source_status"].values())
        )

    def test_one_source_http_failure_returns_partial_not_empty_success(self):
        responses = response_map(
            {"bls": (403, "text/html", "blocked")}
        )
        with mock_client(responses) as client:
            result = self.service().get_context(REQUEST, client=client, now=NOW)

        self.assertEqual(result["data_status"], "partial")
        self.assertFalse(result["source_status"]["bls"]["available"])
        self.assertEqual(
            result["source_status"]["bls"]["error_code"],
            "HTTP_STATUS_403",
        )
        self.assertEqual(len(result["events"]), 3)

    def test_timeout_uses_recent_stale_cache_and_marks_partial(self):
        service = self.service(cache_ttl_sec=60, max_stale_sec=3600)
        with mock_client(response_map()) as client:
            service.get_context(REQUEST, client=client, now=NOW)

        later = NOW + timedelta(minutes=2)
        timeout_request = httpx.Request("GET", SOURCE_SPECS[1].url)
        responses = response_map(
            {"bls": httpx.ReadTimeout("timeout", request=timeout_request)}
        )
        with mock_client(responses) as client:
            result = service.get_context(REQUEST, client=client, now=later)

        self.assertEqual(result["data_status"], "partial")
        self.assertTrue(result["source_status"]["bls"]["available"])
        self.assertTrue(result["source_status"]["bls"]["stale"])
        self.assertEqual(
            result["source_status"]["bls"]["cache_state"],
            "stale_fallback",
        )
        self.assertEqual(
            result["source_status"]["bls"]["error_code"],
            "UPSTREAM_TIMEOUT",
        )

    def test_all_sources_fail_without_cache_is_unavailable(self):
        responses = {
            source: (503, "text/html", "unavailable")
            for source in ("fed", "bls", "bea")
        }
        with mock_client(responses) as client:
            result = self.service().get_context(REQUEST, client=client, now=NOW)

        self.assertEqual(result["data_status"], "unavailable")
        self.assertEqual(result["events"], [])
        self.assertTrue(
            all(
                status["error_code"] == "HTTP_STATUS_503"
                for status in result["source_status"].values()
            )
        )

    def test_fresh_cache_avoids_repeating_official_requests(self):
        counter = {}
        service = self.service(cache_ttl_sec=3600)
        with mock_client(response_map(), counter) as client:
            first = service.get_context(REQUEST, client=client, now=NOW)
            second = service.get_context(
                REQUEST,
                client=client,
                now=NOW + timedelta(minutes=5),
            )

        self.assertEqual(first["data_status"], "complete")
        self.assertEqual(second["data_status"], "complete")
        self.assertEqual(counter, {"fed": 1, "bls": 1, "bea": 1})
        self.assertTrue(
            all(
                status["cache_state"] == "fresh"
                for status in second["source_status"].values()
            )
        )

    def test_valid_but_empty_source_is_partial_with_explicit_error(self):
        responses = response_map(
            {
                "bea": (
                    200,
                    "application/json",
                    '{"Personal Income and Outlays":{"release_dates":[]}}',
                )
            }
        )
        with mock_client(responses) as client:
            result = self.service().get_context(REQUEST, client=client, now=NOW)

        self.assertEqual(result["data_status"], "partial")
        self.assertEqual(
            result["source_status"]["bea"]["error_code"],
            "SOURCE_EVENTS_EMPTY",
        )

    def test_events_outside_expanded_query_window_are_filtered(self):
        responses = response_map(
            {
                "bea": (
                    200,
                    "application/json",
                    '{"Personal Income and Outlays":{"release_dates":['
                    '"2025-01-01T13:30:00+00:00"]}}',
                )
            }
        )
        with mock_client(responses) as client:
            result = self.service().get_context(REQUEST, client=client, now=NOW)

        self.assertEqual(result["data_status"], "complete")
        self.assertNotIn("pce", {event["event_code"] for event in result["events"]})

    def test_request_rejects_timezone_free_data_as_of(self):
        invalid = dict(REQUEST)
        invalid["data_as_of"] = "2026-09-16T11:45:00"
        with self.assertRaisesRegex(
            MacroContextError,
            "DATA_AS_OF_TIMEZONE_REQUIRED",
        ):
            self.service().get_context(invalid, now=NOW)

    def test_context_route_uses_bearer_auth_and_service(self):
        expected = {
            "schema_version": "macro-events-context-v1",
            "request_id": "macro-test-1",
            "data_status": "complete",
            "directional_bias": "not_calculated",
            "events": [],
        }
        with (
            patch.object(main, "TOKEN", "unit-test-token"),
            patch.object(
                main.MACRO_CONTEXT_SERVICE,
                "get_context",
                return_value=expected,
            ) as get_context,
            TestClient(main.app) as api,
        ):
            unauthorized = api.post("/v1/macro-events/context", json=REQUEST)
            authorized = api.post(
                "/v1/macro-events/context",
                json=REQUEST,
                headers={"Authorization": "Bearer unit-test-token"},
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json(), expected)
        get_context.assert_called_once()


if __name__ == "__main__":
    unittest.main()
