"""Parse the official BLS release calendar into high-impact US events.

Only the three releases approved for the first macro phase are emitted.  The
calendar provides release times, not values or market direction.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


BLS_ICS_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
BLS_DEFAULT_TIMEZONE = "America/New_York"

_EVENT_CODES = {
    "consumer price index": "cpi",
    "producer price index": "ppi",
    "employment situation": "employment",
}
_TIMEZONE_ALIASES = {
    "america/new_york": BLS_DEFAULT_TIMEZONE,
    "us/eastern": BLS_DEFAULT_TIMEZONE,
    "us-eastern": BLS_DEFAULT_TIMEZONE,
    "eastern standard time": BLS_DEFAULT_TIMEZONE,
}
_STATUS_MAP = {
    "CONFIRMED": "scheduled",
    "TENTATIVE": "tentative",
    "CANCELLED": "cancelled",
}


class BlsCalendarParseError(ValueError):
    """Raised when a whitelisted BLS event cannot be parsed safely."""


def _unfold_lines(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    unfolded: list[str] = []
    for line in normalized.split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    return unfolded


def _parse_property(line: str) -> tuple[str, dict[str, str], str] | None:
    if ":" not in line:
        return None
    raw_name, value = line.split(":", 1)
    parts = raw_name.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, param_value = part.split("=", 1)
        params[key.strip().upper()] = param_value.strip().strip('"')
    return name, params, value.strip()


def _event_blocks(lines: list[str]) -> list[dict[str, tuple[dict[str, str], str]]]:
    events: list[dict[str, tuple[dict[str, str], str]]] = []
    current: dict[str, tuple[dict[str, str], str]] | None = None
    for line in lines:
        marker = line.strip().upper()
        if marker == "BEGIN:VEVENT":
            current = {}
            continue
        if marker == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
            continue
        if current is None:
            continue
        parsed = _parse_property(line)
        if parsed is None:
            continue
        name, params, value = parsed
        current.setdefault(name, (params, value))
    return events


def _unescape_text(value: str) -> str:
    return (
        value.replace("\\n", " ")
        .replace("\\N", " ")
        .replace("\\,", ",")
        .replace("\\;", ";")
        .replace("\\\\", "\\")
        .strip()
    )


def _event_code(title: str) -> str | None:
    normalized = re.sub(r"\s+", " ", title).strip().casefold()
    return _EVENT_CODES.get(normalized)


def _timezone_for(tzid: str | None) -> ZoneInfo:
    requested = (tzid or BLS_DEFAULT_TIMEZONE).strip()
    zone_name = _TIMEZONE_ALIASES.get(requested.casefold(), requested)
    try:
        return ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise BlsCalendarParseError(
            f"BLS_UNSUPPORTED_TIMEZONE:{requested}"
        ) from exc


def _parse_dtstart(
    params: dict[str, str],
    raw_value: str,
) -> tuple[str | None, str, str]:
    value = raw_value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(
        r"\d{8}", value
    ):
        try:
            scheduled_date = datetime.strptime(value, "%Y%m%d").date().isoformat()
        except ValueError as exc:
            raise BlsCalendarParseError("BLS_INVALID_DATE") from exc
        return None, scheduled_date, "date_only"

    is_utc = value.endswith("Z")
    compact_value = value[:-1] if is_utc else value
    parsed_local: datetime | None = None
    for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
        try:
            parsed_local = datetime.strptime(compact_value, pattern)
            break
        except ValueError:
            continue
    if parsed_local is None:
        raise BlsCalendarParseError("BLS_INVALID_DATETIME")

    if is_utc:
        scheduled_utc = parsed_local.replace(tzinfo=timezone.utc)
    else:
        scheduled_utc = parsed_local.replace(
            tzinfo=_timezone_for(params.get("TZID"))
        ).astimezone(timezone.utc)

    return (
        scheduled_utc.isoformat().replace("+00:00", "Z"),
        scheduled_utc.date().isoformat(),
        "exact",
    )


def _event_id(event_code: str, title: str, raw_dtstart: str, uid: str) -> str:
    identity = "|".join((event_code, title, raw_dtstart, uid))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"bls-{event_code}-{digest}"


def parse_bls_ics(
    ics_text: str,
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    """Return normalized CPI, PPI, and Employment Situation release events."""

    lines = _unfold_lines(ics_text)
    markers = {line.strip().upper() for line in lines}
    if "BEGIN:VCALENDAR" not in markers or "END:VCALENDAR" not in markers:
        raise BlsCalendarParseError("BLS_INVALID_CALENDAR_STRUCTURE")

    parsed_events: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for event in _event_blocks(lines):
        summary_entry = event.get("SUMMARY")
        if summary_entry is None:
            continue
        title = _unescape_text(summary_entry[1])
        event_code = _event_code(title)
        if event_code is None:
            continue

        dtstart_entry = event.get("DTSTART")
        if dtstart_entry is None:
            raise BlsCalendarParseError(f"BLS_MISSING_DTSTART:{event_code}")
        dtstart_params, raw_dtstart = dtstart_entry
        scheduled_time_utc, scheduled_date, precision = _parse_dtstart(
            dtstart_params,
            raw_dtstart,
        )

        uid = _unescape_text(event.get("UID", ({}, ""))[1])
        event_id = _event_id(event_code, title, raw_dtstart, uid)
        if event_id in seen_ids:
            continue
        seen_ids.add(event_id)

        raw_status = event.get("STATUS", ({}, "CONFIRMED"))[1].upper()
        status = _STATUS_MAP.get(raw_status, "unknown")
        parsed_events.append(
            {
                "event_id": event_id,
                "event_code": event_code,
                "title": title,
                "country": "US",
                "currency": "USD",
                "scheduled_time_utc": scheduled_time_utc,
                "scheduled_date": scheduled_date,
                "time_precision": precision,
                "status": status,
                "impact": "high",
                "source": "bls",
                "source_url": BLS_ICS_URL,
                "source_fetched_at_utc": source_fetched_at_utc,
            }
        )

    return sorted(
        parsed_events,
        key=lambda item: (
            item["scheduled_date"],
            item["scheduled_time_utc"] or "",
            item["event_code"],
        ),
    )
