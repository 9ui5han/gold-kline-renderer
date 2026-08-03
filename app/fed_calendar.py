"""Parse official FOMC meeting dates into statement and press events.

The Federal Reserve publishes the policy statement at 2:00 p.m. Eastern Time
on the second day of each regular meeting and the Chair's news conference at
2:30 p.m. Eastern Time.  These events provide timing only, never direction.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from zoneinfo import ZoneInfo


FED_FOMC_CALENDAR_URL = (
    "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
)
FED_EASTERN_TIMEZONE = ZoneInfo("America/New_York")

_MONTH_NUMBERS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
_MONTH_PATTERN = re.compile(
    r"\b(?:" + "|".join(sorted(_MONTH_NUMBERS, key=len, reverse=True)) + r")\b",
    re.I,
)
_EVENT_TIMES = (
    ("policy_statement", "FOMC Policy Statement", 14, 0),
    ("press_conference", "FOMC Press Conference", 14, 30),
)


class FedCalendarParseError(ValueError):
    """Raised when the official FOMC calendar structure cannot be trusted."""


class _FomcHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.current_year: int | None = None
        self.row_div_depth = 0
        self.field_name: str | None = None
        self.field_div_depth = 0
        self.row: dict[str, Any] | None = None
        self.rows: list[dict[str, Any]] = []
        self.found_calendar_heading = False

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        raw_class = next((value or "" for key, value in attrs if key == "class"), "")
        return set(raw_class.split())

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "div":
            return
        classes = self._classes(attrs)
        if self.row_div_depth == 0 and "fomc-meeting" in classes:
            self.row_div_depth = 1
            self.row = {
                "year": self.current_year,
                "month_parts": [],
                "date_parts": [],
            }
            return
        if self.row_div_depth == 0:
            return

        self.row_div_depth += 1
        if "fomc-meeting__month" in classes:
            self.field_name = "month_parts"
            self.field_div_depth = self.row_div_depth
        elif "fomc-meeting__date" in classes:
            self.field_name = "date_parts"
            self.field_div_depth = self.row_div_depth

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "div" or self.row_div_depth == 0:
            return
        if self.field_name and self.row_div_depth == self.field_div_depth:
            self.field_name = None
            self.field_div_depth = 0
        self.row_div_depth -= 1
        if self.row_div_depth == 0 and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data: str) -> None:
        year_match = re.search(r"\b(20\d{2})\s+FOMC\s+Meetings\b", data, re.I)
        if year_match:
            self.current_year = int(year_match.group(1))
            self.found_calendar_heading = True
        if self.row is not None and self.field_name and data:
            self.row[self.field_name].append(data)


def _meeting_end_date(year: int, month_text: str, date_text: str) -> datetime:
    month_names = [match.casefold() for match in _MONTH_PATTERN.findall(month_text)]
    day_numbers = [int(value) for value in re.findall(r"\d{1,2}", date_text)]
    if not month_names or not day_numbers:
        raise FedCalendarParseError("FED_INVALID_MEETING_DATE")

    start_month = _MONTH_NUMBERS[month_names[0]]
    decision_month = _MONTH_NUMBERS[month_names[-1]]
    start_day = day_numbers[0]
    decision_day = day_numbers[-1]
    decision_year = year

    if len(month_names) == 1 and decision_day < start_day:
        decision_month = start_month % 12 + 1
        if decision_month == 1:
            decision_year += 1
    elif len(month_names) > 1 and decision_month < start_month:
        decision_year += 1

    try:
        return datetime(decision_year, decision_month, decision_day)
    except ValueError as exc:
        raise FedCalendarParseError("FED_INVALID_MEETING_DATE") from exc


def _event_id(subtype: str, scheduled_time_utc: str) -> str:
    identity = f"fomc|{subtype}|{scheduled_time_utc}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"fed-fomc-{subtype}-{digest}"


def parse_fed_fomc_calendar(
    html_text: str,
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    """Return statement and press-conference events for each regular meeting."""

    if not isinstance(html_text, str) or not html_text.strip():
        raise FedCalendarParseError("FED_EMPTY_HTML")

    parser = _FomcHtmlParser()
    try:
        parser.feed(html_text)
        parser.close()
    except Exception as exc:
        raise FedCalendarParseError("FED_INVALID_HTML") from exc
    if not parser.found_calendar_heading or not parser.rows:
        raise FedCalendarParseError("FED_CALENDAR_STRUCTURE_MISSING")

    events: list[dict[str, Any]] = []
    seen_times: set[tuple[str, str]] = set()
    for row in parser.rows:
        year = row.get("year")
        if not isinstance(year, int):
            raise FedCalendarParseError("FED_MEETING_YEAR_MISSING")
        month_text = " ".join(row["month_parts"]).strip()
        date_text = " ".join(row["date_parts"]).strip()
        if "notation vote" in date_text.casefold():
            continue
        meeting_end = _meeting_end_date(year, month_text, date_text)

        for subtype, title, hour, minute in _EVENT_TIMES:
            scheduled_utc = meeting_end.replace(
                hour=hour,
                minute=minute,
                tzinfo=FED_EASTERN_TIMEZONE,
            ).astimezone(timezone.utc)
            scheduled_time_utc = scheduled_utc.isoformat().replace("+00:00", "Z")
            identity = (subtype, scheduled_time_utc)
            if identity in seen_times:
                continue
            seen_times.add(identity)
            events.append(
                {
                    "event_id": _event_id(subtype, scheduled_time_utc),
                    "event_code": "fomc",
                    "event_subtype": subtype,
                    "title": title,
                    "country": "US",
                    "currency": "USD",
                    "scheduled_time_utc": scheduled_time_utc,
                    "scheduled_date": scheduled_utc.date().isoformat(),
                    "time_precision": "exact",
                    "status": "scheduled",
                    "impact": "high",
                    "source": "fed",
                    "source_url": FED_FOMC_CALENDAR_URL,
                    "source_fetched_at_utc": source_fetched_at_utc,
                }
            )

    return sorted(events, key=lambda item: item["scheduled_time_utc"])
