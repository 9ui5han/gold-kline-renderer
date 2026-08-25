import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import main
from app.macro_source_probe import SOURCE_SPECS, build_user_agent, probe_all_sources


VALID_RESPONSES = {
    "fed": (
        200,
        "text/html; charset=utf-8",
        "<html><body>Federal Open Market Committee FOMC</body></html>",
    ),
    "bls": (
        200,
        "text/calendar; charset=utf-8",
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR",
    ),
    "bea": (
        200,
        "application/json",
        '{"file_last_updated":"2026-08-03","release_dates":[]}',
    ),
    "fed_speeches": (
        200,
        "text/xml; charset=utf-8",
        "<rss><channel><item><title>Powell</title></item></channel></rss>",
    ),
    "nyfed_williams_speeches": (
        200,
        "text/html; charset=utf-8",
        "<html><body><h1>Speeches</h1></body></html>",
    ),
    "whitehouse_remarks": (
        200,
        "text/html; charset=utf-8",
        "<html><body>Remarks from President Trump</body></html>",
    ),
    "state_diplomacy": (
        200,
        "application/json",
        "[]",
    ),
    "treasury_auctions": (
        200,
        "application/json",
        '{"data":[{"auction_date":"2026-08-27"}]}',
    ),
    "treasury_buybacks": (
        200,
        "application/xml",
        "<BuyBackCalendar><BuybackCalendarDate/></BuyBackCalendar>",
    ),
    "treasury_press": (
        200,
        "application/json",
        '{"category":"press-releases","items":[]}',
    ),
}


def client_for(responses):
    def handler(request: httpx.Request) -> httpx.Response:
        source = next(
            spec.source for spec in SOURCE_SPECS if spec.url == str(request.url)
        )
        status, content_type, body = responses[source]
        return httpx.Response(
            status,
            headers={"content-type": content_type},
            text=body,
            request=request,
        )

    return httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )


class MacroSourceProbeTests(unittest.TestCase):
    def test_render_user_agent_can_be_configured_without_railway_url(self):
        with patch.dict(
            "os.environ",
            {"MACRO_USER_AGENT": "GoldKlineRender/2.0 contact@example.com"},
        ):
            value = build_user_agent()

        self.assertEqual(value, "GoldKlineRender/2.0 contact@example.com")
        self.assertNotIn("railway", value.lower())

    def test_all_official_sources_valid(self):
        with client_for(VALID_RESPONSES) as client:
            result = probe_all_sources(client)

        self.assertEqual(result["schema_version"], "macro-source-health-v1")
        self.assertEqual(result["data_status"], "complete")
        self.assertEqual(result["valid_source_count"], 10)
        self.assertEqual(result["directional_bias"], "not_calculated")
        self.assertTrue(all(item["structure_valid"] for item in result["sources"]))

    def test_bls_403_is_partial_and_body_is_truncated(self):
        responses = dict(VALID_RESPONSES)
        responses["bls"] = (
            403,
            "text/html; charset=utf-8",
            "blocked " + ("x" * 500),
        )
        with client_for(responses) as client:
            result = probe_all_sources(client)

        self.assertEqual(result["data_status"], "partial")
        bls = next(item for item in result["sources"] if item["source"] == "bls")
        self.assertFalse(bls["reachable"])
        self.assertFalse(bls["structure_valid"])
        self.assertEqual(bls["error_code"], "HTTP_STATUS_403")
        self.assertLessEqual(len(bls["response_sample"]), 160)

    def test_wrong_content_type_is_not_accepted(self):
        responses = dict(VALID_RESPONSES)
        responses["bea"] = (200, "text/html", "<html>not json</html>")
        with client_for(responses) as client:
            result = probe_all_sources(client)

        bea = next(item for item in result["sources"] if item["source"] == "bea")
        self.assertTrue(bea["reachable"])
        self.assertFalse(bea["structure_valid"])
        self.assertEqual(bea["error_code"], "UNEXPECTED_RESPONSE_STRUCTURE")

    def test_source_health_route_uses_existing_bearer_auth(self):
        expected = {
            "schema_version": "macro-source-health-v1",
            "checked_at_utc": "2026-08-03T00:00:00Z",
            "data_status": "complete",
            "directional_bias": "not_calculated",
            "source_count": 10,
            "valid_source_count": 10,
            "sources": [],
        }
        with (
            patch.object(main, "TOKEN", "unit-test-token"),
            patch.object(main, "probe_all_sources", return_value=expected),
            TestClient(main.app) as api,
        ):
            unauthorized = api.get("/v1/macro-events/source-health")
            authorized = api.get(
                "/v1/macro-events/source-health",
                headers={"Authorization": "Bearer unit-test-token"},
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)
        self.assertEqual(authorized.json(), expected)


if __name__ == "__main__":
    unittest.main()
