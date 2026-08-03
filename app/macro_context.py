"""Build a cached, source-aware macro calendar context for XAUUSD.

This service combines official Fed, BLS, and BEA calendars.  It only reports
event timing and source health; it never calculates a directional bias.
"""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from .bea_calendar import parse_bea_release_dates
from .bls_calendar import parse_bls_ics
from .fed_calendar import parse_fed_fomc_calendar
from .macro_source_probe import (
    CONNECT_TIMEOUT_SEC,
    READ_TIMEOUT_SEC,
    SOURCE_SPECS,
    USER_AGENT,
    SourceSpec,
)


CACHE_SCHEMA_VERSION = "macro-source-cache-v1"
CONTEXT_SCHEMA_VERSION = "macro-events-context-v1"
DEFAULT_CACHE_TTL_SEC = 6 * 60 * 60
DEFAULT_MAX_STALE_SEC = 48 * 60 * 60
QUERY_LOOKBACK_HOURS = 24
QUERY_LOOKAHEAD_HOURS = 24

_PARSERS: dict[str, Callable[[Any, str], list[dict[str, Any]]]] = {
    "fed": parse_fed_fomc_calendar,
    "bls": parse_bls_ics,
    "bea": parse_bea_release_dates,
}


class MacroContextError(ValueError):
    """Raised when a context request is invalid."""


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso_time(value: Any, label: str) -> datetime:
    text = str(value or "").strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MacroContextError(f"{label}_INVALID") from exc
    if parsed.tzinfo is None:
        raise MacroContextError(f"{label}_TIMEZONE_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _validate_request(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise MacroContextError("REQUEST_BODY_INVALID")
    symbol = str(payload.get("symbol") or "").strip().upper()
    if symbol != "XAUUSD":
        raise MacroContextError("SYMBOL_MUST_BE_XAUUSD")

    data_as_of = _parse_iso_time(payload.get("data_as_of"), "DATA_AS_OF")
    horizon = payload.get("forecast_horizon")
    if not isinstance(horizon, dict):
        raise MacroContextError("FORECAST_HORIZON_INVALID")
    start = _parse_iso_time(horizon.get("start_time"), "HORIZON_START")
    end = _parse_iso_time(horizon.get("end_time"), "HORIZON_END")
    if end <= start:
        raise MacroContextError("HORIZON_END_MUST_BE_LATER")
    if data_as_of > end:
        raise MacroContextError("DATA_AS_OF_AFTER_HORIZON")

    duration_minutes = horizon.get("duration_minutes")
    if not isinstance(duration_minutes, (int, float)) or isinstance(
        duration_minutes, bool
    ):
        raise MacroContextError("HORIZON_DURATION_INVALID")
    actual_duration = (end - start).total_seconds() / 60
    if abs(float(duration_minutes) - actual_duration) > 0.01:
        raise MacroContextError("HORIZON_DURATION_MISMATCH")

    timeframe = str(horizon.get("timeframe") or "").strip()
    if not timeframe:
        raise MacroContextError("HORIZON_TIMEFRAME_REQUIRED")

    request_id = str(payload.get("request_id") or "").strip() or str(uuid.uuid4())
    return {
        "request_id": request_id,
        "symbol": symbol,
        "data_as_of": data_as_of,
        "forecast_horizon": {
            "schema_version": str(
                horizon.get("schema_version") or "forecast-horizon-v1"
            ),
            "timeframe": timeframe,
            "start_time": _iso_utc(start),
            "end_time": _iso_utc(end),
            "duration_minutes": float(duration_minutes),
        },
        "horizon_start": start,
        "horizon_end": end,
    }


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";", 1)[0].lower()


def _cache_age_seconds(entry: dict[str, Any], now: datetime) -> float | None:
    try:
        fetched = _parse_iso_time(entry.get("fetched_at_utc"), "CACHE_TIME")
    except MacroContextError:
        return None
    age = (now - fetched).total_seconds()
    if age < -300:
        return None
    return max(0.0, age)


def _valid_cache_entry(entry: Any) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    if not isinstance(entry.get("events"), list):
        return None
    if not str(entry.get("fetched_at_utc") or "").strip():
        return None
    return entry


def _source_status(
    source: str,
    *,
    available: bool,
    stale: bool,
    cache_state: str,
    fetched_at_utc: str,
    http_status: int,
    content_type: str,
    event_count: int,
    error_code: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    return {
        "source": source,
        "available": available,
        "stale": stale,
        "cache_state": cache_state,
        "fetched_at_utc": fetched_at_utc,
        "http_status": http_status,
        "content_type": content_type,
        "event_count": event_count,
        "error_code": error_code,
        "error_message": error_message,
    }


def _fetch_source(
    spec: SourceSpec,
    client: httpx.Client,
    fetched_at_utc: str,
) -> dict[str, Any]:
    try:
        response = client.get(
            spec.url,
            headers={"User-Agent": USER_AGENT, "Accept": spec.accept},
        )
    except httpx.TimeoutException:
        return {
            "ok": False,
            "http_status": 0,
            "content_type": "",
            "error_code": "UPSTREAM_TIMEOUT",
            "error_message": "官方来源连接或读取超时",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "http_status": 0,
            "content_type": "",
            "error_code": "UPSTREAM_REQUEST_FAILED",
            "error_message": type(exc).__name__,
        }

    content_type = _content_type(response)
    if not 200 <= response.status_code < 300:
        return {
            "ok": False,
            "http_status": response.status_code,
            "content_type": content_type,
            "error_code": f"HTTP_STATUS_{response.status_code}",
            "error_message": "官方来源返回非2xx状态",
        }
    if content_type not in spec.expected_content_types:
        return {
            "ok": False,
            "http_status": response.status_code,
            "content_type": content_type,
            "error_code": "UNEXPECTED_CONTENT_TYPE",
            "error_message": "官方来源响应类型不符合文档",
        }

    try:
        parser_input: Any = response.json() if spec.response_format == "json" else response.text
        events = _PARSERS[spec.source](parser_input, fetched_at_utc)
    except (ValueError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "http_status": response.status_code,
            "content_type": content_type,
            "error_code": "SOURCE_PARSE_FAILED",
            "error_message": str(exc)[:160],
        }
    if not events:
        return {
            "ok": False,
            "http_status": response.status_code,
            "content_type": content_type,
            "error_code": "SOURCE_EVENTS_EMPTY",
            "error_message": "官方来源结构有效但白名单事件为空",
        }

    return {
        "ok": True,
        "http_status": response.status_code,
        "content_type": content_type,
        "fetched_at_utc": fetched_at_utc,
        "events": events,
    }


class MacroContextService:
    def __init__(
        self,
        cache_path: Path,
        cache_ttl_sec: int = DEFAULT_CACHE_TTL_SEC,
        max_stale_sec: int = DEFAULT_MAX_STALE_SEC,
    ) -> None:
        self.cache_path = Path(cache_path)
        self.cache_ttl_sec = max(0, int(cache_ttl_sec))
        self.max_stale_sec = max(self.cache_ttl_sec, int(max_stale_sec))
        self._lock = threading.Lock()

    def _load_cache(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": CACHE_SCHEMA_VERSION, "sources": {}}
        if not isinstance(payload, dict) or payload.get("schema_version") != CACHE_SCHEMA_VERSION:
            return {"schema_version": CACHE_SCHEMA_VERSION, "sources": {}}
        if not isinstance(payload.get("sources"), dict):
            return {"schema_version": CACHE_SCHEMA_VERSION, "sources": {}}
        return payload

    def _save_cache(self, cache: dict[str, Any]) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(cache, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.cache_path)

    def _fetch_sources(
        self,
        specs: list[SourceSpec],
        client: httpx.Client,
        fetched_at_utc: str,
    ) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=len(specs) or 1) as executor:
            futures = {
                executor.submit(_fetch_source, spec, client, fetched_at_utc): spec.source
                for spec in specs
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    results[source] = future.result()
                except Exception as exc:
                    results[source] = {
                        "ok": False,
                        "http_status": 0,
                        "content_type": "",
                        "error_code": "SOURCE_WORKER_FAILED",
                        "error_message": type(exc).__name__,
                    }
        return results

    @staticmethod
    def _event_in_query_window(
        event: dict[str, Any],
        query_start: datetime,
        query_end: datetime,
    ) -> bool:
        scheduled_time = event.get("scheduled_time_utc")
        if scheduled_time:
            try:
                parsed = _parse_iso_time(scheduled_time, "EVENT_TIME")
            except MacroContextError:
                return False
            return query_start <= parsed <= query_end
        scheduled_date = str(event.get("scheduled_date") or "").strip()
        return query_start.date().isoformat() <= scheduled_date <= query_end.date().isoformat()

    def get_context(
        self,
        payload: dict[str, Any],
        *,
        client: httpx.Client | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        request = _validate_request(payload)
        checked_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        checked_at_utc = _iso_utc(checked_at)

        with self._lock:
            cache = self._load_cache()
            cache_sources = cache["sources"]
            source_events: dict[str, list[dict[str, Any]]] = {}
            statuses: dict[str, dict[str, Any]] = {}
            specs_to_fetch: list[SourceSpec] = []

            for spec in SOURCE_SPECS:
                entry = _valid_cache_entry(cache_sources.get(spec.source))
                age = _cache_age_seconds(entry, checked_at) if entry else None
                if entry is not None and age is not None and age <= self.cache_ttl_sec:
                    events = entry["events"]
                    source_events[spec.source] = events
                    statuses[spec.source] = _source_status(
                        spec.source,
                        available=True,
                        stale=False,
                        cache_state="fresh",
                        fetched_at_utc=entry["fetched_at_utc"],
                        http_status=int(entry.get("http_status") or 200),
                        content_type=str(entry.get("content_type") or ""),
                        event_count=len(events),
                    )
                else:
                    specs_to_fetch.append(spec)

            owns_client = False
            active_client = client
            if specs_to_fetch and active_client is None:
                active_client = httpx.Client(
                    follow_redirects=True,
                    timeout=httpx.Timeout(
                        READ_TIMEOUT_SEC,
                        connect=CONNECT_TIMEOUT_SEC,
                    ),
                )
                owns_client = True
            try:
                fetched_results = (
                    self._fetch_sources(
                        specs_to_fetch,
                        active_client,
                        checked_at_utc,
                    )
                    if active_client is not None
                    else {}
                )
            finally:
                if owns_client and active_client is not None:
                    active_client.close()
            cache_changed = False
            for spec in specs_to_fetch:
                result = fetched_results[spec.source]
                if result["ok"]:
                    events = result["events"]
                    source_events[spec.source] = events
                    cache_sources[spec.source] = {
                        "fetched_at_utc": result["fetched_at_utc"],
                        "http_status": result["http_status"],
                        "content_type": result["content_type"],
                        "events": events,
                    }
                    cache_changed = True
                    statuses[spec.source] = _source_status(
                        spec.source,
                        available=True,
                        stale=False,
                        cache_state="refreshed",
                        fetched_at_utc=result["fetched_at_utc"],
                        http_status=result["http_status"],
                        content_type=result["content_type"],
                        event_count=len(events),
                    )
                    continue

                entry = _valid_cache_entry(cache_sources.get(spec.source))
                age = _cache_age_seconds(entry, checked_at) if entry else None
                if entry is not None and age is not None and age <= self.max_stale_sec:
                    events = entry["events"]
                    source_events[spec.source] = events
                    statuses[spec.source] = _source_status(
                        spec.source,
                        available=True,
                        stale=True,
                        cache_state="stale_fallback",
                        fetched_at_utc=entry["fetched_at_utc"],
                        http_status=result["http_status"],
                        content_type=result["content_type"],
                        event_count=len(events),
                        error_code=result["error_code"],
                        error_message=result["error_message"],
                    )
                else:
                    source_events[spec.source] = []
                    statuses[spec.source] = _source_status(
                        spec.source,
                        available=False,
                        stale=False,
                        cache_state="missing",
                        fetched_at_utc="",
                        http_status=result["http_status"],
                        content_type=result["content_type"],
                        event_count=0,
                        error_code=result["error_code"],
                        error_message=result["error_message"],
                    )

            if cache_changed:
                cache["updated_at_utc"] = checked_at_utc
                self._save_cache(cache)

        available_count = sum(status["available"] for status in statuses.values())
        stale_count = sum(status["stale"] for status in statuses.values())
        if available_count == len(SOURCE_SPECS) and stale_count == 0:
            data_status = "complete"
        elif available_count:
            data_status = "partial"
        else:
            data_status = "unavailable"

        query_start = request["data_as_of"] - timedelta(hours=QUERY_LOOKBACK_HOURS)
        query_end = request["horizon_end"] + timedelta(hours=QUERY_LOOKAHEAD_HOURS)
        all_events = [
            event
            for events in source_events.values()
            for event in events
            if self._event_in_query_window(event, query_start, query_end)
        ]
        all_events.sort(
            key=lambda item: (
                item.get("scheduled_time_utc") or item.get("scheduled_date") or "",
                item.get("event_id") or "",
            )
        )

        return {
            "schema_version": CONTEXT_SCHEMA_VERSION,
            "request_id": request["request_id"],
            "symbol": request["symbol"],
            "data_as_of": _iso_utc(request["data_as_of"]),
            "forecast_horizon": request["forecast_horizon"],
            "query_window": {
                "start_time": _iso_utc(query_start),
                "end_time": _iso_utc(query_end),
            },
            "fetched_at_utc": checked_at_utc,
            "source_status": statuses,
            "events": all_events,
            "data_status": data_status,
            "directional_bias": "not_calculated",
            "limitations": [
                "事件时间只用于风险窗口，不代表黄金上涨或下跌方向。",
                "接口不提供actual、consensus或previous数值。",
                "接口未接入美元指数、美债收益率或新闻正文。",
            ],
        }
