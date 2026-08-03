"""Parse BEA release dates for the monthly PCE and core PCE release.

BEA publishes both indexes in the Personal Income and Outlays release.  This
module emits calendar context only; it does not fetch values or infer direction.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any


BEA_RELEASE_DATES_URL = "https://apps.bea.gov/API/signup/release_dates.json"
BEA_PCE_RELEASE_NAME = "Personal Income and Outlays"


class BeaCalendarParseError(ValueError):
    """Raised when BEA's PCE release-date structure is unsafe to use."""


def _load_payload(payload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        raise BeaCalendarParseError("BEA_INVALID_PAYLOAD_TYPE")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BeaCalendarParseError("BEA_INVALID_JSON") from exc
    if not isinstance(parsed, dict):
        raise BeaCalendarParseError("BEA_INVALID_TOP_LEVEL_STRUCTURE")
    return parsed


def _parse_release_time(raw_value: str) -> tuple[str | None, str, str]:
    value = raw_value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            scheduled_date = datetime.strptime(value, "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise BeaCalendarParseError("BEA_INVALID_RELEASE_DATE") from exc
        return None, scheduled_date, "date_only"

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BeaCalendarParseError("BEA_INVALID_RELEASE_DATETIME") from exc
    if parsed.tzinfo is None:
        raise BeaCalendarParseError("BEA_RELEASE_TIMEZONE_REQUIRED")

    scheduled_utc = parsed.astimezone(timezone.utc)
    return (
        scheduled_utc.isoformat().replace("+00:00", "Z"),
        scheduled_utc.date().isoformat(),
        "exact",
    )


def _event_id(schedule_identity: str) -> str:
    identity = f"pce|{BEA_PCE_RELEASE_NAME}|{schedule_identity}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"bea-pce-{digest}"


def parse_bea_release_dates(
    payload: str | dict[str, Any],
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    """Return one normalized PCE event per Personal Income and Outlays date."""

    data = _load_payload(payload)
    if BEA_PCE_RELEASE_NAME not in data:
        raise BeaCalendarParseError("BEA_PCE_RELEASE_MISSING")

    release = data[BEA_PCE_RELEASE_NAME]
    if not isinstance(release, dict):
        raise BeaCalendarParseError("BEA_PCE_RELEASE_INVALID")
    release_dates = release.get("release_dates")
    if not isinstance(release_dates, list):
        raise BeaCalendarParseError("BEA_RELEASE_DATES_INVALID")

    events: list[dict[str, Any]] = []
    seen_times: set[str] = set()
    for raw_value in release_dates:
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise BeaCalendarParseError("BEA_RELEASE_DATE_VALUE_INVALID")
        normalized_raw_value = raw_value.strip()
        scheduled_time_utc, scheduled_date, precision = _parse_release_time(
            normalized_raw_value
        )
        schedule_identity = scheduled_time_utc or f"date:{scheduled_date}"
        if schedule_identity in seen_times:
            continue
        seen_times.add(schedule_identity)
        events.append(
            {
                "event_id": _event_id(schedule_identity),
                "event_code": "pce",
                "title": BEA_PCE_RELEASE_NAME,
                "country": "US",
                "currency": "USD",
                "scheduled_time_utc": scheduled_time_utc,
                "scheduled_date": scheduled_date,
                "time_precision": precision,
                "status": "scheduled",
                "impact": "high",
                "source": "bea",
                "source_url": BEA_RELEASE_DATES_URL,
                "source_fetched_at_utc": source_fetched_at_utc,
            }
        )

    return sorted(
        events,
        key=lambda item: (
            item["scheduled_date"],
            item["scheduled_time_utc"] or "",
        ),
    )
