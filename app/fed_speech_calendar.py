"""Parse the Federal Reserve's official speeches and testimony RSS feed."""

from __future__ import annotations

import hashlib
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree


FED_SPEECHES_RSS_URL = "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml"
# 保留旧常量名，避免项目外部脚本因导入失败而中断；实际来源已升级为
# 美联储公开的“全部讲话与证词”RSS，才能覆盖主席和点名委员。
FED_POWELL_RSS_URL = FED_SPEECHES_RSS_URL
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
_TRACKED_SPEAKERS = {
    "warsh": ("Kevin Warsh", "fed_warsh_speech", "warsh_published"),
    "jefferson": ("Philip Jefferson", "fed_jefferson_speech", "jefferson_published"),
    "bowman": ("Michelle Bowman", "fed_bowman_speech", "bowman_published"),
    "waller": ("Christopher Waller", "fed_waller_speech", "waller_published"),
    "powell": ("Jerome H. Powell", "fed_speech", "powell_published"),
}


class FedSpeechParseError(ValueError):
    """Raised when the official speech feed cannot be trusted."""


def _text(element: ElementTree.Element, name: str) -> str:
    child = element.find(name)
    return str(child.text or "").strip() if child is not None else ""


def _is_fed_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").casefold()
    return (
        parsed.scheme == "https"
        and (host == "federalreserve.gov" or host.endswith(".federalreserve.gov"))
    )


def parse_fed_fomc_speeches_rss(
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
        if not _is_fed_url(link):
            raise FedSpeechParseError("FED_SPEECH_URL_INVALID")
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
        speaker_key, separator, speech_title = title.partition(",")
        speaker_data = _TRACKED_SPEAKERS.get(speaker_key.strip().casefold())
        title_lower = title.casefold()
        if (
            any(speaker_key in title_lower for speaker_key in _TRACKED_SPEAKERS)
            and (not separator or speaker_data is None)
        ):
            raise FedSpeechParseError("FED_SPEECH_TRACKED_SPEAKER_FORMAT_INVALID")
        if not separator or speaker_data is None:
            continue
        normalized_title = speech_title.strip().casefold()
        if normalized_title.startswith(_CEREMONIAL_PREFIXES):
            continue
        if not any(keyword in topic_text for keyword in _MACRO_TOPIC_KEYWORDS):
            continue
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        speaker, event_code, event_subtype = speaker_data
        events.append({
            "event_id": f"{event_code}-{digest}",
            "event_code": event_code,
            "event_subtype": event_subtype,
            "title": title,
            "speaker": speaker,
            "country": "US",
            "currency": "USD",
            "scheduled_time_utc": scheduled_time,
            "scheduled_date": published_utc.date().isoformat(),
            "time_precision": "exact",
            "time_basis": "publication_time",
            "status": "published",
            "impact": "high",
            "source": "fed_speeches",
            "source_url": link,
            "source_fetched_at_utc": source_fetched_at_utc,
        })

    return sorted(events, key=lambda event: event["scheduled_time_utc"])


def parse_fed_powell_rss(
    xml_text: str,
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    """Compatibility entry point for callers that used the old Powell-only name."""
    return parse_fed_fomc_speeches_rss(xml_text, source_fetched_at_utc)
