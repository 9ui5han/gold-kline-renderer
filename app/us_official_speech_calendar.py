"""Parse official U.S. speech, remarks, and diplomatic-release sources.

These parsers record an official publication timestamp or date.  They do not
infer what moved gold, and they deliberately exclude items without a clear
official speaker/role plus a macro or geopolitical topic.
"""

from __future__ import annotations

import hashlib
import html
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse


NYFED_WILLIAMS_SPEECHES_URL = "https://www.newyorkfed.org/newsevents/speeches/index"
WHITEHOUSE_TRUMP_REMARKS_URL = "https://www.whitehouse.gov/remarks/"
STATE_DIPLOMACY_RELEASES_URL = (
    "https://www.state.gov/wp-json/wp/v2/state_press_release?"
    "per_page=100&_fields=id,date_gmt,link,title,content,type"
)

_MACRO_OR_GEOPOLITICAL_KEYWORDS = (
    "economic",
    "economy",
    "inflation",
    "trade",
    "tariff",
    "sanction",
    "energy",
    "oil",
    "petroleum",
    "gas",
    "mining",
    "critical mineral",
    "currency",
    "dollar",
    "iran",
    "russia",
    "ukraine",
    "china",
    "taiwan",
    "middle east",
    "israel",
    "gaza",
    "nato",
    "nuclear",
    "military",
    "national security",
    "conflict",
    "ceasefire",
)
_CEREMONIAL_KEYWORDS = (
    "reception",
    "swearing-in",
    "graduation",
    "holiday",
    "memorial",
    "birthday",
)
_STATE_SPEECH_MARKERS = (
    "remarks",
    "statement",
    "press availability",
    "press conference",
    "address",
    "interview",
)


class OfficialSpeechParseError(ValueError):
    """Raised when an official source no longer has the required structure."""


def _clean_text(value: Any) -> str:
    raw = html.unescape(str(value or ""))
    without_tags = re.sub(r"<[^>]+>", " ", raw)
    return " ".join(without_tags.split())


def _is_official_url(url: str, domain: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    domain = domain.casefold()
    return parsed.scheme == "https" and (host == domain or host.endswith(f".{domain}"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]


def _published_event(
    *,
    event_id: str,
    event_code: str,
    event_subtype: str,
    title: str,
    speaker: str,
    source: str,
    source_url: str,
    source_fetched_at_utc: str,
    published: datetime | None = None,
    published_date: str = "",
) -> dict[str, Any]:
    if published is None and not published_date:
        raise OfficialSpeechParseError("OFFICIAL_SPEECH_TIME_MISSING")
    if published is not None:
        if published.tzinfo is None:
            raise OfficialSpeechParseError("OFFICIAL_SPEECH_TIMEZONE_MISSING")
        published_utc = published.astimezone(timezone.utc)
        scheduled_time_utc = published_utc.isoformat().replace("+00:00", "Z")
        scheduled_date = published_utc.date().isoformat()
        time_precision = "exact"
    else:
        scheduled_time_utc = None
        scheduled_date = published_date
        time_precision = "date_only"
    return {
        "event_id": event_id,
        "event_code": event_code,
        "event_subtype": event_subtype,
        "title": title,
        "speaker": speaker,
        "country": "US",
        "currency": "USD",
        "scheduled_time_utc": scheduled_time_utc,
        "scheduled_date": scheduled_date,
        "time_precision": time_precision,
        "time_basis": "publication_time",
        "status": "published",
        "impact": "high",
        "source": source,
        "source_url": source_url,
        "source_fetched_at_utc": source_fetched_at_utc,
    }


class _NyFedSpeechIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self._row: dict[str, str] | None = None
        self._in_date_cell = False
        self._in_speech_link = False
        self._link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self._row = {"date": "", "title": "", "href": ""}
            return
        if self._row is None:
            return
        if tag == "td" and "dirColL" in str(attributes.get("class") or ""):
            self._in_date_cell = True
        if tag == "a" and "paraHeader" in str(attributes.get("class") or ""):
            self._in_speech_link = True
            self._link_text = []
            self._row["href"] = str(attributes.get("href") or "").strip()

    def handle_data(self, data: str) -> None:
        if self._row is None:
            return
        if self._in_date_cell:
            self._row["date"] += f" {data}"
        if self._in_speech_link:
            self._link_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "td":
            self._in_date_cell = False
        elif tag == "a" and self._in_speech_link:
            if self._row is not None:
                self._row["title"] = " ".join(self._link_text).strip()
            self._in_speech_link = False
            self._link_text = []
        elif tag == "tr" and self._row is not None:
            if self._row["date"] or self._row["title"] or self._row["href"]:
                self.rows.append(self._row)
            self._row = None
            self._in_date_cell = False
            self._in_speech_link = False
            self._link_text = []


def parse_nyfed_williams_speeches(
    html_text: str,
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    if not isinstance(html_text, str) or not html_text.strip():
        raise OfficialSpeechParseError("NYFED_WILLIAMS_EMPTY_HTML")
    parser = _NyFedSpeechIndexParser()
    parser.feed(html_text)
    parser.close()
    if not parser.rows:
        raise OfficialSpeechParseError("NYFED_WILLIAMS_ITEMS_MISSING")

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in parser.rows:
        title = _clean_text(row.get("title"))
        date_text = _clean_text(row.get("date"))
        source_url = urljoin(NYFED_WILLIAMS_SPEECHES_URL, row.get("href") or "")
        if "williams" in title.casefold() and not title.startswith("Williams:"):
            raise OfficialSpeechParseError("NYFED_WILLIAMS_TITLE_FORMAT_INVALID")
        if not title.startswith("Williams:"):
            continue
        if not source_url or not _is_official_url(source_url, "newyorkfed.org"):
            raise OfficialSpeechParseError("NYFED_WILLIAMS_URL_INVALID")
        try:
            published_date = datetime.strptime(date_text, "%b %d, %Y").date().isoformat()
        except ValueError as exc:
            raise OfficialSpeechParseError("NYFED_WILLIAMS_DATE_INVALID") from exc
        identity = source_url.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        events.append(_published_event(
            event_id=f"nyfed-williams-speech-{_digest(identity)}",
            event_code="nyfed_williams_speech",
            event_subtype="williams_published_date_only",
            title=title,
            speaker="John C. Williams",
            source="nyfed_williams_speeches",
            source_url=source_url,
            source_fetched_at_utc=source_fetched_at_utc,
            published_date=published_date,
        ))
    return sorted(events, key=lambda event: (event["scheduled_date"], event["event_id"]))


class _WhiteHouseRemarksParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[dict[str, str]] = []
        self.item_count = 0
        self._record: dict[str, str] | None = None
        self._item_depth = 0
        self._capturing_anchor = False
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = str(attributes.get("class") or "")
        if tag == "li" and "playlist_term-remarks-from-president-trump" in classes:
            self.item_count += 1
            self._record = {"title": "", "href": "", "published": ""}
            self._item_depth = 1
            return
        if self._record is None:
            return
        if tag == "li":
            self._item_depth += 1
        elif tag == "a":
            href = str(attributes.get("href") or "").strip()
            title = _clean_text(attributes.get("title"))
            if href and not self._record["href"]:
                self._record["href"] = href
            if title and not self._record["title"] and not title.startswith("Published on "):
                self._record["title"] = title
            self._capturing_anchor = True
            self._anchor_text = []
        elif tag == "time":
            self._record["published"] = str(attributes.get("datetime") or "").strip()

    def handle_data(self, data: str) -> None:
        if self._capturing_anchor:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capturing_anchor:
            if self._record is not None and not self._record["title"]:
                self._record["title"] = _clean_text(" ".join(self._anchor_text))
            self._capturing_anchor = False
            self._anchor_text = []
        if tag == "li" and self._record is not None:
            self._item_depth -= 1
            if self._item_depth == 0:
                self.records.append(self._record)
                self._record = None


def _topic_qualified(text: str) -> bool:
    normalized = text.casefold()
    return (
        any(keyword in normalized for keyword in _MACRO_OR_GEOPOLITICAL_KEYWORDS)
        and not any(keyword in normalized for keyword in _CEREMONIAL_KEYWORDS)
    )


def parse_whitehouse_trump_remarks(
    html_text: str,
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    if not isinstance(html_text, str) or not html_text.strip():
        raise OfficialSpeechParseError("WHITEHOUSE_REMARKS_EMPTY_HTML")
    parser = _WhiteHouseRemarksParser()
    parser.feed(html_text)
    parser.close()
    if parser.item_count == 0:
        raise OfficialSpeechParseError("WHITEHOUSE_REMARKS_ITEMS_MISSING")
    if len(parser.records) != parser.item_count:
        raise OfficialSpeechParseError("WHITEHOUSE_REMARKS_ITEM_STRUCTURE_INVALID")

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in parser.records:
        title = _clean_text(record.get("title"))
        source_url = urljoin(WHITEHOUSE_TRUMP_REMARKS_URL, record.get("href") or "")
        if not title or not source_url or not record.get("published"):
            raise OfficialSpeechParseError("WHITEHOUSE_REMARKS_ITEM_INVALID")
        if not _is_official_url(source_url, "whitehouse.gov"):
            raise OfficialSpeechParseError("WHITEHOUSE_REMARKS_URL_INVALID")
        try:
            published = datetime.fromisoformat(str(record["published"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise OfficialSpeechParseError("WHITEHOUSE_REMARKS_TIME_INVALID") from exc
        if not _topic_qualified(title):
            continue
        identity = source_url.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        events.append(_published_event(
            event_id=f"whitehouse-trump-remarks-{_digest(identity)}",
            event_code="whitehouse_trump_remarks",
            event_subtype="trump_remarks_published",
            title=title,
            speaker="Donald J. Trump",
            source="whitehouse_remarks",
            source_url=source_url,
            source_fetched_at_utc=source_fetched_at_utc,
            published=published,
        ))
    return sorted(events, key=lambda event: event["scheduled_time_utc"])


def _state_speaker(title: str) -> str:
    lowered = title.casefold()
    if "secretary rubio" in lowered or "secretary marco rubio" in lowered or "marco rubio" in lowered:
        return "Marco Rubio"
    if "deputy secretary" in lowered:
        return "U.S. Department of State Deputy Secretary"
    if "under secretary" in lowered:
        return "U.S. Department of State Under Secretary"
    if "secretary of state" in lowered:
        return "U.S. Secretary of State"
    return ""


def parse_state_diplomatic_releases(
    payload: Any,
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        raise OfficialSpeechParseError("STATE_DIPLOMACY_JSON_ARRAY_REQUIRED")

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in payload:
        if not isinstance(item, dict):
            raise OfficialSpeechParseError("STATE_DIPLOMACY_ITEM_INVALID")
        title_data = item.get("title")
        title = _clean_text(title_data.get("rendered") if isinstance(title_data, dict) else "")
        source_url = str(item.get("link") or "").strip()
        published_gmt = str(item.get("date_gmt") or "").strip()
        content_data = item.get("content")
        content = _clean_text(
            content_data.get("rendered") if isinstance(content_data, dict) else ""
        )
        if not title or not source_url or not published_gmt:
            raise OfficialSpeechParseError("STATE_DIPLOMACY_ITEM_FIELDS_MISSING")
        if not _is_official_url(source_url, "state.gov"):
            raise OfficialSpeechParseError("STATE_DIPLOMACY_URL_INVALID")
        try:
            published = datetime.fromisoformat(published_gmt.replace("Z", "+00:00"))
        except ValueError as exc:
            raise OfficialSpeechParseError("STATE_DIPLOMACY_TIME_INVALID") from exc
        if published.tzinfo is None:
            # WordPress `date_gmt` is explicitly UTC but omits the suffix.
            published = published.replace(tzinfo=timezone.utc)
        speaker = _state_speaker(title)
        text = f"{title} {content}"
        if (
            not speaker
            or not any(marker in text.casefold() for marker in _STATE_SPEECH_MARKERS)
            or not _topic_qualified(text)
        ):
            continue
        identity = source_url.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        events.append(_published_event(
            event_id=f"state-diplomatic-release-{_digest(identity)}",
            event_code="state_diplomatic_official_statement",
            event_subtype="diplomatic_statement_published",
            title=title,
            speaker=speaker,
            source="state_diplomacy",
            source_url=source_url,
            source_fetched_at_utc=source_fetched_at_utc,
            published=published,
        ))
    return sorted(events, key=lambda event: event["scheduled_time_utc"])
