import json
import unittest

from app.bea_calendar import BeaCalendarParseError, parse_bea_release_dates


FIXED_BEA_RELEASE_DATES = {
    "Gross Domestic Product": {
        "release_dates": ["2026-07-30T12:30:00+00:00"],
    },
    "Personal Income and Outlays": {
        "release_dates": [
            "2026-06-25T12:30:00+00:00",
            "2026-07-30T12:30:00+00:00",
            "2026-08-26T12:30:00+00:00",
        ],
    },
}


class BeaCalendarTests(unittest.TestCase):
    def test_extracts_only_personal_income_and_outlays_dates(self):
        events = parse_bea_release_dates(
            json.dumps(FIXED_BEA_RELEASE_DATES),
            "2026-08-03T09:28:10Z",
        )

        self.assertEqual(len(events), 3)
        self.assertTrue(all(event["event_code"] == "pce" for event in events))
        self.assertTrue(
            all(event["title"] == "Personal Income and Outlays" for event in events)
        )
        self.assertEqual(events[0]["scheduled_time_utc"], "2026-06-25T12:30:00Z")
        self.assertEqual(events[2]["scheduled_date"], "2026-08-26")
        self.assertTrue(all(event["source"] == "bea" for event in events))
        self.assertNotIn("directional_bias", events[0])
        self.assertNotIn("actual", events[0])
        self.assertNotIn("consensus", events[0])

    def test_one_release_represents_pce_and_core_pce_together(self):
        events = parse_bea_release_dates(
            {
                "Personal Income and Outlays": {
                    "release_dates": ["2026-09-30T12:30:00+00:00"],
                }
            },
            "2026-08-03T09:28:10Z",
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_code"], "pce")

    def test_converts_offset_time_to_utc_and_supports_date_only(self):
        payload = {
            "Personal Income and Outlays": {
                "release_dates": [
                    "2026-10-29T08:30:00-04:00",
                    "2026-11-25",
                ],
            }
        }
        events = parse_bea_release_dates(payload, "2026-08-03T09:28:10Z")

        self.assertEqual(events[0]["scheduled_time_utc"], "2026-10-29T12:30:00Z")
        self.assertIsNone(events[1]["scheduled_time_utc"])
        self.assertEqual(events[1]["time_precision"], "date_only")

    def test_duplicate_release_time_is_returned_once(self):
        value = "2026-12-23T13:30:00+00:00"
        payload = {
            "Personal Income and Outlays": {
                "release_dates": [value, "2026-12-23T13:30:00Z"],
            }
        }

        events = parse_bea_release_dates(payload, "2026-08-03T09:28:10Z")

        self.assertEqual(len(events), 1)

    def test_empty_release_dates_returns_empty_list(self):
        payload = {"Personal Income and Outlays": {"release_dates": []}}

        events = parse_bea_release_dates(payload, "2026-08-03T09:28:10Z")

        self.assertEqual(events, [])

    def test_rejects_missing_pce_release(self):
        with self.assertRaisesRegex(
            BeaCalendarParseError,
            "BEA_PCE_RELEASE_MISSING",
        ):
            parse_bea_release_dates(
                {"Gross Domestic Product": {"release_dates": []}},
                "2026-08-03T09:28:10Z",
            )

    def test_rejects_invalid_release_datetime(self):
        payload = {
            "Personal Income and Outlays": {
                "release_dates": ["2026/08/26 08:30"],
            }
        }
        with self.assertRaisesRegex(
            BeaCalendarParseError,
            "BEA_INVALID_RELEASE_DATETIME",
        ):
            parse_bea_release_dates(payload, "2026-08-03T09:28:10Z")

    def test_rejects_release_datetime_without_timezone(self):
        payload = {
            "Personal Income and Outlays": {
                "release_dates": ["2026-08-26T08:30:00"],
            }
        }
        with self.assertRaisesRegex(
            BeaCalendarParseError,
            "BEA_RELEASE_TIMEZONE_REQUIRED",
        ):
            parse_bea_release_dates(payload, "2026-08-03T09:28:10Z")


if __name__ == "__main__":
    unittest.main()
