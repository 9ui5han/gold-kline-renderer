"""Probe the official US macro calendar sources without parsing events yet.

This module is deliberately limited to source reachability and basic response
shape checks. It does not infer a market direction and does not expose full
upstream response bodies.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


def build_user_agent() -> str:
    return os.getenv(
        "MACRO_USER_AGENT",
        "GoldKlineRender/2.0 (+https://gold-kline-renderer.onrender.com)",
    ).strip()


USER_AGENT = build_user_agent()
CONNECT_TIMEOUT_SEC = 5.0
READ_TIMEOUT_SEC = 12.0
MAX_SAMPLE_CHARS = 160


@dataclass(frozen=True)
class SourceSpec:
    source: str
    url: str
    accept: str
    expected_content_types: tuple[str, ...]
    body_marker: str
    response_format: str


SOURCE_SPECS = (
    SourceSpec(
        source="fed",
        url=(
            "https://www.federalreserve.gov/monetarypolicy/"
            "fomccalendars.htm"
        ),
        accept="text/html,application/xhtml+xml",
        expected_content_types=("text/html", "application/xhtml+xml"),
        body_marker="fomc",
        response_format="html",
    ),
    SourceSpec(
        source="bls",
        url="https://www.bls.gov/schedule/news_release/bls.ics",
        accept="text/calendar,text/plain;q=0.9,*/*;q=0.8",
        expected_content_types=("text/calendar", "text/plain"),
        body_marker="begin:vcalendar",
        response_format="ics",
    ),
    SourceSpec(
        source="bea",
        url="https://apps.bea.gov/API/signup/release_dates.json",
        accept="application/json",
        expected_content_types=("application/json", "text/json"),
        body_marker="",
        response_format="json",
    ),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_sample(text: str) -> str:
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    return compact[:MAX_SAMPLE_CHARS]


def _content_type(response: httpx.Response) -> str:
    return response.headers.get("content-type", "").split(";", 1)[0].lower()


def _structure_valid(spec: SourceSpec, response: httpx.Response) -> bool:
    content_type = _content_type(response)
    if not any(
        content_type == expected
        for expected in spec.expected_content_types
    ):
        return False

    if spec.response_format == "json":
        try:
            return isinstance(response.json(), dict)
        except (json.JSONDecodeError, ValueError):
            return False

    return spec.body_marker in response.text.lower()


def probe_source(
    spec: SourceSpec,
    client: httpx.Client,
) -> dict[str, Any]:
    requested_at_utc = _now_iso()
    started = time.monotonic()
    try:
        response = client.get(
            spec.url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": spec.accept,
            },
        )
        elapsed_ms = round((time.monotonic() - started) * 1000)
        content_type = _content_type(response)
        status_ok = 200 <= response.status_code < 300
        structure_valid = status_ok and _structure_valid(spec, response)

        error_code = ""
        error_message = ""
        if not status_ok:
            error_code = f"HTTP_STATUS_{response.status_code}"
            error_message = "官方来源返回非2xx状态"
        elif not structure_valid:
            error_code = "UNEXPECTED_RESPONSE_STRUCTURE"
            error_message = "响应类型或最小内容标记与预期不一致"

        return {
            "source": spec.source,
            "url": spec.url,
            "requested_at_utc": requested_at_utc,
            "http_status": response.status_code,
            "content_type": content_type,
            "response_bytes": len(response.content),
            "elapsed_ms": elapsed_ms,
            "reachable": status_ok,
            "structure_valid": structure_valid,
            "error_code": error_code,
            "error_message": error_message,
            "response_sample": (
                "" if structure_valid else _safe_sample(response.text)
            ),
        }
    except httpx.TimeoutException:
        error_code = "UPSTREAM_TIMEOUT"
        error_message = "连接或读取官方来源超时"
    except httpx.HTTPError as exc:
        error_code = "UPSTREAM_REQUEST_FAILED"
        error_message = type(exc).__name__

    return {
        "source": spec.source,
        "url": spec.url,
        "requested_at_utc": requested_at_utc,
        "http_status": 0,
        "content_type": "",
        "response_bytes": 0,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "reachable": False,
        "structure_valid": False,
        "error_code": error_code,
        "error_message": error_message,
        "response_sample": "",
    }


def probe_all_sources(
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    owns_client = client is None
    if client is None:
        client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(
                READ_TIMEOUT_SEC,
                connect=CONNECT_TIMEOUT_SEC,
            ),
        )

    try:
        sources = [probe_source(spec, client) for spec in SOURCE_SPECS]
    finally:
        if owns_client:
            client.close()

    valid_count = sum(item["structure_valid"] for item in sources)
    if valid_count == len(sources):
        data_status = "complete"
    elif valid_count:
        data_status = "partial"
    else:
        data_status = "unavailable"

    return {
        "schema_version": "macro-source-health-v1",
        "checked_at_utc": _now_iso(),
        "data_status": data_status,
        "directional_bias": "not_calculated",
        "source_count": len(sources),
        "valid_source_count": valid_count,
        "sources": sources,
    }
