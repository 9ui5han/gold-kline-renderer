"""Parse the Federal Reserve's official Jerome Powell RSS feed."""

from __future__ import annotations

import hashlib
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree


FED_POWELL_RSS_URL = "https://www.federalreserve.gov/feeds/s_t_powell.xml"
_MACRO_TOPIC_KEYWORDS = (
    "economic outlook",
    "economy",
    "economic conditions",
    "monetary policy",
    "inflation",
    "labor market",
    "employment",
    "interest rate",
    "balance sheet",
    "financial stability",
    "framework review",
)
_CEREMONIAL_PREFIXES = ("acceptance remarks", "welcoming remarks")


class FedSpeechParseError(ValueError):
    """Raised when the official speech feed cannot be trusted."""


def _text(element: ElementTree.Element, name: str) -> str:
    child = element.find(name)
    return str(child.text or "").strip() if child is not None else ""


def parse_fed_powell_rss(
    xml_text: str,
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    if not isinstance(xml_text, str) or not xml_text.strip():
        raise FedSpeechParseError("FED_SPEECH_EMPTY_XML")
    try:
        root = ElementTree.fromstring(xml_text.lstrip("\ufeff"))
    except ElementTree.ParseError as exc:
        raise FedSpeechParseError("FED_SPEECH_INVALID_XML") from exc

    channel = root.find("channel")
    items = channel.findall("item") if channel is not None else []
    if channel is None or not items:
        raise FedSpeechParseError("FED_SPEECH_ITEMS_MISSING")

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        title = _text(item, "title")
        link = _text(item, "link") or _text(item, "guid")
        published = _text(item, "pubDate")
        description = _text(item, "description")
        if not title or not link or not published:
            raise FedSpeechParseError("FED_SPEECH_ITEM_INVALID")
        try:
            parsed = parsedate_to_datetime(published)
        except (TypeError, ValueError) as exc:
            raise FedSpeechParseError("FED_SPEECH_DATE_INVALID") from exc
        if parsed.tzinfo is None:
            raise FedSpeechParseError("FED_SPEECH_TIMEZONE_MISSING")
        published_utc = parsed.astimezone(timezone.utc)
        scheduled_time = published_utc.isoformat().replace("+00:00", "Z")
        identity = link.casefold()
        if identity in seen:
            continue
        seen.add(identity)
        topic_text = f"{title} {description}".casefold()
        normalized_title = title.split(",", 1)[-1].strip().casefold()
        if normalized_title.startswith(_CEREMONIAL_PREFIXES):
            continue
        if not any(keyword in topic_text for keyword in _MACRO_TOPIC_KEYWORDS):
            continue
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        events.append({
            "event_id": f"fed-powell-speech-{digest}",
            "event_code": "fed_speech",
            "event_subtype": "powell_published",
            "title": title,
            "speaker": "Jerome H. Powell",
            "country": "US",
            "currency": "USD",
            "scheduled_time_utc": scheduled_time,
            "scheduled_date": published_utc.date().isoformat(),
            "time_precision": "publication_time",
            "status": "published",
            "impact": "high",
            "source": "fed_speeches",
            "source_url": link,
            "source_fetched_at_utc": source_fetched_at_utc,
        })

    return sorted(events, key=lambda event: event["scheduled_time_utc"])
