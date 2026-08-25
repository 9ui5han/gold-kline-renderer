import unittest

from app.fed_speech_calendar import FedSpeechParseError, parse_fed_fomc_speeches_rss
from app.us_official_speech_calendar import (
    OfficialSpeechParseError,
    parse_nyfed_williams_speeches,
    parse_state_diplomatic_releases,
    parse_whitehouse_trump_remarks,
)


FETCHED_AT = "2026-08-25T01:00:00Z"


class OfficialSpeechSourceTests(unittest.TestCase):
    def test_fed_all_speeches_rss_tracks_only_requested_fomc_speakers(self):
        events = parse_fed_fomc_speeches_rss(
            """<rss><channel>
            <item><title>Warsh, Economic Outlook</title><link>https://www.federalreserve.gov/newsevents/speech/warsh.htm</link><pubDate>Mon, 24 Aug 2026 14:00:00 GMT</pubDate></item>
            <item><title>Jefferson, Monetary Policy and Inflation</title><link>https://www.federalreserve.gov/newsevents/speech/jefferson.htm</link><pubDate>Mon, 24 Aug 2026 15:00:00 GMT</pubDate></item>
            <item><title>Waller, Financial Stability</title><link>https://www.federalreserve.gov/newsevents/speech/waller.htm</link><pubDate>Mon, 24 Aug 2026 16:00:00 GMT</pubDate></item>
            <item><title>Bowman, Monetary Policy and Financial Stability</title><link>https://www.federalreserve.gov/newsevents/speech/bowman.htm</link><pubDate>Mon, 24 Aug 2026 17:00:00 GMT</pubDate></item>
            <item><title>Powell, Economic Conditions</title><link>https://www.federalreserve.gov/newsevents/speech/powell.htm</link><pubDate>Mon, 24 Aug 2026 18:00:00 GMT</pubDate></item>
            <item><title>Bowman, Acceptance Remarks</title><link>https://www.federalreserve.gov/newsevents/speech/bowman-ceremonial.htm</link><pubDate>Mon, 24 Aug 2026 19:00:00 GMT</pubDate></item>
            <item><title>Cook, Economic Outlook</title><link>https://www.federalreserve.gov/newsevents/speech/cook.htm</link><pubDate>Mon, 24 Aug 2026 20:00:00 GMT</pubDate></item>
            </channel></rss>""",
            FETCHED_AT,
        )

        self.assertEqual(
            [event["event_code"] for event in events],
            [
                "fed_warsh_speech",
                "fed_jefferson_speech",
                "fed_waller_speech",
                "fed_bowman_speech",
                "fed_speech",
            ],
        )
        self.assertEqual(events[0]["speaker"], "Kevin Warsh")
        self.assertEqual(events[0]["status"], "published")
        self.assertEqual(events[0]["time_basis"], "publication_time")

    def test_fed_source_rejects_external_links_and_changed_tracked_title_format(self):
        with self.assertRaisesRegex(FedSpeechParseError, "URL_INVALID"):
            parse_fed_fomc_speeches_rss(
                """<rss><channel><item><title>Warsh, Economic Outlook</title><link>https://example.com/warsh</link><pubDate>Mon, 24 Aug 2026 14:00:00 GMT</pubDate></item></channel></rss>""",
                FETCHED_AT,
            )
        with self.assertRaisesRegex(FedSpeechParseError, "TRACKED_SPEAKER_FORMAT_INVALID"):
            parse_fed_fomc_speeches_rss(
                """<rss><channel><item><title>Warsh: Economic Outlook</title><link>https://www.federalreserve.gov/newsevents/speech/warsh.htm</link><pubDate>Mon, 24 Aug 2026 14:00:00 GMT</pubDate></item></channel></rss>""",
                FETCHED_AT,
            )

    def test_nyfed_williams_page_is_preserved_as_date_only(self):
        events = parse_nyfed_williams_speeches(
            """<table><tr><td class="dirColL"><div>Jul 15, 2026</div></td>
            <td><a href="/newsevents/speeches/2026/wil260715" class="paraHeader">Williams: Stability of Thy Times</a></td></tr></table>""",
            FETCHED_AT,
        )

        self.assertEqual(events[0]["event_code"], "nyfed_williams_speech")
        self.assertEqual(events[0]["scheduled_time_utc"], None)
        self.assertEqual(events[0]["scheduled_date"], "2026-07-15")
        self.assertEqual(events[0]["time_precision"], "date_only")

    def test_nyfed_williams_title_format_drift_fails_closed(self):
        with self.assertRaisesRegex(OfficialSpeechParseError, "TITLE_FORMAT_INVALID"):
            parse_nyfed_williams_speeches(
                """<table><tr><td class="dirColL">Jul 15, 2026</td>
                <td><a href="/newsevents/speeches/2026/wil260715" class="paraHeader">John Williams on Monetary Policy</a></td></tr></table>""",
                FETCHED_AT,
            )

    def test_whitehouse_remarks_require_a_macro_or_geopolitical_topic(self):
        events = parse_whitehouse_trump_remarks(
            """<ul>
            <li class="playlist_term-remarks-from-president-trump"><a href="/videos/roundtable/" title="American Mining Roundtable">American Mining Roundtable</a><time datetime="2026-08-17T17:30:56+00:00"></time></li>
            <li class="playlist_term-remarks-from-president-trump"><a href="/videos/reception/" title="President Trump Participates in a Team USA Reception">Reception</a><time datetime="2026-08-16T17:30:56+00:00"></time></li>
            </ul>""",
            FETCHED_AT,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_code"], "whitehouse_trump_remarks")
        self.assertEqual(events[0]["speaker"], "Donald J. Trump")
        self.assertEqual(events[0]["scheduled_time_utc"], "2026-08-17T17:30:56Z")

    def test_whitehouse_unclosed_remark_item_fails_closed(self):
        with self.assertRaisesRegex(OfficialSpeechParseError, "ITEM_STRUCTURE_INVALID"):
            parse_whitehouse_trump_remarks(
                """<ul><li class="playlist_term-remarks-from-president-trump">
                <a href="/videos/roundtable/" title="American Mining Roundtable">American Mining Roundtable</a>
                <time datetime="2026-08-17T17:30:56+00:00"></time>""",
                FETCHED_AT,
            )

    def test_state_api_requires_role_speech_and_topic(self):
        events = parse_state_diplomatic_releases(
            [
                {
                    "date_gmt": "2026-08-24T17:00:00",
                    "link": "https://www.state.gov/releases/remarks-rubio/",
                    "title": {"rendered": "Remarks by Secretary Rubio on Energy Sanctions"},
                    "content": {"rendered": "<p>U.S. energy and Iran policy.</p>"},
                },
                {
                    "date_gmt": "2026-08-24T17:15:00",
                    "link": "https://www.state.gov/releases/rubio-call/",
                    "title": {"rendered": "Secretary Rubio’s Call with Foreign Minister"},
                    "content": {"rendered": "<p>Trade policy was discussed.</p>"},
                },
                {
                    "date_gmt": "2026-08-24T17:30:00",
                    "link": "https://www.state.gov/releases/rubio-reception/",
                    "title": {"rendered": "Remarks by Secretary Rubio at a Reception on Trade"},
                    "content": {"rendered": "<p>Trade policy was discussed.</p>"},
                },
            ],
            FETCHED_AT,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["speaker"], "Marco Rubio")
        self.assertEqual(events[0]["event_code"], "state_diplomatic_official_statement")
        self.assertEqual(events[0]["scheduled_time_utc"], "2026-08-24T17:00:00Z")

    def test_state_api_fails_closed_when_the_response_is_not_an_array(self):
        with self.assertRaisesRegex(OfficialSpeechParseError, "JSON_ARRAY_REQUIRED"):
            parse_state_diplomatic_releases({}, FETCHED_AT)


if __name__ == "__main__":
    unittest.main()
