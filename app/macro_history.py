"""Durable, rolling thirty-day storage for macro events and source checks."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


RETENTION_DAYS = 30


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class MacroHistoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            with connection:
                connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS macro_events (
                    source TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    event_code TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    scheduled_time_utc TEXT NOT NULL,
                    scheduled_date TEXT NOT NULL,
                    time_precision TEXT NOT NULL,
                    official_url TEXT NOT NULL,
                    status TEXT NOT NULL,
                    first_seen_at_utc TEXT NOT NULL,
                    last_seen_at_utc TEXT NOT NULL,
                    article_body TEXT NOT NULL DEFAULT '',
                    article_fetched_at_utc TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (source, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_macro_events_schedule
                    ON macro_events(scheduled_time_utc, scheduled_date);
                CREATE TABLE IF NOT EXISTS source_checks (
                    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    checked_at_utc TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    data_status TEXT NOT NULL,
                    source_count INTEGER NOT NULL,
                    valid_source_count INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_check_items (
                    check_id INTEGER NOT NULL REFERENCES source_checks(check_id)
                        ON DELETE CASCADE,
                    source TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    content_type TEXT NOT NULL,
                    response_bytes INTEGER NOT NULL,
                    elapsed_ms INTEGER NOT NULL,
                    reachable INTEGER NOT NULL,
                    structure_valid INTEGER NOT NULL,
                    error_code TEXT NOT NULL,
                    PRIMARY KEY (check_id, source)
                );
                CREATE INDEX IF NOT EXISTS idx_source_checks_time
                    ON source_checks(checked_at_utc);
                """
            )
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(macro_events)")
                }
                if "description" not in columns:
                    connection.execute(
                        "ALTER TABLE macro_events ADD COLUMN description TEXT NOT NULL DEFAULT ''"
                    )
                if "article_body" not in columns:
                    connection.execute("ALTER TABLE macro_events ADD COLUMN article_body TEXT NOT NULL DEFAULT ''")
                if "article_fetched_at_utc" not in columns:
                    connection.execute("ALTER TABLE macro_events ADD COLUMN article_fetched_at_utc TEXT NOT NULL DEFAULT ''")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _event_id(event: dict[str, Any]) -> str:
        value = str(event.get("event_id") or "").strip()
        if value:
            return value
        identity = json.dumps(
            {
                "event_code": event.get("event_code"),
                "title": event.get("title"),
                "scheduled_time_utc": event.get("scheduled_time_utc"),
                "scheduled_date": event.get("scheduled_date"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return "fallback-" + hashlib.sha256(identity.encode()).hexdigest()

    def _prune(self, connection: sqlite3.Connection, now: datetime) -> None:
        cutoff = _utc_text(now - timedelta(days=RETENTION_DAYS))
        cutoff_date = (now - timedelta(days=RETENTION_DAYS)).date().isoformat()
        connection.execute(
            "DELETE FROM macro_events WHERE "
            "(scheduled_time_utc <> '' AND scheduled_time_utc < ?) OR "
            "(scheduled_time_utc = '' AND scheduled_date <> '' AND scheduled_date < ?)",
            (cutoff, cutoff_date),
        )
        connection.execute(
            "DELETE FROM source_checks WHERE checked_at_utc < ?",
            (cutoff,),
        )

    def record_events(
        self,
        source: str,
        events: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> None:
        checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        checked_text = _utc_text(checked_at)
        source_text = str(source or "").strip()
        with closing(self._connect()) as connection:
            with connection:
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    scheduled_time = str(event.get("scheduled_time_utc") or "").strip()
                    scheduled_date = str(event.get("scheduled_date") or "").strip()
                    connection.execute(
                    """INSERT INTO macro_events(
                        source,event_id,event_code,title,description,scheduled_time_utc,
                        scheduled_date,time_precision,official_url,status,
                        first_seen_at_utc,last_seen_at_utc
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source,event_id) DO UPDATE SET
                        event_code=excluded.event_code,
                        title=excluded.title,
                        description=excluded.description,
                        scheduled_time_utc=excluded.scheduled_time_utc,
                        scheduled_date=excluded.scheduled_date,
                        time_precision=excluded.time_precision,
                        official_url=excluded.official_url,
                        status=excluded.status,
                        last_seen_at_utc=excluded.last_seen_at_utc
                    """,
                        (
                        source_text,
                        self._event_id(event),
                        str(event.get("event_code") or "").strip(),
                        str(event.get("title") or "").strip()[:500],
                        str(
                            event.get("description")
                            or event.get("summary")
                            or "官方日历事件，来源未提供详细简介。"
                        ).strip()[:1000],
                        scheduled_time,
                        scheduled_date,
                        "exact" if scheduled_time else "date_only",
                        str(
                            event.get("official_url")
                            or event.get("source_url")
                            or ""
                        ).strip()[:1000],
                        str(event.get("status") or "scheduled").strip()[:40],
                        checked_text,
                        checked_text,
                        ),
                    )
                self._prune(connection, checked_at)

    def list_events(
        self,
        *,
        now: datetime | None = None,
        days: int = RETENTION_DAYS,
    ) -> list[dict[str, Any]]:
        checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        days = max(1, min(RETENTION_DAYS, int(days)))
        cutoff = _utc_text(checked_at - timedelta(days=days))
        cutoff_date = (checked_at - timedelta(days=days)).date().isoformat()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT source,event_id,event_code,title,description,
                   scheduled_time_utc,scheduled_date,time_precision,official_url,status,
                   first_seen_at_utc,last_seen_at_utc,article_body,article_fetched_at_utc
                   FROM macro_events
                   WHERE (scheduled_time_utc <> '' AND scheduled_time_utc >= ?
                          AND scheduled_time_utc <= ?)
                      OR (scheduled_time_utc = '' AND scheduled_date >= ?
                          AND scheduled_date <= ?)
                   ORDER BY CASE WHEN scheduled_time_utc <> ''
                                 THEN scheduled_time_utc ELSE scheduled_date END DESC,
                            event_id ASC""",
                (cutoff, _utc_text(checked_at), cutoff_date, checked_at.date().isoformat()),
            ).fetchall()
        fields = (
            "source", "event_id", "event_code", "title", "description",
            "scheduled_time_utc", "scheduled_date", "time_precision", "official_url", "status",
            "first_seen_at_utc", "last_seen_at_utc",
        )
        return [dict(zip(fields, row)) for row in rows]

    def get_event(self, source: str, event_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """SELECT source,event_id,event_code,title,description,
                   scheduled_time_utc,scheduled_date,time_precision,official_url,status,
                   first_seen_at_utc,last_seen_at_utc
                   FROM macro_events WHERE source = ? AND event_id = ?""",
                (str(source).strip(), str(event_id).strip()),
            ).fetchone()
        if row is None:
            return None
        fields = (
            "source", "event_id", "event_code", "title", "description",
            "scheduled_time_utc", "scheduled_date", "time_precision", "official_url", "status",
            "first_seen_at_utc", "last_seen_at_utc", "article_body", "article_fetched_at_utc",
        )
        return dict(zip(fields, row))

    def save_article(self, source: str, event_id: str, body: str, *, fetched_at: datetime | None = None) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "UPDATE macro_events SET article_body = ?, article_fetched_at_utc = ? WHERE source = ? AND event_id = ?",
                    (str(body or "")[:30000], _utc_text(fetched_at or datetime.now(timezone.utc)), str(source).strip(), str(event_id).strip()),
                )

    def record_source_check(
        self,
        status: dict[str, Any],
        *,
        trigger: str,
        now: datetime | None = None,
    ) -> None:
        checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        checked_text = str(status.get("checked_at_utc") or _utc_text(checked_at))
        with closing(self._connect()) as connection:
            with connection:
                cursor = connection.execute(
                "INSERT INTO source_checks(checked_at_utc,trigger,data_status,source_count,valid_source_count) VALUES(?,?,?,?,?)",
                (
                    checked_text,
                    str(trigger or "unknown")[:40],
                    str(status.get("data_status") or "unavailable")[:30],
                    int(status.get("source_count") or 0),
                    int(status.get("valid_source_count") or 0),
                ),
            )
                check_id = cursor.lastrowid
                for item in status.get("sources") or []:
                    if not isinstance(item, dict):
                        continue
                    connection.execute(
                    """INSERT INTO source_check_items(
                        check_id,source,http_status,content_type,response_bytes,
                        elapsed_ms,reachable,structure_valid,error_code
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (
                        check_id,
                        str(item.get("source") or "").strip(),
                        int(item.get("http_status") or 0),
                        str(item.get("content_type") or "")[:120],
                        int(item.get("response_bytes") or 0),
                        int(item.get("elapsed_ms") or 0),
                        int(item.get("reachable") is True),
                        int(item.get("structure_valid") is True),
                        str(item.get("error_code") or "")[:80],
                        ),
                    )
                self._prune(connection, checked_at)
