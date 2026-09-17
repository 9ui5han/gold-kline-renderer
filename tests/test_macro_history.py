import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from app.macro_history import MacroHistoryStore


class MacroHistoryStoreTests(unittest.TestCase):
    def test_upserts_events_and_prunes_records_older_than_thirty_days(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroHistoryStore(Path(directory) / "macro-history.sqlite3")
            now = datetime(2026, 9, 16, tzinfo=timezone.utc)
            store.record_events("bls", [{
                "event_id": "old",
                "event_code": "cpi",
                "title": "Old CPI",
                "scheduled_time_utc": "2026-08-01T12:30:00Z",
            }], now=now)
            store.record_events("bls", [{
                "event_id": "new",
                "event_code": "cpi",
                "title": "New CPI",
                "scheduled_time_utc": "2026-09-15T12:30:00Z",
            }], now=now)
            with closing(sqlite3.connect(store.path)) as connection:
                with connection:
                    events = connection.execute(
                        "SELECT event_id, title FROM macro_events ORDER BY event_id"
                    ).fetchall()

        self.assertEqual(events, [("new", "New CPI")])

    def test_records_source_check_without_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroHistoryStore(Path(directory) / "macro-history.sqlite3")
            store.record_source_check({
                "checked_at_utc": "2026-09-16T00:00:00Z",
                "data_status": "partial",
                "source_count": 2,
                "valid_source_count": 1,
                "sources": [{
                    "source": "bls",
                    "http_status": 200,
                    "content_type": "text/calendar",
                    "response_bytes": 12,
                    "elapsed_ms": 44,
                    "reachable": True,
                    "structure_valid": True,
                    "error_code": "",
                    "url": "https://private.example.test",
                    "response_sample": "secret body",
                    "error_message": "Bearer secret-token",
                }],
            }, trigger="public_summary")

            with closing(sqlite3.connect(store.path)) as connection:
                with connection:
                    columns = {
                        row[1] for row in connection.execute(
                            "PRAGMA table_info(source_check_items)"
                        )
                    }
                    row = connection.execute(
                        "SELECT source, http_status, error_code FROM source_check_items"
                    ).fetchone()

        self.assertEqual(row, ("bls", 200, ""))
        self.assertNotIn("url", columns)
        self.assertNotIn("response_sample", columns)
        self.assertNotIn("error_message", columns)

    def test_lists_recent_events_newest_first_with_description(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroHistoryStore(Path(directory) / "macro-history.sqlite3")
            now = datetime(2026, 9, 16, tzinfo=timezone.utc)
            store.record_events("fed", [{
                "event_id": "fomc-1",
                "event_code": "fomc",
                "title": "FOMC Meeting",
                "description": "Federal Reserve policy meeting",
                "scheduled_date": "2026-09-16",
            }], now=now)
            store.record_events("bls", [{
                "event_id": "cpi-1",
                "event_code": "cpi",
                "title": "CPI Release",
                "description": "Consumer inflation release",
                "scheduled_time_utc": "2026-09-15T12:30:00Z",
            }], now=now)
            store.record_events("bea", [{
                "event_id": "pce-future",
                "event_code": "pce",
                "title": "Future PCE Release",
                "scheduled_date": "2026-09-20",
            }], now=now)

            events = store.list_events(now=now)

        self.assertEqual([item["event_id"] for item in events], ["fomc-1", "cpi-1"])
        self.assertEqual(events[0]["description"], "Federal Reserve policy meeting")

    def test_get_event_returns_saved_detail_by_source_and_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroHistoryStore(Path(directory) / "macro-history.sqlite3")
            store.record_events("fed", [{
                "event_id": "fomc-1",
                "event_code": "fomc",
                "title": "FOMC Meeting",
                "description": "Policy meeting",
                "scheduled_time_utc": "2026-09-16T18:00:00Z",
                "scheduled_date": "2026-09-16",
                "official_url": "https://www.federalreserve.gov/fomc.htm",
                "status": "scheduled",
            }], now=datetime(2026, 9, 16, tzinfo=timezone.utc))
            event = store.get_event("fed", "fomc-1")

        self.assertEqual(event["title"], "FOMC Meeting")
        self.assertEqual(event["official_url"], "https://www.federalreserve.gov/fomc.htm")

    def test_record_events_uses_source_url_as_official_article_url(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MacroHistoryStore(Path(directory) / "macro-history.sqlite3")
            store.record_events("fed_speeches", [{
                "event_id": "waller-1",
                "event_code": "fed_waller_speech",
                "title": "Waller, Economic Outlook",
                "source_url": "https://www.federalreserve.gov/newsevents/speech/waller.htm",
                "scheduled_time_utc": "2026-09-16T18:00:00Z",
            }])
            event = store.get_event("fed_speeches", "waller-1")

        self.assertEqual(
            event["official_url"],
            "https://www.federalreserve.gov/newsevents/speech/waller.htm",
        )


if __name__ == "__main__":
    unittest.main()
