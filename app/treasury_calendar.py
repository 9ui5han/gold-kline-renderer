"""Parse official Treasury auction, buyback, and debt-management sources."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, time, timezone
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree
from zoneinfo import ZoneInfo


TREASURY_AUCTIONS_URL = (
    "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
    "v1/accounting/od/auctions_query?sort=-auction_date&page%5Bsize%5D=100"
)
TREASURY_BUYBACK_URL = (
    "https://home.treasury.gov/system/files/221/Tentative-Buyback-Schedule.xml"
)
TREASURY_PRESS_YEAR = datetime.now(timezone.utc).year
TREASURY_PRESS_URL = (
    "https://home.treasury.gov/news-data/press-releases/search/"
    f"{TREASURY_PRESS_YEAR}.json"
)
TREASURY_EASTERN_TIMEZONE = ZoneInfo("America/New_York")

_PRESS_KEYWORDS = (
    "buyback",
    "quarterly refunding",
    "debt management",
    "marketable securities",
    "liquidity support",
)
_PRESS_EXCLUSIONS = (
    "sanction",
    "property auction",
    "art auction",
    "vehicle auction",
    "tax lien auction",
)


class TreasuryCalendarParseError(ValueError):
    """Raised when an official Treasury source cannot be trusted."""


def _event_id(prefix: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"treasury-{prefix}-{digest}"


def _load_json(payload: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, str):
        raise TreasuryCalendarParseError("TREASURY_JSON_TYPE_INVALID")
    try:
        result = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise TreasuryCalendarParseError("TREASURY_JSON_INVALID") from exc
    if not isinstance(result, dict):
        raise TreasuryCalendarParseError("TREASURY_JSON_STRUCTURE_INVALID")
    return result


def parse_treasury_auctions(
    payload: str | dict[str, Any],
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    data = _load_json(payload).get("data")
    if not isinstance(data, list) or not data:
        raise TreasuryCalendarParseError("TREASURY_AUCTIONS_DATA_MISSING")

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in data:
        if not isinstance(row, dict):
            raise TreasuryCalendarParseError("TREASURY_AUCTION_ROW_INVALID")
        auction_date = str(row.get("auction_date") or "").strip()
        cusip = str(row.get("cusip") or "").strip()
        security_type = str(row.get("security_type") or "").strip()
        security_term = str(row.get("security_term") or "").strip()
        if not auction_date or not cusip or not security_type or not security_term:
            raise TreasuryCalendarParseError("TREASURY_AUCTION_FIELDS_MISSING")
        try:
            day = datetime.strptime(auction_date, "%Y-%m-%d").date()
        except ValueError as exc:
            raise TreasuryCalendarParseError("TREASURY_AUCTION_DATE_INVALID") from exc
        close_time = str(row.get("closing_time_comp") or "").strip()
        if not close_time:
            raise TreasuryCalendarParseError("TREASURY_AUCTION_TIME_MISSING")
        try:
            parsed_time = datetime.strptime(close_time, "%I:%M %p").time()
        except ValueError as exc:
            raise TreasuryCalendarParseError("TREASURY_AUCTION_TIME_INVALID") from exc
        scheduled = datetime.combine(
            day,
            parsed_time,
            tzinfo=TREASURY_EASTERN_TIMEZONE,
        ).astimezone(timezone.utc)
        identity = f"{cusip}|{auction_date}"
        if identity in seen:
            continue
        seen.add(identity)
        offering_amount = str(row.get("offering_amt") or "").strip()
        events.append({
            "event_id": _event_id("auction", identity),
            "event_code": "treasury_auction",
            "event_subtype": security_type.casefold().replace(" ", "_"),
            "title": f"U.S. Treasury {security_term} {security_type} Auction",
            "country": "US",
            "currency": "USD",
            "scheduled_time_utc": scheduled.isoformat().replace("+00:00", "Z"),
            "scheduled_date": day.isoformat(),
            "time_precision": "exact",
            "status": "scheduled",
            "impact": "medium",
            "source": "treasury_auctions",
            "source_url": TREASURY_AUCTIONS_URL,
            "source_fetched_at_utc": source_fetched_at_utc,
            "cusip": cusip,
            "security_type": security_type,
            "security_term": security_term,
            "offering_amount_usd": offering_amount,
        })
    return sorted(events, key=lambda event: event["scheduled_time_utc"])


def _xml_text(parent: ElementTree.Element, name: str) -> str:
    element = parent.find(name)
    return str(element.text or "").strip() if element is not None else ""


def parse_treasury_buybacks(
    xml_text: str,
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    if not isinstance(xml_text, str) or not xml_text.strip():
        raise TreasuryCalendarParseError("TREASURY_BUYBACK_EMPTY_XML")
    try:
        root = ElementTree.fromstring(xml_text.lstrip("\ufeff"))
    except ElementTree.ParseError as exc:
        raise TreasuryCalendarParseError("TREASURY_BUYBACK_INVALID_XML") from exc
    rows = root.findall("BuybackCalendarDate")
    if root.tag != "BuyBackCalendar" or not rows:
        raise TreasuryCalendarParseError("TREASURY_BUYBACK_ROWS_MISSING")

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        operation_date = _xml_text(row, "OperationDate")
        start_time = _xml_text(row, "OperationStartTimeEasternUS")
        bucket = _xml_text(row, "PurchaseBucketName")
        operation_type = _xml_text(row, "OperationType")
        if not operation_date or not start_time or not bucket or not operation_type:
            raise TreasuryCalendarParseError("TREASURY_BUYBACK_FIELDS_MISSING")
        try:
            day = datetime.strptime(operation_date, "%Y-%m-%d").date()
            clock = time.fromisoformat(start_time)
        except ValueError as exc:
            raise TreasuryCalendarParseError("TREASURY_BUYBACK_DATETIME_INVALID") from exc
        scheduled = datetime.combine(
            day,
            clock,
            tzinfo=TREASURY_EASTERN_TIMEZONE,
        ).astimezone(timezone.utc)
        identity = f"{operation_date}|{start_time}|{bucket}|{operation_type}"
        if identity in seen:
            continue
        seen.add(identity)
        events.append({
            "event_id": _event_id("buyback", identity),
            "event_code": "treasury_buyback",
            "event_subtype": operation_type.casefold().replace(" ", "_"),
            "title": f"U.S. Treasury {operation_type} Buyback: {bucket}",
            "country": "US",
            "currency": "USD",
            "scheduled_time_utc": scheduled.isoformat().replace("+00:00", "Z"),
            "scheduled_date": day.isoformat(),
            "time_precision": "exact",
            "status": "scheduled",
            "impact": "high",
            "source": "treasury_buybacks",
            "source_url": TREASURY_BUYBACK_URL,
            "source_fetched_at_utc": source_fetched_at_utc,
            "announcement_date": _xml_text(row, "AnnouncementDate"),
            "operation_type": operation_type,
            "purchase_bucket": bucket,
            "maximum_purchase_amount_usd": _xml_text(
                row, "MaximumPurchaseAmountDollars"
            ),
        })
    return sorted(events, key=lambda event: event["scheduled_time_utc"])


def parse_treasury_press_releases(
    payload: str | dict[str, Any],
    source_fetched_at_utc: str,
) -> list[dict[str, Any]]:
    rows = _load_json(payload).get("items")
    if not isinstance(rows, list):
        raise TreasuryCalendarParseError("TREASURY_PRESS_ROWS_MISSING")

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise TreasuryCalendarParseError("TREASURY_PRESS_ROW_INVALID")
        raw_time = str(row.get("datetime") or "").strip()
        href = str(row.get("url") or "").strip()
        title = re.sub(r"\s+", " ", str(row.get("title") or "")).strip()
        if not raw_time or not href or not title:
            raise TreasuryCalendarParseError("TREASURY_PRESS_FIELDS_MISSING")
        topic_text = title.casefold()
        if any(exclusion in topic_text for exclusion in _PRESS_EXCLUSIONS):
            continue
        if not any(keyword in topic_text for keyword in _PRESS_KEYWORDS):
            continue
        # Treasury's annual search JSON appends ``Z`` to the publication
        # wall-clock value, while the official article page exposes that same
        # value with the America/New_York offset.  Treat the search value as an
        # Eastern wall clock; otherwise summer releases are shifted four hours
        # early and winter releases five hours early.
        normalized = raw_time[:-1] if raw_time.endswith("Z") else raw_time
        try:
            published = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise TreasuryCalendarParseError("TREASURY_PRESS_DATE_INVALID") from exc
        if published.tzinfo is None:
            published = published.replace(tzinfo=TREASURY_EASTERN_TIMEZONE)
        published_utc = published.astimezone(timezone.utc)
        source_url = urljoin(TREASURY_PRESS_URL, href)
        if source_url in seen:
            continue
        seen.add(source_url)
        events.append({
            "event_id": _event_id("announcement", source_url),
            "event_code": "treasury_announcement",
            "event_subtype": "debt_management",
            "title": title,
            "country": "US",
            "currency": "USD",
            "scheduled_time_utc": published_utc.isoformat().replace("+00:00", "Z"),
            "scheduled_date": published.date().isoformat(),
            "time_precision": "exact",
            "time_basis": "publication_time",
            "source_timezone": "America/New_York",
            "source_local_time": published.isoformat(),
            "source_time_raw": raw_time,
            "time_verified": True,
            "status": "published",
            "impact": "high",
            "source": "treasury_press",
            "source_url": source_url,
            "source_fetched_at_utc": source_fetched_at_utc,
        })
    return sorted(events, key=lambda event: event["scheduled_time_utc"])
