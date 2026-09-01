"""Strict renderer for analysis-supplied propulsion-block market pages."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .chart_renderer import _english_font, _font

WIDTH, HEIGHT = 1080, 720
# Keep the deterministic chart data intact while giving candles and zones more canvas.
PLOT = (74, 95, 1014, 650)
STYLE_VERSION = "trading-editorial-v1"
PALETTE = {
    "background": "#FFFFFF",
    "ink": "#123B5D",
    "bullish": "#D8A12E",
    "bearish": "#123B5D",
    "grid": "#DCE4EA",
    "order_block": "#2E7896",
    "propulsion_block": "#D8A12E",
}
ROUTE_VERSION = "carousel-route-v2"
RULE_VERSION = "pb-edu-v1"
ZONE_KINDS = {"order_block", "propulsion_block"}
MARKER_KINDS = {"liquidity_sweep", "inducement"}
TIMEFRAME_RE = re.compile(r"^([1-9][0-9]*)(m|h|d|w)$")


def _fail(code: str) -> None:
    raise ValueError(code)


def _is_int(value: Any) -> bool:
    return type(value) is int


def _number(value: Any, code: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        _fail(code)
    try:
        number = float(value)
    except (TypeError, ValueError):
        _fail(code)
    if not math.isfinite(number) or (positive and number <= 0):
        _fail(code)
    return number


def _parse_time(value: Any, code: str) -> tuple[float, str]:
    if isinstance(value, bool):
        _fail(code)
    if isinstance(value, (int, float)):
        numeric = _number(value, code)
        return (numeric / 1000 if abs(numeric) >= 100000000000 else numeric), "absolute"
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    # Naive input deliberately remains on its source clock; only mixed clocks fail.
    return parsed.timestamp() if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc).timestamp(), "absolute" if parsed.tzinfo else "naive"


def _timeframe_seconds(value: Any) -> float:
    match = TIMEFRAME_RE.fullmatch(str(value or ""))
    if not match:
        _fail("MARKET_TIMEFRAME_INVALID")
    return int(match.group(1)) * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[match.group(2)]


def _validate_page(page: dict[str, Any], timeframe: Any) -> list[dict[str, Any]]:
    if page.get("chart_mode") != "educational_reconstruction" or page.get("historical_pattern_claim") is not False:
        _fail("EDUCATIONAL_RECONSTRUCTION_REQUIRED")
    if page.get('direction') not in {'bullish','bearish'}:
        _fail('MARKET_DIRECTION_INVALID')
    if str(page.get("rule_version") or "") != RULE_VERSION:
        _fail("MARKET_RULE_VERSION_INVALID")
    if page.get("bars_closed") is not True:
        _fail("MARKET_BARS_NOT_CLOSED")
    bars = page.get("visible_kline")
    if not isinstance(bars, list) or not 5 <= len(bars) <= 150:
        _fail("MARKET_KLINE_EMPTY_OR_TOO_SHORT")
    if not _is_int(page.get("slice_start")) or not _is_int(page.get("slice_end")) or page["slice_start"] < 0:
        _fail("MARKET_SLICE_INVALID")
    if page["slice_end"] - page["slice_start"] != len(bars):
        _fail("MARKET_SLICE_LENGTH_MISMATCH")
    count = len(bars)
    anchor, confirmation = page.get("anchor_index"), page.get("confirmation_index")
    if not _is_int(anchor) or not _is_int(confirmation) or not 0 <= anchor < confirmation == count - 1:
        _fail("MARKET_ANCHOR_OR_CONFIRMATION_OUT_OF_RANGE")
    times, modes, normalized = [], [], []
    for candle in bars:
        if not isinstance(candle, dict):
            _fail("MARKET_KLINE_INVALID")
        timestamp, mode = _parse_time(candle.get("t"), "MARKET_TIMESTAMP_INVALID")
        o, h, l, c = (_number(candle.get(key), "MARKET_OHLC_INVALID", positive=True) for key in ("o", "h", "l", "c"))
        v = _number(candle.get("v"), "MARKET_VOLUME_INVALID")
        if h < max(o, c) or l > min(o, c) or l > h or v < 0:
            _fail("MARKET_OHLC_INVALID")
        times.append(timestamp); modes.append(mode)
        normalized.append({"t": candle["t"], "o": o, "h": h, "l": l, "c": c, "v": v})
    if len(set(modes)) != 1:
        _fail("MARKET_TIMESTAMP_CLOCK_MIXED")
    interval = _timeframe_seconds(timeframe)
    if any(abs(times[index] - times[index - 1] - interval) > .001 for index in range(1, count)):
        _fail("MARKET_TIMEFRAME_MISMATCH")
    as_of, as_mode = _parse_time(page.get("as_of"), "MARKET_AS_OF_INVALID")
    if as_mode != modes[-1] or abs(as_of - times[-1]) > .001:
        _fail("MARKET_AS_OF_CONFLICT")
    zones = page.get("zones")
    if not isinstance(zones, list) or len(zones) != 2:
        _fail("MARKET_ZONES_INVALID")
    by_kind: dict[str, dict[str, Any]] = {}
    for zone in zones:
        if not isinstance(zone, dict) or zone.get("kind") not in ZONE_KINDS:
            _fail("MARKET_ZONE_KIND_INVALID")
        kind = str(zone["kind"])
        if kind in by_kind:
            _fail("MARKET_ZONE_KIND_DUPLICATE")
        start, end = zone.get("start_index"), zone.get("end_index")
        if not _is_int(start) or not _is_int(end) or not 0 <= start <= end < count:
            _fail("MARKET_ZONE_INDEX_OUT_OF_RANGE")
        low = _number(zone.get("price_low"), "MARKET_ZONE_PRICE_INVALID", positive=True)
        high = _number(zone.get("price_high"), "MARKET_ZONE_PRICE_INVALID", positive=True)
        if low >= high or not isinstance(zone.get("label"), str) or not zone["label"].strip():
            _fail("MARKET_ZONE_INVALID")
        by_kind[kind] = zone
    if set(by_kind) != ZONE_KINDS:
        _fail("MARKET_ZONE_KIND_INVALID")
    for kind, start_expected in (("order_block", None), ("propulsion_block", anchor)):
        zone = by_kind[kind]
        if zone["end_index"] != confirmation or (start_expected is not None and zone["start_index"] != start_expected) or (kind == "order_block" and zone["start_index"] >= anchor):
            _fail("MARKET_ZONE_INDEX_CONTRACT_INVALID")
        origin = normalized[zone["start_index"]]
        expected_low, expected_high = ((origin["l"], origin["o"]) if page.get("direction") == "bullish" else (origin["o"], origin["h"]))
        if abs(float(zone["price_low"]) - expected_low) > 1e-9 or abs(float(zone["price_high"]) - expected_high) > 1e-9:
            _fail("MARKET_ZONE_BOUNDARY_MISMATCH")
    markers = page.get("markers")
    if not isinstance(markers, list) or len(markers) > 2:
        _fail("MARKET_MARKERS_INVALID")
    for marker in markers:
        if not isinstance(marker, dict) or marker.get("kind") not in MARKER_KINDS:
            _fail("MARKET_MARKER_KIND_INVALID")
        if not _is_int(marker.get("index")) or not 0 <= marker["index"] < count:
            _fail("MARKET_MARKER_INDEX_OUT_OF_RANGE")
        reference = marker.get("reference_index")
        if not _is_int(reference) or not 0 <= reference < marker["index"]:
            _fail("MARKET_MARKER_REFERENCE_INVALID")
        _number(marker.get("price"), "MARKET_MARKER_PRICE_INVALID", positive=True)
        expected = normalized[marker['index']]['h' if page['direction']=='bearish' else 'l']
        if abs(float(marker['price'])-expected)>1e-9:
            _fail('MARKET_MARKER_PRICE_MISMATCH')
    if page.get('lesson_type') == 'checklist':
        required={'liquidity_sweep','valid_order_block','inducement_before_poi','propulsion_unmitigated'}
        checks=page.get('checks')
        if (not isinstance(checks,dict) or any(checks.get(k) is not True for k in required)
                or {m['kind'] for m in markers} != MARKER_KINDS):
            _fail('MARKET_CHECKLIST_INCOMPLETE')
    return normalized


def validate_market_request(request_pages: list[dict[str, Any]], route_payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(route_payload, dict) or route_payload.get("schema_version") != ROUTE_VERSION:
        _fail("MARKET_ROUTE_VERSION_INVALID")
    if route_payload.get("analysis_mode") != "educational_reconstruction":
        _fail("EDUCATIONAL_RECONSTRUCTION_REQUIRED")
    if not str(route_payload.get("market") or "").strip():
        _fail("MARKET_ROUTE_IDENTITY_INVALID")
    _timeframe_seconds(route_payload.get("timeframe"))
    if not isinstance(route_payload.get("input_meta"), dict):
        _fail("MARKET_INPUT_META_INVALID")
    analysis_pages = route_payload.get("analysis_pages")
    if not isinstance(analysis_pages, list) or not analysis_pages:
        _fail("MARKET_ANALYSIS_PAGES_EMPTY")
    requested = [item.get("page_no") for item in request_pages if isinstance(item, dict)]
    if len(requested) != len(request_pages) or not requested or any(not _is_int(no) for no in requested) or any(item.get("visual_type") != "market_chart" for item in request_pages):
        _fail("MARKET_REQUEST_PAGES_INVALID")
    page_nos = [item.get("page_no") for item in analysis_pages if isinstance(item, dict)]
    if len(page_nos) != len(analysis_pages) or any(not _is_int(no) for no in page_nos) or len(set(page_nos)) != len(page_nos):
        _fail("MARKET_PAGE_NO_DUPLICATE")
    if len(set(requested)) != len(requested) or set(requested) != set(page_nos):
        _fail("MARKET_PAGE_SET_MISMATCH")
    return analysis_pages


def _font_for(language: str, size: int, bold: bool = False):
    return _english_font(size, 650 if bold else 450) if str(language).lower().startswith("en") else _font(size, bold)


def _bounded_text(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, font, *, width: int = WIDTH) -> tuple[float, float]:
    box = draw.textbbox(xy, text, font=font)
    x = max(4, min(xy[0], width - (box[2] - box[0]) - 4))
    y = max(2, min(xy[1], HEIGHT - (box[3] - box[1]) - 2))
    draw.text((x, y), text, fill="#536270", font=font)
    return x, y


def render_market_chart(page: dict[str, Any], output_path: Path, route_payload: dict[str, Any], *, language: str) -> dict[str, Any]:
    candles = _validate_page(page, route_payload.get("timeframe"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (WIDTH, HEIGHT), PALETTE["background"])
    draw = ImageDraw.Draw(image, "RGBA")
    left, top, right, bottom = PLOT
    prices = [item[key] for item in candles for key in ("h", "l")] + [float(z[key]) for z in page["zones"] for key in ("price_low", "price_high")] + [float(m["price"]) for m in page["markers"]]
    low, high = min(prices), max(prices); pad = max((high - low) * .08, .01); low -= pad; high += pad
    pitch = (right - left) / len(candles)
    x_for = lambda i: left + (i + .5) * pitch
    y_for = lambda value: bottom - (value - low) / (high - low) * (bottom - top)
    for fraction in range(5):
        y = top + fraction * (bottom - top) / 4
        draw.line((left, y, right, y), fill=PALETTE["grid"], width=1)
    draw.text((left, 18), f"{route_payload.get('market', '')} · {route_payload.get('timeframe', '')} · {page.get('direction', '')}", fill=PALETTE["ink"], font=_font_for(language, 23, True))
    coord_zones, coord_markers = [], []
    colors = {"order_block": (46, 120, 150, 70), "propulsion_block": (216, 161, 46, 78)}
    for ordinal, zone in enumerate(page["zones"]):
        x1 = max(left, x_for(zone["start_index"]) - pitch / 2); x2 = min(right, x_for(zone["end_index"]) + pitch / 2)
        y1, y2 = sorted((y_for(float(zone["price_high"])), y_for(float(zone["price_low"]))))
        color = colors[zone["kind"]]; draw.rectangle((x1, y1, x2, y2), fill=color, outline=color[:3] + (220,), width=2)
        label = f"{zone['kind'].replace('_', ' ')}: {zone['label']}"
        draw.rectangle((left + ordinal * 420, 57, left + ordinal * 420 + 14, 71), fill=color[:3] + (220,))
        _bounded_text(draw, (left + ordinal * 420 + 21, 50), label[:45], _font_for(language, 17))
        coord_zones.append({"kind": zone["kind"], "start_index": zone["start_index"], "end_index": zone["end_index"], "pixel_box": [round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)]})
    for index, candle in enumerate(candles):
        x = x_for(index); color = PALETTE["bullish"] if candle["c"] >= candle["o"] else PALETTE["bearish"]
        draw.line((x, y_for(candle["h"]), x, y_for(candle["l"])), fill=color, width=3)
        y1, y2 = sorted((y_for(candle["o"]), y_for(candle["c"])))
        body = (x - pitch * .28, y1, x + pitch * .28, max(y2, y1 + 3))
        draw.rounded_rectangle(body, radius=max(1, int(pitch * .06)), fill=color)
    for ordinal, marker in enumerate(page["markers"]):
        x, y = x_for(marker["index"]), y_for(float(marker["price"])); color = "#D96B78" if marker["kind"] == "liquidity_sweep" else "#D9A62E"
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=color, outline="#17212B", width=1)
        coord_markers.append({"kind": marker["kind"], "index": marker["index"], "price": marker["price"], "pixel": [round(x, 2), round(y, 2)]})
        _bounded_text(draw, (left + ordinal * 420, 674), marker['kind'].replace('_', ' ') + f" (bar {marker['index']})", _font_for(language, 16))
    draw.rectangle(PLOT, outline="#8D98A4", width=2)
    for value, y in ((high - pad, y_for(high-pad)), (low + pad, y_for(low+pad))):
        _bounded_text(draw, (right + 7, y - 9), f"{value:.2f}", _font_for(language, 16))
    image.save(output_path, "PNG")
    fingerprint_input = {"market": route_payload.get("market"), "timeframe": route_payload.get("timeframe"), "visible_kline": page["visible_kline"], "zones": page["zones"], "markers": page["markers"], "as_of": page["as_of"], "rule_version": page["rule_version"]}
    return {"page_no": int(page["page_no"]), "asset_key": f"chart_page_{int(page['page_no']):02d}", "asset_type": "market_chart", "asset_path": str(output_path), "width": WIDTH, "height": HEIGHT, "source_type": "educational_reconstruction", "source_market": str(route_payload.get("market")), "source_timeframe": str(route_payload.get("timeframe")), "data_timezone": str(route_payload.get("input_meta", {}).get("data_timezone", "not_provided")), "source_as_of": str(page["as_of"]), "bars_closed": True, "rule_version": RULE_VERSION, "rendered_candle_count": len(candles), "data_fingerprint": hashlib.sha256(json.dumps(fingerprint_input, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest(), "style_version": STYLE_VERSION, "palette": dict(PALETTE), "coordinate_map": {"zones": coord_zones, "markers": coord_markers}}
