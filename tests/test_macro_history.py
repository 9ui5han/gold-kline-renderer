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


if __name__ == "__main__":
    unittest.main()
