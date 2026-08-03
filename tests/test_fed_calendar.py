import unittest

from app.fed_calendar import FedCalendarParseError, parse_fed_fomc_calendar


FIXED_FED_HTML = """<html><body>
<div class="panel panel-default">
  <div class="panel-heading"><h4><a>2026 FOMC Meetings</a></h4></div>
  <div class="row fomc-meeting">
    <div class="fomc-meeting__month"><strong>January</strong></div>
    <div class="fomc-meeting__date">27-28</div>
  </div>
  <div class="fomc-meeting--shaded row fomc-meeting">
    <div class="fomc-meeting__month"><strong>March</strong></div>
    <div class="fomc-meeting__date">17-18*</div>
  </div>
  <div class="row fomc-meeting">
    <div class="fomc-meeting__month"><strong>September</strong></div>
    <div class="fomc-meeting__date">15-16*</div>
  </div>
</div>
</body></html>"""


class FedCalendarTests(unittest.TestCase):
    def test_emits_statement_and_press_conference_per_meeting(self):
        events = parse_fed_fomc_calendar(
            FIXED_FED_HTML,
            "2026-08-03T09:28:10Z",
        )

        self.assertEqual(len(events), 6)
        self.assertEqual(
            [event["event_subtype"] for event in events[:2]],
            ["policy_statement", "press_conference"],
        )
        self.assertTrue(all(event["event_code"] == "fomc" for event in events))
        self.assertTrue(all(event["source"] == "fed" for event in events))
        self.assertNotIn("directional_bias", events[0])

    def test_converts_winter_and_summer_eastern_times_to_utc(self):
        events = parse_fed_fomc_calendar(
            FIXED_FED_HTML,
            "2026-08-03T09:28:10Z",
        )

        self.assertEqual(events[0]["scheduled_time_utc"], "2026-01-28T19:00:00Z")
        self.assertEqual(events[1]["scheduled_time_utc"], "2026-01-28T19:30:00Z")
        self.assertEqual(events[2]["scheduled_time_utc"], "2026-03-18T18:00:00Z")
        self.assertEqual(events[3]["scheduled_time_utc"], "2026-03-18T18:30:00Z")

    def test_uses_second_day_of_cross_month_meeting(self):
        html = """<h4>2027 FOMC Meetings</h4>
<div class="row fomc-meeting">
  <div class="fomc-meeting__month">Jan/Feb</div>
  <div class="fomc-meeting__date">31-1</div>
</div>"""

        events = parse_fed_fomc_calendar(html, "2026-08-03T09:28:10Z")

        self.assertEqual(events[0]["scheduled_time_utc"], "2027-02-01T19:00:00Z")
        self.assertEqual(events[0]["scheduled_date"], "2027-02-01")

    def test_excludes_notation_vote_row(self):
        html = """<h4>2025 FOMC Meetings</h4>
<div class="row fomc-meeting">
  <div class="fomc-meeting__month">August</div>
  <div class="fomc-meeting__date">22 (notation vote)</div>
</div>
<div class="row fomc-meeting">
  <div class="fomc-meeting__month">September</div>
  <div class="fomc-meeting__date">16-17*</div>
</div>"""

        events = parse_fed_fomc_calendar(html, "2026-08-03T09:28:10Z")

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["scheduled_date"], "2025-09-17")

    def test_duplicate_meeting_row_is_deduplicated(self):
        duplicated = FIXED_FED_HTML.replace(
            "</div>\n</body>",
            """<div class="row fomc-meeting">
    <div class="fomc-meeting__month">September</div>
    <div class="fomc-meeting__date">15-16*</div>
  </div>
</div>
</body>""",
        )

        events = parse_fed_fomc_calendar(
            duplicated,
            "2026-08-03T09:28:10Z",
        )

        september_events = [
            event for event in events if event["scheduled_date"] == "2026-09-16"
        ]
        self.assertEqual(len(september_events), 2)

    def test_rejects_page_without_fomc_calendar(self):
        with self.assertRaisesRegex(
            FedCalendarParseError,
            "FED_CALENDAR_STRUCTURE_MISSING",
        ):
            parse_fed_fomc_calendar(
                "<html><body>access denied</body></html>",
                "2026-08-03T09:28:10Z",
            )

    def test_rejects_invalid_meeting_date(self):
        html = """<h4>2026 FOMC Meetings</h4>
<div class="row fomc-meeting">
  <div class="fomc-meeting__month">September</div>
  <div class="fomc-meeting__date">TBA</div>
</div>"""
        with self.assertRaisesRegex(
            FedCalendarParseError,
            "FED_INVALID_MEETING_DATE",
        ):
            parse_fed_fomc_calendar(html, "2026-08-03T09:28:10Z")


if __name__ == "__main__":
    unittest.main()
