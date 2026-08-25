import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import main
from app.macro_context import MacroContextError, MacroContextService, _validate_request
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
        "end_time": "2026-09-16T19:45:00Z",
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
FED_SPEECH_RSS = """<rss><channel><item>
<title>Powell, Economic Outlook</title>
<link>https://www.federalreserve.gov/newsevents/speech/powell20260916a.htm</link>
<pubDate>Wed, 16 Sep 2026 15:00:00 GMT</pubDate>
</item></channel></rss>"""
NYFED_WILLIAMS_HTML = """<table><tr><td class="dirColL">Sep 16, 2026</td>
<td><a href="/newsevents/speeches/2026/wil260916" class="paraHeader">Williams: Monetary Policy Outlook</a></td>
</tr></table>"""
WHITEHOUSE_REMARKS_HTML = """<ul><li class="playlist_term-remarks-from-president-trump">
<a href="/videos/trump-energy/" title="President Trump Remarks on Energy and Trade">President Trump Remarks on Energy and Trade</a>
<time datetime="2026-09-16T12:45:00+00:00"></time></li></ul>"""
STATE_DIPLOMACY_JSON = [{
    "date_gmt": "2026-09-16T12:50:00",
    "link": "https://www.state.gov/releases/rubio-energy-sanctions/",
    "title": {"rendered": "Remarks by Secretary Rubio on Energy Sanctions"},
    "content": {"rendered": "<p>Iran energy policy.</p>"},
}]
TREASURY_AUCTIONS_JSON = {"data": [{
    "auction_date": "2026-09-16",
    "cusip": "91282TEST",
    "security_type": "Note",
    "security_term": "10-Year",
    "closing_time_comp": "01:00 PM",
    "offering_amt": "42000000000",
}]}
TREASURY_BUYBACK_XML = """<BuyBackCalendar><BuybackCalendarDate>
<PurchaseBucketName>Nominal Coupons 10Y to 20Y</PurchaseBucketName>
<SecurityType>NOMINAL COUPONS</SecurityType>
<OperationType>Liquidity Support</OperationType>
<MaximumPurchaseAmountDollars>2000000000</MaximumPurchaseAmountDollars>
<AnnouncementDate>2026-09-15</AnnouncementDate>
<OperationDate>2026-09-16</OperationDate>
<OperationStartTimeEasternUS>13:40</OperationStartTimeEasternUS>
</BuybackCalendarDate></BuyBackCalendar>"""
TREASURY_PRESS_JSON = {"items": [
    {
        "datetime": "2026-09-16T14:00:00Z",
        "url": "/news/press-releases/test001/",
        "title": "Treasury Announces Quarterly Refunding",
    },
    {
        "datetime": "2026-09-16T14:15:00Z",
        "url": "/news/press-releases/test-bessent/",
        "title": "Remarks by Treasury Secretary Scott Bessent before the Economic Club",
    },
]}


def response_map(overrides=None):
    responses = {
        "fed": (200, "text/html; charset=utf-8", FED_HTML),
        "bls": (200, "text/calendar; charset=utf-8", BLS_ICS),
        "bea": (200, "application/json", json.dumps(BEA_JSON)),
        "fed_speeches": (200, "text/xml", FED_SPEECH_RSS),
        "nyfed_williams_speeches": (200, "text/html", NYFED_WILLIAMS_HTML),
        "whitehouse_remarks": (200, "text/html", WHITEHOUSE_REMARKS_HTML),
        "state_diplomacy": (200, "application/json", json.dumps(STATE_DIPLOMACY_JSON)),
        "treasury_auctions": (
            200, "application/json", json.dumps(TREASURY_AUCTIONS_JSON),
        ),
        "treasury_buybacks": (200, "application/xml", TREASURY_BUYBACK_XML),
        "treasury_press": (
            200, "application/json", json.dumps(TREASURY_PRESS_JSON),
        ),
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
        self.assertEqual(len(result["events"]), 12)
        self.assertEqual(
            {event["event_code"] for event in result["events"]},
            {
                "cpi", "pce", "fomc", "fed_speech", "nyfed_williams_speech",
                "whitehouse_trump_remarks", "state_diplomatic_official_statement",
                "treasury_auction",
                "treasury_buyback", "treasury_announcement",
                "treasury_secretary_speech",
            },
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
        self.assertEqual(len(result["events"]), 11)

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
            spec.source: (503, "text/html", "unavailable")
            for spec in SOURCE_SPECS
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
        self.assertEqual(counter, {spec.source: 1 for spec in SOURCE_SPECS})
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

    def test_speech_source_without_a_qualified_item_is_healthy(self):
        no_macro_remarks = """<ul><li class="playlist_term-remarks-from-president-trump">
        <a href="/videos/reception/" title="President Trump Participates in a Team USA Reception">Reception</a>
        <time datetime="2026-09-16T12:45:00+00:00"></time></li></ul>"""
        with mock_client(
            response_map({"whitehouse_remarks": (200, "text/html", no_macro_remarks)})
        ) as client:
            result = self.service().get_context(REQUEST, client=client, now=NOW)

        self.assertEqual(result["data_status"], "complete")
        self.assertTrue(result["source_status"]["whitehouse_remarks"]["available"])
        self.assertEqual(result["source_status"]["whitehouse_remarks"]["event_count"], 0)

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

    def test_cross_year_window_merges_two_treasury_press_years(self):
        request = {
            "request_id": "cross-year",
            "symbol": "XAUUSD",
            "data_as_of": "2026-12-31T23:45:00Z",
            "forecast_horizon": {
                "schema_version": "forecast-horizon-v1",
                "timeframe": "15m",
                "start_time": "2027-01-01T00:00:00Z",
                "end_time": "2027-01-01T01:45:00Z",
                "duration_minutes": 120,
            },
        }
        requested_press_years = []

        def handler(http_request: httpx.Request) -> httpx.Response:
            url = str(http_request.url)
            if "/news-data/press-releases/search/" in url:
                year = int(url.rsplit("/", 1)[-1].split(".", 1)[0])
                requested_press_years.append(year)
                body = {"items": [{
                    "datetime": f"{year}-{'12-31T23:50:00Z' if year == 2026 else '01-01T00:30:00Z'}",
                    "url": f"/news/press-releases/year{year}/",
                    "title": "Treasury Announces Quarterly Refunding",
                }]}
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=body,
                    request=http_request,
                )
            source = next(
                spec.source for spec in SOURCE_SPECS if spec.url == url
            )
            status, content_type, body = response_map()[source]
            return httpx.Response(
                status,
                headers={"content-type": content_type},
                text=body,
                request=http_request,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = self.service().get_context(
                request,
                client=client,
                now=datetime(2027, 1, 1, tzinfo=timezone.utc),
            )

        announcements = [
            event for event in result["events"]
            if event["event_code"] == "treasury_announcement"
        ]
        self.assertEqual(requested_press_years, [2026, 2027])
        self.assertEqual(len(announcements), 2)

    def test_fresh_press_cache_switches_year_after_long_running_rollover(self):
        requested_press_years = []

        def handler(http_request: httpx.Request) -> httpx.Response:
            url = str(http_request.url)
            if "/news-data/press-releases/search/" in url:
                year = int(url.rsplit("/", 1)[-1].split(".", 1)[0])
                requested_press_years.append(year)
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"items": [{
                        "datetime": f"{year}-02-01T12:00:00Z",
                        "url": f"/news/press-releases/year{year}/",
                        "title": "Treasury Announces Quarterly Refunding",
                    }]},
                    request=http_request,
                )
            source = next(
                spec.source for spec in SOURCE_SPECS if spec.url == url
            )
            status, content_type, body = response_map()[source]
            return httpx.Response(
                status,
                headers={"content-type": content_type},
                text=body,
                request=http_request,
            )

        service = self.service(cache_ttl_sec=86400)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            service.get_context(REQUEST, client=client, now=NOW)
            request_2027 = {
                **REQUEST,
                "data_as_of": "2027-02-01T11:45:00Z",
                "forecast_horizon": {
                    **REQUEST["forecast_horizon"],
                    "start_time": "2027-02-01T12:00:00Z",
                    "end_time": "2027-02-01T19:45:00Z",
                },
            }
            service.get_context(
                request_2027,
                client=client,
                now=NOW + timedelta(hours=1),
            )

        self.assertEqual(requested_press_years, [2026, 2027])

    def test_press_year_with_empty_official_list_is_healthy_and_cached(self):
        request = {
            **REQUEST,
            "data_as_of": "2027-02-01T11:45:00Z",
            "forecast_horizon": {
                **REQUEST["forecast_horizon"],
                "start_time": "2027-02-01T12:00:00Z",
                "end_time": "2027-02-01T19:45:00Z",
            },
        }
        requested_press_years = []

        def handler(http_request: httpx.Request) -> httpx.Response:
            url = str(http_request.url)
            if "/news-data/press-releases/search/" in url:
                requested_press_years.append(2027)
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"items": []},
                    request=http_request,
                )
            source = next(spec.source for spec in SOURCE_SPECS if spec.url == url)
            status, content_type, body = response_map()[source]
            return httpx.Response(
                status,
                headers={"content-type": content_type},
                text=body,
                request=http_request,
            )

        service = self.service(cache_ttl_sec=3600)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            first = service.get_context(
                request, client=client, now=datetime(2027, 2, 1, tzinfo=timezone.utc)
            )
            second = service.get_context(
                request,
                client=client,
                now=datetime(2027, 2, 1, 0, 5, tzinfo=timezone.utc),
            )

        self.assertTrue(first["source_status"]["treasury_press"]["available"])
        self.assertEqual(first["source_status"]["treasury_press"]["event_count"], 0)
        self.assertEqual(second["source_status"]["treasury_press"]["cache_state"], "fresh")
        self.assertEqual(requested_press_years, [2027])

    def test_cross_year_press_allows_one_empty_year(self):
        request = {
            "request_id": "cross-year-empty",
            "symbol": "XAUUSD",
            "data_as_of": "2026-12-31T23:45:00Z",
            "forecast_horizon": {
                "schema_version": "forecast-horizon-v1",
                "timeframe": "15m",
                "start_time": "2027-01-01T00:00:00Z",
                "end_time": "2027-01-01T01:45:00Z",
                "duration_minutes": 120,
            },
        }

        def handler(http_request: httpx.Request) -> httpx.Response:
            url = str(http_request.url)
            if "/news-data/press-releases/search/" in url:
                year = int(url.rsplit("/", 1)[-1].split(".", 1)[0])
                title = (
                    "Treasury Announces Quarterly Refunding"
                    if year == 2026
                    else "Treasury Announces Community Grant Program"
                )
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={"items": [{
                        "datetime": f"{year}-{'12-31T23:50:00Z' if year == 2026 else '01-01T00:30:00Z'}",
                        "url": f"/news/press-releases/year{year}/",
                        "title": title,
                    }]},
                    request=http_request,
                )
            source = next(spec.source for spec in SOURCE_SPECS if spec.url == url)
            status, content_type, body = response_map()[source]
            return httpx.Response(
                status,
                headers={"content-type": content_type},
                text=body,
                request=http_request,
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            result = self.service().get_context(
                request,
                client=client,
                now=datetime(2027, 1, 1, tzinfo=timezone.utc),
            )

        announcements = [
            event for event in result["events"]
            if event["event_code"] == "treasury_announcement"
        ]
        self.assertTrue(result["source_status"]["treasury_press"]["available"])
        self.assertEqual(len(announcements), 1)

    def test_request_rejects_timezone_free_data_as_of(self):
        invalid = dict(REQUEST)
        invalid["data_as_of"] = "2026-09-16T11:45:00"
        with self.assertRaisesRegex(
            MacroContextError,
            "DATA_AS_OF_TIMEZONE_REQUIRED",
        ):
            self.service().get_context(invalid, now=NOW)

    def test_request_duration_uses_data_as_of_not_first_future_candle(self):
        request = {
            "request_id": "macro-56de31e816dea253",
            "symbol": "XAUUSD",
            "data_as_of": "2026-08-03T09:45:00Z",
            "forecast_horizon": {
                "schema_version": "forecast-horizon-v1",
                "timeframe": "15m",
                "start_time": "2026-08-03T10:00:00Z",
                "end_time": "2026-08-03T11:45:00Z",
                "duration_minutes": 120,
            },
        }

        normalized = _validate_request(request)

        self.assertEqual(normalized["request_id"], request["request_id"])
        self.assertEqual(
            normalized["forecast_horizon"]["duration_minutes"],
            120.0,
        )

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
