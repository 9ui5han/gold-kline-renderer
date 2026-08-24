import unittest

from app.fed_speech_calendar import parse_fed_powell_rss
from app.treasury_calendar import (
    parse_treasury_auctions,
    parse_treasury_buybacks,
    parse_treasury_press_releases,
)


FETCHED_AT = "2026-08-24T09:00:00Z"


class OfficialDebtSourceTests(unittest.TestCase):
    def test_powell_rss_uses_publication_time(self):
        events = parse_fed_powell_rss(
            """<?xml version="1.0"?><rss><channel><item>
            <title>Powell, Economic Outlook</title>
            <link>https://www.federalreserve.gov/newsevents/speech/powell20250822a.htm</link>
            <pubDate>Fri, 22 Aug 2025 14:00:00 GMT</pubDate>
            </item></channel></rss>""",
            FETCHED_AT,
        )
        self.assertEqual(events[0]["event_code"], "fed_speech")
        self.assertEqual(events[0]["time_precision"], "exact")
        self.assertEqual(events[0]["time_basis"], "publication_time")
        self.assertEqual(events[0]["scheduled_time_utc"], "2025-08-22T14:00:00Z")

    def test_powell_ceremonial_remarks_are_not_macro_events(self):
        events = parse_fed_powell_rss(
            """<rss><channel><item>
            <title>Powell, Acceptance Remarks</title>
            <description>For a public service award ceremony</description>
            <link>https://www.federalreserve.gov/newsevents/speech/powell20260531a.htm</link>
            <pubDate>Mon, 1 Jun 2026 00:30:00 GMT</pubDate>
            </item><item>
            <title>Powell, Understanding the Fed's Balance Sheet</title>
            <description>At an economic policy conference</description>
            <link>https://www.federalreserve.gov/newsevents/speech/powell20251014a.htm</link>
            <pubDate>Tue, 14 Oct 2025 16:20:00 GMT</pubDate>
            </item></channel></rss>""",
            FETCHED_AT,
        )
        self.assertEqual(len(events), 1)
        self.assertIn("Balance Sheet", events[0]["title"])

    def test_auction_json_uses_official_competitive_close_time(self):
        events = parse_treasury_auctions({"data": [{
            "auction_date": "2026-08-27",
            "cusip": "91282CRJ2",
            "security_type": "Note",
            "security_term": "7-Year",
            "closing_time_comp": "01:00 PM",
            "offering_amt": "44000000000",
        }]}, FETCHED_AT)
        self.assertEqual(events[0]["event_code"], "treasury_auction")
        self.assertEqual(events[0]["scheduled_time_utc"], "2026-08-27T17:00:00Z")
        self.assertEqual(events[0]["offering_amount_usd"], "44000000000")

    def test_auction_missing_close_time_fails_instead_of_inventing_time(self):
        with self.assertRaisesRegex(ValueError, "TREASURY_AUCTION_TIME_MISSING"):
            parse_treasury_auctions({"data": [{
                "auction_date": "2026-08-27",
                "cusip": "91282CRJ2",
                "security_type": "Note",
                "security_term": "7-Year",
            }]}, FETCHED_AT)

    def test_buyback_xml_handles_eastern_daylight_time(self):
        events = parse_treasury_buybacks(
            """<BuyBackCalendar><BuybackCalendarDate>
            <PurchaseBucketName>Nominal Coupons 20Y to 30Y</PurchaseBucketName>
            <SecurityType>NOMINAL COUPONS</SecurityType>
            <OperationType>Liquidity Support</OperationType>
            <MaximumPurchaseAmountDollars>2000000000</MaximumPurchaseAmountDollars>
            <AnnouncementDate>2026-08-17</AnnouncementDate>
            <OperationDate>2026-08-18</OperationDate>
            <OperationStartTimeEasternUS>13:40</OperationStartTimeEasternUS>
            </BuybackCalendarDate></BuyBackCalendar>""",
            FETCHED_AT,
        )
        self.assertEqual(events[0]["event_code"], "treasury_buyback")
        self.assertEqual(events[0]["scheduled_time_utc"], "2026-08-18T17:40:00Z")
        self.assertEqual(events[0]["impact"], "high")

    def test_press_page_keeps_debt_news_and_excludes_sanctions(self):
        events = parse_treasury_press_releases(
            {"items": [
                {"datetime": "2026-08-20T13:30:00Z", "url": "/news/press-releases/sb0611/", "title": "Treasury Increases Sanctions"},
                {"datetime": "2026-08-19T08:30:00Z", "url": "/news/press-releases/sb0607/", "title": "Treasury Announces Increased Sizes of Nominal Long-End Liquidity Support Buybacks Beginning September 9"},
                {"datetime": "2026-08-18T10:00:00Z", "url": "/news/press-releases/test002/", "title": "Treasury Sanctions Illegal Property Auction Network"},
            ]},
            FETCHED_AT,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_code"], "treasury_announcement")
        self.assertIn("sb0607", events[0]["source_url"])
        self.assertEqual(events[0]["source_local_time"], "2026-08-19T08:30:00-04:00")
        self.assertEqual(events[0]["scheduled_time_utc"], "2026-08-19T12:30:00Z")
        self.assertEqual(events[0]["source_timezone"], "America/New_York")
        self.assertEqual(events[0]["time_precision"], "exact")
        self.assertEqual(events[0]["time_basis"], "publication_time")

    def test_press_page_handles_eastern_standard_time_without_fixed_offset(self):
        events = parse_treasury_press_releases(
            {"items": [{
                "datetime": "2026-02-04T08:30:00Z",
                "url": "/news/press-releases/sb0384/",
                "title": "Treasury Announces Quarterly Refunding",
            }]},
            FETCHED_AT,
        )
        self.assertEqual(events[0]["source_local_time"], "2026-02-04T08:30:00-05:00")
        self.assertEqual(events[0]["scheduled_time_utc"], "2026-02-04T13:30:00Z")

    def test_press_empty_official_year_is_valid(self):
        self.assertEqual(
            parse_treasury_press_releases({"items": []}, FETCHED_AT),
            [],
        )

    def test_malformed_sources_fail_closed(self):
        with self.assertRaises(ValueError):
            parse_fed_powell_rss("<rss/>", FETCHED_AT)
        with self.assertRaises(ValueError):
            parse_treasury_auctions({"data": []}, FETCHED_AT)
        with self.assertRaises(ValueError):
            parse_treasury_buybacks("<BuyBackCalendar/>", FETCHED_AT)
        with self.assertRaises(ValueError):
            parse_treasury_press_releases({"items": "invalid"}, FETCHED_AT)


if __name__ == "__main__":
    unittest.main()
