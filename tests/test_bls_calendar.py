import unittest

from app.bls_calendar import BlsCalendarParseError, parse_bls_ics


FIXED_BLS_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//U.S. Bureau of Labor Statistics//Release Calendar//EN
BEGIN:VEVENT
UID:employment-20260807@bls.gov
DTSTART;TZID=America/New_York:20260807T083000
SUMMARY:Employment Situation
STATUS:CONFIRMED
END:VEVENT
BEGIN:VEVENT
UID:cpi-20260812@bls.gov
DTSTART;TZID=US-Eastern:20260812T083000
SUMMARY:Consumer Price Index
STATUS:CONFIRMED
END:VEVENT
BEGIN:VEVENT
UID:ppi-20260813@bls.gov
DTSTART;TZID=Eastern Standard Time:20260813T083000
SUMMARY:Producer Price Index
STATUS:TENTATIVE
END:VEVENT
BEGIN:VEVENT
UID:jolts-20260804@bls.gov
DTSTART;TZID=America/New_York:20260804T100000
SUMMARY:Job Openings and Labor Turnover Survey
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR
"""


class BlsCalendarTests(unittest.TestCase):
    def test_extracts_only_three_whitelisted_releases(self):
        events = parse_bls_ics(FIXED_BLS_ICS, "2026-08-03T09:28:10Z")

        self.assertEqual(
            [event["event_code"] for event in events],
            ["employment", "cpi", "ppi"],
        )
        self.assertEqual(events[0]["scheduled_time_utc"], "2026-08-07T12:30:00Z")
        self.assertEqual(events[1]["scheduled_time_utc"], "2026-08-12T12:30:00Z")
        self.assertEqual(events[2]["scheduled_time_utc"], "2026-08-13T12:30:00Z")
        self.assertEqual(events[2]["status"], "tentative")
        self.assertTrue(all(event["impact"] == "high" for event in events))
        self.assertTrue(all(event["source"] == "bls" for event in events))
        self.assertNotIn("directional_bias", events[0])

    def test_converts_winter_eastern_time_and_utc_time(self):
        calendar = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:cpi-winter@bls.gov
DTSTART;TZID=America/New_York:20261210T083000
SUMMARY:Consumer Price Index
END:VEVENT
BEGIN:VEVENT
UID:employment-utc@bls.gov
DTSTART:20261204T133000Z
SUMMARY:Employment Situation
END:VEVENT
END:VCALENDAR
"""
        events = parse_bls_ics(calendar, "2026-08-03T09:28:10Z")

        self.assertEqual(events[0]["scheduled_time_utc"], "2026-12-04T13:30:00Z")
        self.assertEqual(events[1]["scheduled_time_utc"], "2026-12-10T13:30:00Z")

    def test_supports_folded_summary_and_date_only_event(self):
        calendar = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:ppi-date-only@bls.gov
DTSTART;VALUE=DATE:20260901
SUMMARY:Producer Price
  Index
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR
"""
        events = parse_bls_ics(calendar, "2026-08-03T09:28:10Z")

        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["scheduled_time_utc"])
        self.assertEqual(events[0]["scheduled_date"], "2026-09-01")
        self.assertEqual(events[0]["time_precision"], "date_only")
        self.assertEqual(events[0]["status"], "cancelled")

    def test_defaults_floating_bls_time_to_eastern_time(self):
        calendar = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:employment-floating@bls.gov
DTSTART:20261106T083000
SUMMARY:Employment Situation
END:VEVENT
END:VCALENDAR
"""
        events = parse_bls_ics(calendar, "2026-08-03T09:28:10Z")

        self.assertEqual(events[0]["scheduled_time_utc"], "2026-11-06T13:30:00Z")

    def test_duplicate_event_is_returned_once(self):
        duplicated = FIXED_BLS_ICS.replace(
            "END:VCALENDAR",
            """BEGIN:VEVENT
UID:cpi-20260812@bls.gov
DTSTART;TZID=US-Eastern:20260812T083000
SUMMARY:Consumer Price Index
STATUS:CONFIRMED
END:VEVENT
END:VCALENDAR""",
        )

        events = parse_bls_ics(duplicated, "2026-08-03T09:28:10Z")

        self.assertEqual([event["event_code"] for event in events].count("cpi"), 1)

    def test_rejects_invalid_calendar_structure(self):
        with self.assertRaisesRegex(
            BlsCalendarParseError,
            "BLS_INVALID_CALENDAR_STRUCTURE",
        ):
            parse_bls_ics("<html>blocked</html>", "2026-08-03T09:28:10Z")

    def test_rejects_whitelisted_event_without_time(self):
        calendar = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Employment Situation
END:VEVENT
END:VCALENDAR
"""
        with self.assertRaisesRegex(
            BlsCalendarParseError,
            "BLS_MISSING_DTSTART:employment",
        ):
            parse_bls_ics(calendar, "2026-08-03T09:28:10Z")


if __name__ == "__main__":
    unittest.main()
