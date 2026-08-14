"""TradingView-inspired chart renderer for the long-form K-line video."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


SC_FONT_CANDIDATES = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
)
SC_BOLD_FONT_CANDIDATES = (
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 2),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 2),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
    ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
)
FONT_SMOKE_TEXT = "Gold support resistance forecast"
_FONT_VERIFIED = False

VIDEO_LABELS = {
    "last_closed_candle": "Last Closed Candle",
    "current_view": "Current View",
    "candle_time": "Candle Time",
    "support": "Support",
    "resistance": "Resistance",
    "key_support_zone": "Support",
    "key_resistance_zone": "Resistance",
    "bullish_fvg": "Bullish FVG",
    "bearish_fvg": "Bearish FVG",
    "primary": "Primary",
    "alternate": "Alternate",
    "range_bound": "Range-bound",
    "bullish": "Bullish",
    "bearish": "Bearish",
    "conditional_path": "Conditional Path",
    "near_term": "Near Term",
    "mid_term": "Mid Term",
    "later": "Later",
    "trend_scenarios": "Trend Scenarios",
    "two_way_scenario": "Two-way Scenario",
    "range_bullish": "Range, Bullish Bias",
    "range_bearish": "Range, Bearish Bias",
    "range_consolidation": "Range Consolidation",
    "primary_alternate_paths": "Primary and Alternate Paths",
    "potential_trend_path": "Potential Trend Path",
    "utc": "UTC",
}
EDUCATIONAL_NOTICE = (
    "Educational market observation · Conditional scenarios, not trading signals"
)
ANALYSIS_ZOOM_CANDLES = 24
FORECAST_TURN_THRESHOLD_DEG = 13.0
SUBTITLE_EN_FONT_SIZE = 104
SUBTITLE_ZH_FONT_SIZE = 72
AXIS_FONT_SIZE = 34
CHART_LABEL_FONT_SIZE = 25
# The review band is deliberately shallow (the red-box area above the chart),
# so this size allows two Chinese lines to stay completely inside it.
REVIEW_CHINESE_FONT_SIZE = 34
LEVEL_LABEL_MIN_FONT_SIZE = 26
LEVEL_LABEL_MAX_FONT_SIZE = 48
PREDICTION_HISTORY_END_RATIO = 0.40


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load the Simplified Chinese face explicitly from the Noto TTC."""
    candidates = SC_BOLD_FONT_CANDIDATES if bold else SC_FONT_CANDIDATES

    for path, index in candidates:
        if not Path(path).exists():
            continue
        try:
            return ImageFont.truetype(path, size=size, index=index)
        except Exception:
            continue

    raise RuntimeError(
        "CHINESE_FONT_NOT_FOUND: cannot load a Simplified Chinese font"
    )


def _verify_font() -> None:
    """Fail fast instead of silently rendering broken Chinese glyphs."""
    global _FONT_VERIFIED

    if _FONT_VERIFIED:
        return

    face = _font(36)
    canvas = Image.new("RGB", (1100, 100), "#ffffff")
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), FONT_SMOKE_TEXT, font=face)

    if bbox[2] - bbox[0] < 300 or bbox[3] - bbox[1] < 20:
        raise RuntimeError("CHINESE_FONT_GLYPH_TEST_FAILED")

    _FONT_VERIFIED = True


def _scenario(analysis: dict[str, Any], name: str) -> dict[str, Any]:
    scenarios = analysis.get("scenarios") or []
    for item in scenarios:
        if item.get("name") == name:
            return item
    return scenarios[0] if scenarios else {"candles": [], "probability": 0}


def _partial_candle(candle: dict[str, Any], progress: float) -> dict[str, Any]:
    """Grow one candle from its open price for smoother narration animation."""
    fraction = max(0.0, min(1.0, float(progress)))
    open_price = float(candle["open"])
    close_price = open_price + (
        float(candle["close"]) - open_price
    ) * fraction
    high_price = open_price + (
        float(candle["high"]) - open_price
    ) * fraction
    low_price = open_price + (
        float(candle["low"]) - open_price
    ) * fraction

    result = dict(candle)
    result["close"] = close_price
    result["high"] = max(open_price, close_price, high_price)
    result["low"] = min(open_price, close_price, low_price)
    return result


def _price(value: float) -> str:
    return f"{float(value):,.2f}"


def _utc_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _header_time_label(value: str) -> str:
    try:
        return _utc_datetime(value).strftime("%b %d, %Y %H:%M UTC")
    except Exception:
        return str(value or "")


def _video_title(timeframe: str) -> str:
    return f"Gold {str(timeframe or '').strip()} Scenario Review"


def _latest_closed_candle_label(symbol: str, data_as_of: str) -> str:
    return f"{str(symbol or '').strip()} · {_header_time_label(data_as_of)}"


def _axis_time_label(value: str) -> str:
    try:
        return _utc_datetime(value).strftime("%b %d %H:%M")
    except Exception:
        return str(value or "")[:16]


def _time_label(value: str) -> str:
    return _axis_time_label(value)


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    face: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    """按口播字幕的常规规则换行：不拆英文单词或价格数字。"""
    normalized = " ".join(str(text or "").split())
    if not normalized:
        return []

    def width(value: str) -> int:
        bbox = draw.textbbox((0, 0), value, font=face)
        return bbox[2] - bbox[0]

    if width(normalized) <= max_width:
        return [normalized]

    # 英文口播：只在词与词之间换行；优先选择两行长度最接近的切分点。
    words = normalized.split(" ")
    if len(words) > 1:
        candidates: list[tuple[int, str, str]] = []
        for split_index in range(1, len(words)):
            first = " ".join(words[:split_index])
            second = " ".join(words[split_index:])
            first_width = width(first)
            second_width = width(second)
            if first_width <= max_width and second_width <= max_width:
                candidates.append(
                    (abs(first_width - second_width), first, second)
                )

        if candidates and max_lines >= 2:
            _, first, second = min(candidates, key=lambda item: item[0])
            return [first, second]

        # 兜底时仍保持单词完整。正常字幕长度不会走到三行以上。
        lines: list[str] = []
        current: list[str] = []
        for word in words:
            trial = " ".join([*current, word])
            if current and width(trial) > max_width:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))
        return lines[:max_lines]

    # 中文没有空格时按字符换行，但价格数字与英文词仍视为不可拆分单元。
    units = re.findall(r"\d+(?:\.\d+)*|[A-Za-z]+(?:['’][A-Za-z]+)?|.", normalized)
    candidates = []
    for split_index in range(1, len(units)):
        first = "".join(units[:split_index]).strip()
        second = "".join(units[split_index:]).strip()
        first_width = width(first)
        second_width = width(second)
        if first_width <= max_width and second_width <= max_width:
            punctuation_bonus = 80 if first.endswith(("，", "。", "；", "：", ",", ";", ":")) else 0
            candidates.append(
                (abs(first_width - second_width) - punctuation_bonus, first, second)
            )

    if candidates and max_lines >= 2:
        _, first, second = min(candidates, key=lambda item: item[0])
        return [first, second]

    # 两行放不下时逐行装入，避免旧逻辑把整句原样返回并画出屏幕。
    lines: list[str] = []
    current = ""
    for unit in units:
        trial = current + unit
        if current and width(trial) > max_width:
            lines.append(current.strip())
            current = unit
        else:
            current = trial
    if current:
        lines.append(current.strip())
    return lines[:max_lines]


def _round_rect_label(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    text: str,
    face: ImageFont.FreeTypeFont,
    fill: str,
    text_fill: str = "#ffffff",
    anchor: str = "lm",
) -> None:
    bbox = draw.textbbox((0, 0), text, font=face)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    pad_x, pad_y = 13, 9

    if anchor == "rm":
        x1, x2 = x - width - pad_x * 2, x
    else:
        x1, x2 = x, x + width + pad_x * 2

    y1 = y - height / 2 - pad_y
    y2 = y + height / 2 + pad_y
    draw.rounded_rectangle(
        (x1, y1, x2, y2),
        radius=8,
        fill=fill,
    )
    draw.text(
        (x1 + pad_x, y1 + pad_y - bbox[1]),
        text,
        font=face,
        fill=text_fill,
    )


def _stacked_price_label(
    draw: ImageDraw.ImageDraw,
    x: float,
    y: float,
    name: str,
    price: str,
    face: ImageFont.FreeTypeFont,
    fill: str,
) -> None:
    """Draw a right-aligned two-line level tag: name above, price below."""
    name_box = draw.textbbox((0, 0), name, font=face)
    price_box = draw.textbbox((0, 0), price, font=face)
    width = max(name_box[2] - name_box[0], price_box[2] - price_box[0])
    line_height = max(
        name_box[3] - name_box[1],
        price_box[3] - price_box[1],
    )
    pad_x, pad_y, gap = 13, 7, 2
    box_width = width + pad_x * 2
    box_height = line_height * 2 + pad_y * 2 + gap
    x1, x2 = x - box_width, x
    y1, y2 = y - box_height / 2, y + box_height / 2
    draw.rounded_rectangle((x1, y1, x2, y2), radius=8, fill=fill)
    for line_index, (text, bbox) in enumerate(((name, name_box), (price, price_box))):
        text_width = bbox[2] - bbox[0]
        text_y = y1 + pad_y + line_index * (line_height + gap) - bbox[1]
        draw.text(
            (x1 + (box_width - text_width) / 2, text_y),
            text,
            font=face,
            fill="#ffffff",
        )


def _level_label_face(
    draw: ImageDraw.ImageDraw,
    lane_width: float,
    chart_height: float,
) -> ImageFont.FreeTypeFont:
    """Use the largest support/resistance type that fits its display lane."""
    preferred_size = round(
        min(
            LEVEL_LABEL_MAX_FONT_SIZE,
            max(
                LEVEL_LABEL_MIN_FONT_SIZE,
                min(chart_height * 0.052, lane_width * 0.32),
            ),
        )
    )
    for size in range(preferred_size, LEVEL_LABEL_MIN_FONT_SIZE - 1, -1):
        face = _font(size, True)
        widest = max(
            draw.textbbox((0, 0), label, font=face)[2]
            for label in (VIDEO_LABELS["support"], VIDEO_LABELS["resistance"])
        )
        # The coloured tag has horizontal padding on both sides; fitting only
        # the glyphs would still let it spill into the fixed price axis.
        if widest <= lane_width - 24:
            return face
    return _font(LEVEL_LABEL_MIN_FONT_SIZE, True)


def _centered_level_label(
    draw: ImageDraw.ImageDraw,
    lane_left: float,
    lane_right: float,
    center_y: float,
    name: str,
    price: str,
    face: ImageFont.FreeTypeFont,
    fill: str,
) -> None:
    """Centre a large two-line level label in the available display lane."""
    name_box = draw.textbbox((0, 0), name, font=face)
    price_box = draw.textbbox((0, 0), price, font=face)
    label_width = max(name_box[2] - name_box[0], price_box[2] - price_box[0])
    line_height = max(name_box[3] - name_box[1], price_box[3] - price_box[1])
    pad_x, pad_y, gap = 8, 7, 2
    box_width = min(lane_right - lane_left - 4, label_width + pad_x * 2)
    box_height = line_height * 2 + pad_y * 2 + gap
    center_x = (lane_left + lane_right) / 2
    x1 = center_x - box_width / 2
    y1 = center_y - box_height / 2
    draw.rounded_rectangle(
        (x1, y1, x1 + box_width, y1 + box_height),
        radius=9,
        fill=fill,
    )
    for line_index, (text, bbox) in enumerate(((name, name_box), (price, price_box))):
        text_width = bbox[2] - bbox[0]
        text_y = y1 + pad_y + line_index * (line_height + gap) - bbox[1]
        draw.text(
            (center_x - text_width / 2, text_y),
            text,
            font=face,
            fill="#ffffff",
        )


def _draw_educational_notice(
    draw: ImageDraw.ImageDraw,
    left: float,
    right: float,
    center_y: float,
) -> tuple[float, float, float, float]:
    """Draw plain persistent text inside the chart's reserved top lane."""
    available_width = max(1.0, right - left - 6)
    face = _font(18, True)
    bbox = draw.textbbox((0, 0), EDUCATIONAL_NOTICE, font=face)
    if bbox[2] - bbox[0] > available_width:
        face = _font(17, True)
        bbox = draw.textbbox((0, 0), EDUCATIONAL_NOTICE, font=face)
    if bbox[2] - bbox[0] > available_width:
        face = _font(16, True)
        bbox = draw.textbbox((0, 0), EDUCATIONAL_NOTICE, font=face)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x = (left + right - text_width) / 2
    text_y = center_y - text_height / 2 - bbox[1]
    draw.text(
        (text_x, text_y),
        EDUCATIONAL_NOTICE,
        font=face,
        fill="#7a5200",
    )
    return text_x, center_y - text_height / 2, text_x + text_width, center_y + text_height / 2


def _dashed_line(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float, float, float],
    fill: str,
    width: int = 2,
    dash: int = 10,
) -> None:
    x1, y1, x2, y2 = xy
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 0:
        return

    for start in range(0, int(length), dash * 2):
        end = min(start + dash, length)
        sx = x1 + (x2 - x1) * start / length
        sy = y1 + (y2 - y1) * start / length
        ex = x1 + (x2 - x1) * end / length
        ey = y1 + (y2 - y1) * end / length
        draw.line((sx, sy, ex, ey), fill=fill, width=width)


def _forecast_anchor_values(
    forecast: list[dict[str, Any]],
    last_close: float,
) -> list[tuple[int, float]]:
    """Keep the forecast turns while reducing one-frame candle noise."""
    closes = [float(last_close)] + [
        float(item["close"]) for item in forecast
    ]
    if len(closes) <= 2:
        return list(enumerate(closes))

    smoothed = [closes[0]]
    for index in range(1, len(closes) - 1):
        smoothed.append(
            (
                closes[index - 1] * 0.20
                + closes[index] * 0.60
                + closes[index + 1] * 0.20
            )
        )
    smoothed.append(closes[-1])

    return list(enumerate(smoothed))


def _qualitative_range_paths(
    analysis: dict[str, Any],
    forecast: list[dict[str, Any]],
    last_close: float,
) -> tuple[
    list[tuple[int, float]],
    list[tuple[int, float]],
] | None:
    """Build primary and alternate paths between structural regions."""
    supports = analysis.get("support_levels") or []
    resistances = analysis.get("resistance_levels") or []
    if not supports or not resistances or not forecast:
        return None

    support = float(supports[0])
    resistance = float(resistances[0])
    if resistance <= support:
        return None

    span = resistance - support
    low_inner = support + span * 0.10
    high_inner = resistance - span * 0.10
    centre = (support + resistance) / 2
    start = max(low_inner, min(high_inner, float(last_close)))
    end_close = float(forecast[-1]["close"])
    primary_offsets = [
        0,
        max(1, round(len(forecast) * 0.35)),
        max(2, round(len(forecast) * 0.68)),
        len(forecast),
    ]
    alternate_offsets = [
        0,
        max(1, round(len(forecast) * 0.28)),
        max(2, round(len(forecast) * 0.62)),
        len(forecast),
    ]

    # The 12 candles decide which path is primary. Each path connects decision
    # regions instead of pretending to know every future candle.
    if end_close >= last_close:
        primary_values = [
            start,
            low_inner,
            centre,
            high_inner,
        ]
        alternate_values = [
            start,
            high_inner,
            centre,
            low_inner,
        ]
    else:
        primary_values = [
            start,
            high_inner,
            centre,
            low_inner,
        ]
        alternate_values = [
            start,
            low_inner,
            centre,
            high_inner,
        ]
    return (
        list(zip(primary_offsets, primary_values)),
        list(zip(alternate_offsets, alternate_values)),
    )


def _observation_zones(
    analysis: dict[str, Any],
    plural_key: str,
) -> list[dict[str, Any]]:
    """Accept both the deployed singular facts contract and legacy arrays."""
    plural = analysis.get(plural_key)
    if isinstance(plural, list):
        return [zone for zone in plural if isinstance(zone, dict)]
    singular_key = plural_key[:-1] if plural_key.endswith("s") else plural_key
    singular = analysis.get(singular_key)
    return [singular] if isinstance(singular, dict) else []


def _recent_fvg(
    candles: list[dict[str, Any]],
) -> tuple[str, float, float, int] | None:
    """Return the newest three-candle fair-value gap in recent history."""
    start = max(2, len(candles) - 35)
    for index in range(len(candles) - 1, start - 1, -1):
        first = candles[index - 2]
        third = candles[index]
        if float(third["low"]) > float(first["high"]):
            return (
                VIDEO_LABELS["bullish_fvg"],
                float(first["high"]),
                float(third["low"]),
                index,
            )
        if float(third["high"]) < float(first["low"]):
            return (
                VIDEO_LABELS["bearish_fvg"],
                float(third["high"]),
                float(first["low"]),
                index,
            )
    return None


def _arrow_head(
    end: tuple[float, float],
    previous: tuple[float, float],
    size: float = 18,
) -> list[tuple[float, float]]:
    angle = math.atan2(end[1] - previous[1], end[0] - previous[0])
    left = (
        end[0] - size * math.cos(angle - math.pi / 6),
        end[1] - size * math.sin(angle - math.pi / 6),
    )
    right = (
        end[0] - size * math.cos(angle + math.pi / 6),
        end[1] - size * math.sin(angle + math.pi / 6),
    )
    return [end, left, right]


def _draw_clean_arrow(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    color: str,
    line_width: int,
    head_size: float,
) -> None:
    """Draw one arrowhead only, with its body ending at the triangle base."""
    if len(points) < 2:
        return
    end = points[-1]
    previous = points[-2]
    dx = end[0] - previous[0]
    dy = end[1] - previous[1]
    length = math.hypot(dx, dy)
    if length <= 0.01:
        return
    body_end = (
        end[0] - dx / length * head_size * 0.72,
        end[1] - dy / length * head_size * 0.72,
    )
    draw.line([*points[:-1], body_end], fill=color, width=line_width)
    draw.polygon(_arrow_head(end, previous, size=head_size), fill=color)


def _rank_structure_scenarios(
    forecast_paths: dict[str, Any],
) -> list[dict[str, Any]]:
    scenarios = [
        item
        for item in forecast_paths.get("scenarios") or []
        if isinstance(item, dict)
        and isinstance(item.get("path_points"), list)
    ]
    if not scenarios:
        return []

    by_id = {
        str(item.get("scenario_id") or ""): item
        for item in scenarios
    }
    ordered: list[dict[str, Any]] = []
    for key in (
        forecast_paths.get("primary_scenario"),
        forecast_paths.get("alternate_scenario"),
    ):
        item = by_id.get(str(key or ""))
        if item is not None and item not in ordered:
            ordered.append(item)

    for item in sorted(
        scenarios,
        key=lambda value: float(
            value.get("probability_prior") or 0
        ),
        reverse=True,
    ):
        if item not in ordered:
            ordered.append(item)
    return ordered


def _structure_path_values(
    scenario: dict[str, Any],
) -> list[tuple[float, float, str]]:
    values = []
    for point in scenario.get("path_points") or []:
        try:
            ratio = float(point["time_ratio"])
            value = float(point["resolved_value"])
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(ratio) and math.isfinite(value):
            values.append(
                (
                    max(0.0, min(1.0, ratio)),
                    value,
                    str(point.get("target_type") or ""),
                )
            )
    values.sort(key=lambda item: item[0])
    return values


def _limit_structure_points(
    values: list[tuple[float, float, str]],
    preserve_index: int | None = None,
) -> list[tuple[float, float, str]]:
    """Render no more than three segments while preserving key endpoints."""
    if len(values) <= 4:
        return values

    selected = {0, len(values) - 1}
    if preserve_index is not None and 0 < preserve_index < len(values) - 1:
        selected.add(preserve_index)

    candidates = [index for index in range(1, len(values) - 1)]
    while len(selected) < 4:
        remaining = [index for index in candidates if index not in selected]
        if not remaining:
            break
        # Keep the point furthest from already retained points so the reduced
        # line still represents the full early/middle/late structure.
        chosen = max(
            remaining,
            key=lambda index: min(abs(index - kept) for kept in selected),
        )
        selected.add(chosen)

    return [values[index] for index in sorted(selected)]


def _distance_to_segment(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    if dx == 0 and dy == 0:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    ratio = (
        (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
    ) / (dx * dx + dy * dy)
    ratio = max(0.0, min(1.0, ratio))
    nearest = (start[0] + ratio * dx, start[1] + ratio * dy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _simplify_visual_polyline(
    points: list[tuple[float, float]],
    angle_threshold_deg: float = FORECAST_TURN_THRESHOLD_DEG,
    deviation_threshold_px: float = 8.0,
) -> list[tuple[float, float]]:
    """Remove short, visually insignificant bends from a forecast line."""
    simplified = list(points)
    changed = True
    while changed and len(simplified) > 2:
        changed = False
        for index in range(1, len(simplified) - 1):
            previous = simplified[index - 1]
            current = simplified[index]
            following = simplified[index + 1]
            first_vector = (
                current[0] - previous[0],
                current[1] - previous[1],
            )
            second_vector = (
                following[0] - current[0],
                following[1] - current[1],
            )
            first_length = math.hypot(*first_vector)
            second_length = math.hypot(*second_vector)
            if first_length <= 0 or second_length <= 0:
                simplified.pop(index)
                changed = True
                break
            cosine = (
                first_vector[0] * second_vector[0]
                + first_vector[1] * second_vector[1]
            ) / (first_length * second_length)
            angle = math.degrees(
                math.acos(max(-1.0, min(1.0, cosine)))
            )
            if angle < angle_threshold_deg:
                simplified.pop(index)
                changed = True
                break
    return simplified


def _segment_overlaps_polyline(
    start: tuple[float, float],
    end: tuple[float, float],
    polyline: list[tuple[float, float]],
    tolerance: float = 10.0,
) -> bool:
    """Hide an alternate segment when it occupies the dashed main branch."""
    if len(polyline) < 2:
        return False
    midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    samples = (start, midpoint, end)
    return all(
        min(
            _distance_to_segment(sample, line_start, line_end)
            for line_start, line_end in zip(polyline, polyline[1:])
        )
        <= tolerance
        for sample in samples
    )


def _spread_label_positions(
    desired_positions: list[float],
    top: float,
    bottom: float,
    minimum_gap: float = 42.0,
) -> list[float]:
    """Avoid overlap while keeping labels centred near their real lines."""
    if not desired_positions:
        return []
    indexed = sorted(enumerate(desired_positions), key=lambda item: item[1])
    sorted_desired = [value for _, value in indexed]
    sorted_placed = list(sorted_desired)

    for index in range(1, len(sorted_placed)):
        sorted_placed[index] = max(
            sorted_placed[index],
            sorted_placed[index - 1] + minimum_gap,
        )

    # Re-centre the whole compact group around its original positions instead
    # of pushing every close label downward.
    average_shift = sum(
        placed - desired
        for placed, desired in zip(sorted_placed, sorted_desired)
    ) / len(sorted_placed)
    sorted_placed = [value - average_shift for value in sorted_placed]

    if sorted_placed[0] < top:
        shift = top - sorted_placed[0]
        sorted_placed = [value + shift for value in sorted_placed]
    if sorted_placed[-1] > bottom:
        shift = sorted_placed[-1] - bottom
        sorted_placed = [value - shift for value in sorted_placed]

    placed = [0.0] * len(desired_positions)
    for (original_index, _), value in zip(indexed, sorted_placed):
        placed[original_index] = value
    return placed


def _scenario_text(value: str) -> str:
    return {
        "sideways": VIDEO_LABELS["range_bound"],
        "up": VIDEO_LABELS["bullish"],
        "down": VIDEO_LABELS["bearish"],
        "resistance_break": "Resistance Break",
        "resistance_hold": "Resistance Hold",
        "support_break": "Support Break",
        "support_hold": "Support Hold",
    }.get(str(value or ""), VIDEO_LABELS["conditional_path"])


def resolve_safe_layout(width: int, height: int, platform: str) -> dict[str, int]:
    """Keep critical TikTok content outside platform overlay areas."""
    if str(platform or "").strip().lower() == "tiktok" and (width, height) == (1080, 1920):
        return {
            "safe_top": round(height * 0.135),
            "safe_bottom": height - round(height * 0.25),
            # Keep the complete chart, price axis and level labels inside
            # equal left/right margins, away from TikTok's right controls.
            "safe_right": width - round(width * 0.06),
            "safe_left": round(width * 0.06),
        }
    return {
        "safe_top": 0,
        "safe_bottom": height,
        "safe_right": width,
        "safe_left": 0,
    }


def _history_end_sec(payload: dict[str, Any], duration: float) -> float:
    for cue in (payload.get("narration") or {}).get("subtitle_cues") or []:
        if str(cue.get("segment_id") or "") == "technical_evidence":
            try:
                return min(duration, max(0.1, float(cue["end_sec"])))
            except (KeyError, TypeError, ValueError):
                break
    try:
        ratio = float((payload.get("timeline") or {}).get("history_ratio", 0.20))
    except (TypeError, ValueError):
        ratio = 0.20
    if not 0.10 <= ratio <= 0.23:
        ratio = 0.20
    return max(0.1, duration * ratio)


HISTORY_SEGMENT_IDS = {"opening", "context", "technical_evidence"}
PREDICTION_SEGMENT_IDS = {
    "resistance_break", "resistance_hold", "support_break", "support_hold",
}
PREDICTION_SEGMENT_ORDER = (
    "resistance_break", "resistance_hold", "support_break", "support_hold",
)
PREDICTION_SEGMENT_COLORS = {
    "resistance_break": "#00a86b",
    "resistance_hold": "#f59e0b",
    "support_break": "#e53935",
    "support_hold": "#2962ff",
}
PREDICTION_SEGMENT_LABELS = {
    "resistance_break": "R Break",
    "resistance_hold": "R Hold",
    "support_break": "S Break",
    "support_hold": "S Hold",
}


def _segment_state(
    narration: dict[str, Any],
    current_time: float,
) -> tuple[str, float]:
    """Return the aligned segment id and progress, holding it through pauses."""
    cues = [
        cue for cue in narration.get("subtitle_cues") or []
        if isinstance(cue, dict) and str(cue.get("segment_id") or "")
    ]
    cues.sort(key=lambda cue: float(cue.get("start_sec") or 0))
    selected = None
    for cue in cues:
        if float(cue.get("start_sec") or 0) <= current_time:
            selected = cue
        else:
            break
    if selected is None:
        return "", 0.0
    start = float(selected.get("start_sec") or 0)
    end = max(start + 0.001, float(selected.get("end_sec") or start))
    progress = max(0.0, min(1.0, (current_time - start) / (end - start)))
    segment_id = str(
        selected.get("parent_segment_id")
        or selected.get("segment_id")
        or ""
    )
    if segment_id not in HISTORY_SEGMENT_IDS | PREDICTION_SEGMENT_IDS | {"macro_event", "closing"}:
        segment_id = re.sub(r"_\d+$", "", segment_id)
    return segment_id, progress


def _segment_start_sec(
    narration: dict[str, Any],
    segment_id: str,
    fallback: float,
) -> float:
    for cue in narration.get("subtitle_cues") or []:
        cue_segment_id = str(
            cue.get("parent_segment_id") or cue.get("segment_id") or ""
        )
        if cue_segment_id == segment_id or re.sub(r"_\d+$", "", cue_segment_id) == segment_id:
            try:
                return max(0.1, float(cue["start_sec"]))
            except (KeyError, TypeError, ValueError):
                break
    return max(0.1, float(fallback))


def _latest_prediction_segment_id(
    narration: dict[str, Any],
    current_time: float,
) -> str:
    """Return the most recent branch cue so macro pauses keep its final path."""
    latest_id = ""
    latest_start = -1.0
    for cue in narration.get("subtitle_cues") or []:
        if not isinstance(cue, dict):
            continue
        try:
            start = float(cue.get("start_sec") or 0)
        except (TypeError, ValueError):
            continue
        segment_id = str(
            cue.get("parent_segment_id") or cue.get("segment_id") or ""
        )
        segment_id = re.sub(r"_\d+$", "", segment_id)
        if (
            segment_id in PREDICTION_SEGMENT_IDS
            and start <= current_time
            and start >= latest_start
        ):
            latest_id = segment_id
            latest_start = start
    return latest_id


def _history_window(
    history: list[dict[str, Any]],
    current_time: float,
    freeze_start_sec: float,
    source_count: int,
    window_count: int,
) -> list[dict[str, Any]]:
    """Reveal recent candles quickly, then roll while keeping the latest window."""
    source = list(history[-max(source_count, window_count):])
    if not source:
        return []
    progress = max(0.0, min(1.0, current_time / max(freeze_start_sec, 0.1)))
    initial_count = min(8, len(source), window_count)
    end_position = initial_count + progress * (len(source) - initial_count)
    full_end = int(math.floor(end_position))
    start = max(0, full_end - window_count)
    visible = list(source[start:full_end])
    fraction = end_position - full_end
    if full_end < len(source) and fraction > 0:
        visible.append(_partial_candle(source[full_end], fraction))
        if len(visible) > window_count:
            visible = visible[-window_count:]
    return visible or source[:initial_count]


def _partial_polyline(
    points: list[tuple[float, float]],
    progress: float,
) -> list[tuple[float, float]]:
    """Reveal a polyline continuously over one aligned narration segment."""
    if len(points) < 2 or progress <= 0:
        return points[:1]
    if progress >= 1:
        return points
    scaled = progress * (len(points) - 1)
    completed = int(math.floor(scaled))
    fraction = scaled - completed
    visible = list(points[:completed + 1])
    start = points[completed]
    end = points[min(completed + 1, len(points) - 1)]
    visible.append((
        start[0] + (end[0] - start[0]) * fraction,
        start[1] + (end[1] - start[1]) * fraction,
    ))
    return visible


def _segment_visual_path(
    forecast_paths: dict[str, Any],
    segment_id: str,
) -> list[dict[str, Any]]:
    paths = forecast_paths.get("segment_paths") or {}
    value = paths.get(segment_id)
    return list(value) if isinstance(value, list) else []


def _visible_segment_paths(
    forecast_paths: dict[str, Any],
    narration: dict[str, Any],
    current_time: float,
) -> list[tuple[str, list[dict[str, Any]], float]]:
    """Return every started branch; completed branches remain fully visible."""
    cue_bounds: dict[str, list[tuple[float, float]]] = {}
    for cue in narration.get("subtitle_cues") or []:
        if not isinstance(cue, dict):
            continue
        segment_id = re.sub(
            r"_\d+$", "",
            str(cue.get("parent_segment_id") or cue.get("segment_id") or ""),
        )
        if segment_id not in PREDICTION_SEGMENT_IDS:
            continue
        try:
            start = float(cue.get("start_sec") or 0)
            end = max(start + 0.001, float(cue.get("end_sec") or start))
        except (TypeError, ValueError):
            continue
        cue_bounds.setdefault(segment_id, []).append((start, end))

    visible = []
    for segment_id in PREDICTION_SEGMENT_ORDER:
        if segment_id not in cue_bounds:
            continue
        intervals = sorted(cue_bounds[segment_id])
        start = intervals[0][0]
        if current_time < start:
            continue
        # Show the complete path as soon as its narration segment starts.
        # This keeps segment timing while removing the slow drawing animation.
        progress = 1.0
        path = _segment_visual_path(forecast_paths, segment_id)
        if path:
            visible.append((segment_id, path, progress))
    return visible


def _prediction_phase_paths(
    forecast_paths: dict[str, Any],
) -> list[tuple[str, list[dict[str, Any]], float]]:
    """Return every available prediction path fully visible."""
    visible = []
    for segment_id in PREDICTION_SEGMENT_ORDER:
        path = _segment_visual_path(forecast_paths, segment_id)
        if path:
            visible.append((segment_id, path, 1.0))
    return visible


def _subtitle_display_chunks(text: str, max_words: int = 1) -> list[str]:
    """Return a single visible English word at a time."""
    del max_words
    return str(text or "").split()


def _subtitle_at(
    narration: dict[str, Any],
    current_time: float,
    progress: float,
) -> str:
    # 字幕时间来自 WhisperX 的逐词对齐，必须与当前视频时间完全一致。
    # 不再提前显示，否则会让字幕抢在口播前出现。
    subtitle_time = current_time
    subtitle_cues = narration.get("subtitle_cues") or []
    for cue in subtitle_cues:
        start = float(cue.get("start_sec") or 0)
        end = float(cue.get("end_sec") or 0)
        if start <= subtitle_time < end:
            aligned_words = cue.get("word_timings") or []
            if isinstance(aligned_words, list) and aligned_words:
                latest_started_word = ""
                for item in aligned_words:
                    if not isinstance(item, dict):
                        continue
                    try:
                        word_start = float(item["start_sec"])
                        word_end = float(item["end_sec"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    word_text = str(item.get("text") or "").strip()
                    if not word_text:
                        continue
                    if word_start <= subtitle_time < word_end:
                        return word_text
                    if word_start <= subtitle_time:
                        latest_started_word = word_text
                # Between real spoken words, hold the last spoken word rather
                # than jumping ahead to the next one during a natural pause.
                return latest_started_word
            words = _subtitle_display_chunks(str(cue.get("text") or ""))
            if not words:
                return ""
            cue_progress = (subtitle_time - start) / max(end - start, 0.001)
            word_index = min(
                len(words) - 1,
                max(0, int(cue_progress * len(words))),
            )
            return words[word_index]

    # 已提供正式时间轴时，时间轴外（例如口播结束后的尾帧）必须没有字幕。
    # 不能退回显示最后一段旁白，否则结尾会残留风险提示文字。
    if subtitle_cues:
        return ""

    segments = sorted(
        narration.get("segments") or [],
        key=lambda item: item.get("order", 0),
    )
    if segments:
        index = min(int(progress * len(segments)), len(segments) - 1)
        return str(segments[index].get("text") or "")

    return str(narration.get("full_text") or "")


def _chinese_subtitle_at(
    narration: dict[str, Any],
    current_time: float,
) -> str:
    """Resolve display-only Chinese by the active English cue's segment id."""
    active_segment_id = ""
    for cue in narration.get("subtitle_cues") or []:
        if not isinstance(cue, dict):
            continue
        start = float(cue.get("start_sec") or 0)
        end = float(cue.get("end_sec") or 0)
        if start <= current_time < end:
            active_segment_id = re.sub(
                r"_\d+$", "",
                str(cue.get("parent_segment_id") or cue.get("segment_id") or ""),
            )
            break
    if not active_segment_id:
        return ""
    for segment in narration.get("bilingual_segments") or []:
        if isinstance(segment, dict) and str(segment.get("segment_id") or "") == active_segment_id:
            return str(segment.get("chinese_text") or "").strip()
    return ""


def _mix_hex_color(first: str, second: str, ratio: float) -> str:
    """Mix two #RRGGBB colours for the active-arrow flash effect."""
    ratio = max(0.0, min(1.0, float(ratio)))
    first = first.lstrip("#")
    second = second.lstrip("#")
    return "#" + "".join(
        f"{round(int(first[index:index + 2], 16) * (1 - ratio) + int(second[index:index + 2], 16) * ratio):02x}"
        for index in range(0, 6, 2)
    )


def _prediction_arrow_style(
    segment_id: str,
    active_segment_id: str,
    current_time: float,
) -> tuple[str, int, int]:
    """Flash only the path currently being explained by narration."""
    base_color = PREDICTION_SEGMENT_COLORS[segment_id]
    if segment_id != active_segment_id:
        return base_color, 5, 17

    pulse = (math.sin(float(current_time) * math.tau * 2.2) + 1.0) / 2.0
    return (
        _mix_hex_color(base_color, "#ffffff", 0.22 + pulse * 0.50),
        6 + round(pulse * 4),
        19 + round(pulse * 5),
    )


def _subtitle_layout(
    width: int,
    height: int,
    safe: dict[str, int],
    is_tiktok_safe: bool,
) -> dict[str, int]:
    """Reserve a top review lane and a lower centered English subtitle lane."""
    del width
    english_y = round(height * (0.79 if is_tiktok_safe else 0.84))
    english_y = min(height - 120, max(220, english_y))
    chart_top = safe["safe_top"] + 12 if is_tiktok_safe else 12
    chart_bottom = max(chart_top + 300, english_y - round(height * 0.095))
    # The review Chinese belongs in the dedicated blank band above the chart,
    # centred in the visible area shown in the audit reference frame.
    review_chinese_y = 34 if is_tiktok_safe else 34
    return {
        "chart_top": chart_top,
        "chart_bottom": chart_bottom,
        "english_y": english_y,
        "review_chinese_y": review_chinese_y,
    }


def _cue_start(
    narration: dict[str, Any],
    keywords: tuple[str, ...],
    fallback_sec: float,
) -> float:
    """Find when narration first mentions a visual concept."""
    for cue in narration.get("subtitle_cues") or []:
        text = str(cue.get("text") or "")
        if any(keyword in text for keyword in keywords):
            return max(0.0, float(cue.get("start_sec") or 0))
    return max(0.0, float(fallback_sec))


def render_tradingview_scene(
    path: Path,
    payload: dict[str, Any],
    scene_index: int,
    total_scenes: int,
    current_time_sec: float | None = None,
) -> None:
    """Render a clean TradingView-inspired frame without changing narration."""
    _verify_font()

    width = int(payload["video"]["width"])
    height = int(payload["video"]["height"])
    safe = resolve_safe_layout(
        width,
        height,
        payload.get("platform_profile", ""),
    )
    is_tiktok_safe = safe["safe_top"] > 0
    duration = float(
        payload.get("render_duration_sec")
        or payload.get("duration_target_sec")
        or payload.get("test_duration_sec")
        or 60
    )
    current_time = (
        float(current_time_sec)
        if current_time_sec is not None
        else scene_index / max(total_scenes - 1, 1) * duration
    )
    progress = max(0.0, min(1.0, current_time / max(duration, 0.1)))

    analysis = payload["analysis_forecast"]
    all_history = payload["historical_candles"]
    timeline = payload.get("timeline") or {}
    segment_sync = timeline.get("visual_sync_strategy") == "segment-id-v1"
    history_source_count = int(timeline.get("history_source_candles") or 90)
    history_window_count = int(timeline.get("history_window_candles") or 70)
    history_scale_source = list(all_history[-history_source_count:])
    history = list(all_history[-history_window_count:])
    narration = payload["narration"]
    active_segment_id, active_segment_progress = _segment_state(
        narration,
        current_time,
    )
    visual_segment_id = active_segment_id
    if segment_sync and active_segment_id == "macro_event":
        visual_segment_id = (
            _latest_prediction_segment_id(narration, current_time)
            or active_segment_id
        )
    selected = _scenario(analysis, payload["style"]["scenario"])
    show_alternate_path = bool(
        payload["style"].get("show_alternate_path", True)
    )
    forecast_all = selected.get("candles") or []
    forecast_paths = payload.get("forecast_paths") or {}
    ranked_structure_scenarios = _rank_structure_scenarios(
        forecast_paths
    )
    active_segment_path = _segment_visual_path(
        forecast_paths,
        visual_segment_id,
    )
    # Only show the branch being explained right now. Keeping old branches on
    # screen makes one arrow look like it has extra flat and pointed heads.
    cumulative_segment_paths = (
        [(visual_segment_id, active_segment_path, 1.0)]
        if (
            segment_sync
            and visual_segment_id in PREDICTION_SEGMENT_IDS
            and active_segment_path
        )
        else []
    )
    if (
        segment_sync
        and visual_segment_id in PREDICTION_SEGMENT_IDS
        and active_segment_path
    ):
        visual_scenario_id = {
            "resistance_break": "up",
            "resistance_hold": "down",
            "support_break": "down",
            "support_hold": "up",
        }[visual_segment_id]
        ranked_structure_scenarios = [{
            "scenario_id": visual_scenario_id,
            "path_points": active_segment_path,
        }]
    raw_primary_structure_values = (
        _structure_path_values(ranked_structure_scenarios[0])
        if ranked_structure_scenarios
        else []
    )
    primary_touch_branch = (
        ranked_structure_scenarios[0].get("touch_branch")
        if ranked_structure_scenarios
        else None
    )
    primary_touch_origin = None
    primary_touch_next = None
    preserve_index = None
    if isinstance(primary_touch_branch, dict):
        try:
            preserve_index = int(
                primary_touch_branch["origin_order"]
            ) - 1
            primary_touch_origin = raw_primary_structure_values[
                preserve_index
            ]
            if preserve_index + 1 < len(raw_primary_structure_values):
                primary_touch_next = raw_primary_structure_values[
                    preserve_index + 1
                ]
        except (KeyError, IndexError, TypeError, ValueError):
            preserve_index = None
            primary_touch_origin = None
            primary_touch_next = None
    primary_structure_values = _limit_structure_points(
        raw_primary_structure_values,
        preserve_index,
    )
    alternate_structure_values = _limit_structure_points(
        _structure_path_values(ranked_structure_scenarios[1])
        if (
            show_alternate_path
            and len(ranked_structure_scenarios) > 1
        )
        else [],
    )

    support_start_sec = _cue_start(
        narration,
        ("support", "Support"),
        duration * 0.34,
    )
    resistance_start_sec = _cue_start(
        narration,
        ("resistance", "Resistance"),
        duration * 0.40,
    )
    level_start_sec = min(support_start_sec, resistance_start_sec)
    if segment_sync:
        prediction_phase = (
            active_segment_id in PREDICTION_SEGMENT_IDS
            or active_segment_id in {"macro_event", "closing"}
        )
        reveal_progress = (
            active_segment_progress
            if active_segment_id in PREDICTION_SEGMENT_IDS
            else 1.0 if prediction_phase else 0.0
        )
    else:
        prediction_start_sec = _cue_start(
            narration,
            (
                "base scenario", "prediction phase", "future path",
                "next we show", "conditional path", "forecast path",
                "structure path", "next five hours",
            ),
            duration * 0.62,
        )
        prediction_start = max(
            0.05,
            min(0.90, prediction_start_sec / max(duration, 0.1)),
        )
        prediction_phase = progress >= prediction_start
        reveal_progress = max(
            0.0,
            min(1.0, (progress - prediction_start) / 0.28),
        ) if prediction_phase else 0.0
    forecast_position = min(
        float(len(forecast_all)),
        reveal_progress * len(forecast_all),
    ) if prediction_phase else 0.0

    if prediction_phase:
        # Analysis close-up: retain enough recent closed candles for a dense
        # chart while enlarging price action and conditional paths.
        visible_history = list(history[-ANALYSIS_ZOOM_CANDLES:])
    else:
        if segment_sync:
            freeze_start = _segment_start_sec(
                narration,
                str(timeline.get("history_freeze_segment") or "technical_evidence"),
                _history_end_sec(payload, duration) * 0.65,
            )
            if active_segment_id == "technical_evidence" or current_time >= freeze_start:
                visible_history = list(history)
            else:
                visible_history = _history_window(
                    all_history,
                    current_time,
                    freeze_start,
                    history_source_count,
                    history_window_count,
                )
        else:
            # Backward-compatible reveal for old timeline contracts.
            initial_history_count = min(8, len(history))
            history_reveal_end_sec = _history_end_sec(payload, duration)
            history_reveal = max(
                0.0,
                min(1.0, current_time / history_reveal_end_sec),
            )
            history_position = min(
                float(len(history)),
                initial_history_count
                + history_reveal
                * max(len(history) - initial_history_count, 0),
            )
            history_count = int(math.floor(history_position))
            visible_history = list(history[:history_count])
            history_fraction = history_position - history_count
            if history_count < len(history) and history_fraction > 0:
                visible_history.append(
                    _partial_candle(history[history_count], history_fraction)
                )
    # Forecast OHLC values stay internal. The screen only shows an abstract
    # bent trend arrow, never precise future candles, prices or timestamps.
    candles = visible_history

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    # TikTok overlay areas intentionally remain plain white. Critical content
    # is drawn only inside resolve_safe_layout().

    axis_face = _font(AXIS_FONT_SIZE, True)
    label_face = _font(CHART_LABEL_FONT_SIZE, True)
    subtitle_face = _font(SUBTITLE_EN_FONT_SIZE, True)
    subtitle_zh_face = _font(REVIEW_CHINESE_FONT_SIZE, True)

    chart_left = max(48, safe["safe_left"])
    layout = _subtitle_layout(width, height, safe, is_tiktok_safe)
    chart_top = layout["chart_top"]
    chart_bottom = layout["chart_bottom"]
    # Three fixed horizontal lanes: chart, coloured support/resistance tags,
    # then grey price scale. They never share pixels or move with prediction.
    safe_right = safe["safe_right"] if is_tiktok_safe else width
    level_lane_width = max(150, round(width * 0.15))
    price_lane_width = max(132, round(width * 0.13))
    chart_right = safe_right - level_lane_width - price_lane_width - 14
    level_lane_left = chart_right + 8
    level_lane_right = level_lane_left + level_lane_width
    price_axis_x = level_lane_right + 10
    time_axis_y = chart_bottom
    # White canvas already supplies the chart background. Do not draw a frame.

    # Freeze the value range before the first frame. Otherwise the y-axis
    # numbers change as future arrows appear, which makes the chart feel like
    # both axes are moving.
    scale_candles = history_scale_source or history or visible_history
    prices = [
        float(candle[key])
        for candle in scale_candles
        for key in ("high", "low")
    ]
    for level_key in ("support_levels", "resistance_levels"):
        prices.extend(float(value) for value in (analysis.get(level_key) or []))
    for zone_key in ("potential_buy_zones", "potential_sell_zones"):
        for zone in _observation_zones(analysis, zone_key):
            try:
                prices.extend([float(zone["low"]), float(zone["high"])])
            except (KeyError, TypeError, ValueError):
                continue
    for scenario in _rank_structure_scenarios(forecast_paths):
        prices.extend(
            value for _, value, _ in _structure_path_values(scenario)
        )
    prices.extend(
        float(candle[key])
        for candle in forecast_all
        for key in ("high", "low")
        if key in candle
    )
    if not prices:
        prices = [0.0, 1.0]

    pmin, pmax = min(prices), max(prices)
    price_span = max(pmax - pmin, 0.01)
    camera_padding = 0.06
    pmin -= price_span * camera_padding
    pmax += price_span * camera_padding

    def py(value: float) -> float:
        return chart_bottom - 78 - (
            (float(value) - pmin)
            / max(pmax - pmin, 0.01)
            * (chart_bottom - chart_top - 112)
        )

    # A price label must visibly point back to the chart. Keep the guides very
    # light so they work as axis ticks rather than competing grid lines.
    draw.line(
        (chart_right, chart_top + 24, chart_right, chart_bottom),
        fill="#d9dde5",
        width=1,
    )
    for grid_index in range(6):
        fraction = grid_index / 5
        y = chart_top + 28 + fraction * (chart_bottom - chart_top - 62)
        value = pmax - fraction * (pmax - pmin)
        _dashed_line(
            draw,
            (chart_left, y, chart_right, y),
            fill="#edf0f4",
            width=1,
            dash=5,
        )
        draw.line(
            (chart_right - 9, y, chart_right + 9, y),
            fill="#9aa1ad",
            width=2,
        )
        draw.text(
            (price_axis_x, y),
            _price(value),
            font=axis_face,
            fill="#5f6470",
            anchor="lm",
        )
    count = max(len(candles), 1)
    chart_width = chart_right - chart_left
    # During prediction, start the forecast at 40% of the chart instead of
    # 60%, shifting the full arrow area left by half of its former width.
    history_end_ratio = PREDICTION_HISTORY_END_RATIO if prediction_phase else 0.76
    history_end_x = chart_left + chart_width * history_end_ratio
    horizontal_history_count = (
        len(visible_history) if prediction_phase else len(history)
    )
    history_slot = (
        history_end_x - chart_left - 18
    ) / max(horizontal_history_count, 1)
    forecast_slot = (
        chart_right - history_end_x - 14
    ) / max(len(forecast_all), 1)
    # No forecast camera push: historical candles stay in place while the
    # prediction occupies the remaining right-side slots.
    camera_progress = 0.0
    slot = forecast_slot if prediction_phase else history_slot
    body_width = max(3, min(10, int(history_slot * 0.68)))

    def px(index: int) -> float:
        history_last_index = max(horizontal_history_count - 1, 1)
        if index <= horizontal_history_count - 1:
            return (
                chart_left
                + 10
                + (history_end_x - chart_left - 20)
                * index
                / history_last_index
            )
        forecast_offset = index - (horizontal_history_count - 1)
        return min(
            chart_right - 8,
            history_end_x + forecast_slot * forecast_offset,
        )

    # Time labels are a fixed coordinate scale. They use fixed locations and
    # a fixed historical range, so the x-axis cannot slide left while the
    # forecast is moved into its earlier display area.
    # Two well-spaced labels stay readable on a vertical short video. More
    # labels would overlap each other at the fixed chart width.
    time_marks = 2
    draw.line(
        (chart_left, time_axis_y, chart_right, time_axis_y),
        fill="#d9dde5",
        width=1,
    )
    axis_time_candles = history or candles
    first_time_index = 0
    last_time_index = max(len(axis_time_candles) - 1, 0)
    for mark_index in range(time_marks):
        candle_index = round(
            first_time_index
            + mark_index
            * (last_time_index - first_time_index)
            / max(time_marks - 1, 1)
        )
        x = chart_left + chart_width * mark_index / max(time_marks - 1, 1)
        if candle_index < len(axis_time_candles):
            label = _time_label(axis_time_candles[candle_index].get("time", ""))
            bbox = draw.textbbox((0, 0), label, font=axis_face)
            half_width = (bbox[2] - bbox[0]) / 2
            tick_x = min(chart_right, max(chart_left, x))
            draw.line(
                (tick_x, time_axis_y, tick_x, time_axis_y + 10),
                fill="#9aa1ad",
                width=2,
            )
            draw.text(
                (
                    min(
                        chart_right - half_width,
                        max(chart_left + half_width, x),
                    ),
                    time_axis_y + 18,
                ),
                label,
                font=axis_face,
                fill="#787b86",
                anchor="ma",
            )

    # Key levels and their support/resistance observation zones appear after
    # the historical overview. The coloured bands are behind candles, lines
    # and arrows, so the complete price structure stays readable.
    # Observation zones are structural context, so keep them present in every
    # frame instead of hiding them before a particular narration segment.
    zones_visible = payload["style"].get("show_observation_zones", True)
    if zones_visible:
        zone_draw = ImageDraw.Draw(image, "RGBA")
        for zone_key, fill, outline, label in (
            ("potential_buy_zones", (41, 98, 255, 58), "#2962ff", "Support zone"),
            ("potential_sell_zones", (245, 158, 11, 58), "#f59e0b", "Resistance zone"),
        ):
            for zone in _observation_zones(analysis, zone_key):
                try:
                    low = float(zone["low"])
                    high = float(zone["high"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not math.isfinite(low) or not math.isfinite(high) or low >= high:
                    continue
                top, bottom = sorted((py(high), py(low)))
                zone_draw.rectangle(
                    (chart_left, top, chart_right, bottom),
                    fill=fill,
                    outline=outline,
                    width=2,
                )
                zone_draw.text(
                    (chart_left + 12, top + 8),
                    label,
                    font=label_face,
                    fill=outline,
                )

    levels_to_show = []
    if (
        payload["style"].get("show_support_resistance", True)
        and (current_time >= level_start_sec or prediction_phase)
    ):
        # Support and resistance run through the full chart.
        level_start_x = chart_left
        supports = analysis.get("support_levels") or []
        resistances = analysis.get("resistance_levels") or []
        if supports and current_time >= support_start_sec:
            levels_to_show.append((supports[0], "#2962ff", VIDEO_LABELS["support"]))
        if resistances and prediction_phase:
            levels_to_show.append((resistances[0], "#f59e0b", VIDEO_LABELS["resistance"]))

        for level, color, name in levels_to_show:
            y = py(float(level))
            _dashed_line(
                draw,
                (level_start_x, y, chart_right, y),
                fill=color,
                width=3,
                dash=10,
            )

    # Candles are the only chart-layer objects besides axis information,
    # support/resistance lines and the currently narrated prediction arrow.
    for index, candle in enumerate(candles):
        x = px(index)
        if x < chart_left - body_width or x > chart_right + body_width:
            continue
        rising = float(candle["close"]) >= float(candle["open"])
        color = "#089981" if rising else "#f23645"

        draw.line(
            (x, py(candle["high"]), x, py(candle["low"])),
            fill=color,
            width=2,
        )
        open_y = py(candle["open"])
        close_y = py(candle["close"])
        y1, y2 = min(open_y, close_y), max(open_y, close_y)
        y2 = max(y2, y1 + 3)

        draw.rectangle(
            (x - body_width / 2, y1, x + body_width / 2, y2),
            fill=color,
        )

    # New structure-path contract: only the path currently being narrated is
    # drawn. Legends, FVGs and auxiliary labels are deliberately removed.
    structured_path_rendered = bool(
        prediction_phase and cumulative_segment_paths
    )

    if (
        prediction_phase
        and not structured_path_rendered
        and len(primary_structure_values) >= 2
        and reveal_progress > 0
    ):
        forecast_left = history_end_x + 4
        forecast_right = chart_right - 18

        def structure_points(
            values: list[tuple[float, float, str]],
        ) -> list[tuple[float, float]]:
            return [
                (
                    forecast_left
                    + ratio * (forecast_right - forecast_left),
                    py(value),
                )
                for ratio, value, _ in values
            ]

        primary_points = structure_points(primary_structure_values)
        alternate_points = structure_points(
            alternate_structure_values
        )
        primary_points = _simplify_visual_polyline(primary_points)
        alternate_points = _simplify_visual_polyline(alternate_points)
        if segment_sync and visual_segment_id in PREDICTION_SEGMENT_IDS:
            visible_primary = _partial_polyline(
                primary_points,
                reveal_progress if active_segment_id in PREDICTION_SEGMENT_IDS else 1.0,
            )
            visible_alternate = []
        else:
            visible_primary = primary_points
            visible_alternate = alternate_points

        branch_points: list[tuple[float, float]] = []
        branch_direction = ""
        if isinstance(primary_touch_branch, dict):
            try:
                end_ratio = float(
                    primary_touch_branch["end_time_ratio"]
                )
                end_value = float(
                    primary_touch_branch["resolved_value"]
                )
                if primary_touch_origin is None:
                    raise ValueError("missing touch origin")
                origin = structure_points([primary_touch_origin])[0]
                branch_end = (
                    forecast_left
                    + end_ratio * (forecast_right - forecast_left),
                    py(end_value),
                )
                branch_points = [origin, branch_end]
                branch_direction = str(
                    primary_touch_branch.get("direction") or ""
                )
            except (KeyError, IndexError, TypeError, ValueError):
                branch_points = []
                branch_direction = ""

        # The model may independently produce a touch outcome that lands on
        # the same geometry as the already selected main path. Keep the
        # probability result in the payload, but do not paint a dashed copy on
        # top of the solid main line because that makes the main path appear
        # dashed.
        branch_duplicates_primary = False
        if len(branch_points) == 2:
            branch_duplicates_primary = _segment_overlaps_polyline(
                branch_points[0],
                branch_points[1],
                visible_primary,
            )
            if primary_touch_next is not None:
                main_next = structure_points([primary_touch_next])[0]
                origin = branch_points[0]
                main_next_direction = (
                    "up"
                    if main_next[1] < origin[1] - 2
                    else "down"
                    if main_next[1] > origin[1] + 2
                    else "flat"
                )
                # Arrival time can differ even when both outcomes point to
                # the same price level. Treat that as the same visual result.
                if (
                    main_next_direction == branch_direction
                    and abs(main_next[1] - branch_points[1][1]) <= 10
                ):
                    branch_duplicates_primary = True
        visible_branch_points = (
            [] if branch_duplicates_primary else branch_points
        )

        # If the runner-up starts from the same turning point and moves in the
        # same direction as the dashed condition branch, it adds no new visual
        # information. Hide the whole runner-up in that case.
        alternate_duplicates_branch = False
        if len(visible_branch_points) == 2 and len(visible_alternate) >= 2:
            branch_origin = visible_branch_points[0]
            for point, next_point in zip(
                visible_alternate,
                visible_alternate[1:],
            ):
                if _distance_to_segment(
                    branch_origin,
                    point,
                    next_point,
                ) > 14:
                    continue
                alternate_direction = (
                    "up"
                    if next_point[1] < point[1] - 2
                    else "down"
                    if next_point[1] > point[1] + 2
                    else "flat"
                )
                if alternate_direction == branch_direction:
                    alternate_duplicates_branch = True
                    break

        if (
            len(visible_alternate) >= 2
            and not alternate_duplicates_branch
        ):
            alternate_color = "#e53935"
            draw.line(
                visible_alternate,
                fill=alternate_color,
                width=3,
            )
            if len(visible_alternate) >= 2:
                draw.polygon(
                    _arrow_head(
                        visible_alternate[-1],
                        visible_alternate[-2],
                        size=14,
                    ),
                    fill=alternate_color,
                )

        if len(visible_primary) >= 2:
            primary_color = "#00a86b"
            # The main forecast never changes style. The dashed line is a
            # separate conditional outcome generated from its level touch.
            draw.line(visible_primary, fill=primary_color, width=5)
            draw.polygon(
                _arrow_head(
                    visible_primary[-1],
                    visible_primary[-2],
                    size=19,
                ),
                fill=primary_color,
            )
            if len(visible_branch_points) == 2:
                _dashed_line(
                    draw,
                    (
                        *visible_branch_points[0],
                        *visible_branch_points[1],
                    ),
                    fill=primary_color,
                    width=3,
                    dash=8,
                )
                draw.polygon(
                    _arrow_head(
                        visible_branch_points[1],
                        visible_branch_points[0],
                        size=11,
                    ),
                    fill=primary_color,
                )
        structured_path_rendered = True

    # Backward-compatible fallback: old requests still derive the path from
    # forecast candles until every Dify environment has migrated.
    if (
        prediction_phase
        and not structured_path_rendered
        and forecast_all
        and reveal_progress > 0
    ):
        last_close = float(visible_history[-1]["close"])
        use_range_path = (
            selected.get("name") == "base"
            and analysis.get("trend") in ("sideways", "mixed")
        )
        range_paths = (
            _qualitative_range_paths(
                analysis,
                forecast_all,
                last_close,
            )
            if use_range_path
            else None
        )
        if range_paths:
            anchors, alternate_anchors = range_paths
        else:
            anchors = _forecast_anchor_values(forecast_all, last_close)
            alternate_anchors = []
        anchors = [
            (int(offset), value)
            for offset, value, _ in _limit_structure_points(
                [
                    (float(offset), float(value), "")
                    for offset, value in anchors
                ]
            )
        ]
        alternate_anchors = [
            (int(offset), value)
            for offset, value, _ in _limit_structure_points(
                [
                    (float(offset), float(value), "")
                    for offset, value in alternate_anchors
                ]
            )
        ]
        raw_trend_points = [
            (
                px(len(visible_history) - 1 + offset),
                py(value),
            )
            for offset, value in anchors
        ]
        base_y = raw_trend_points[0][1]
        raw_span = max(
            point[1] for point in raw_trend_points
        ) - min(point[1] for point in raw_trend_points)
        visual_boost = (
            1.0
            if use_range_path
            else min(
                3.8,
                max(1.35, 135 / max(raw_span, 1)),
            )
        )
        trend_points = [
            (
                x,
                max(
                    chart_top + 160,
                    min(
                        chart_bottom - 125,
                        base_y + (y - base_y) * visual_boost,
                    ),
                ),
            )
            for x, y in raw_trend_points
        ]
        trend_points = _simplify_visual_polyline(trend_points)
        visible_trend = trend_points
        alternate_points = [
            (
                px(len(visible_history) - 1 + offset),
                py(value),
            )
            for offset, value in alternate_anchors
        ]
        alternate_points = _simplify_visual_polyline(alternate_points)
        visible_alternate = alternate_points

        if len(visible_trend) >= 2:
            start_value = anchors[0][1]
            end_value = anchors[-1][1]
            neutral_threshold = max((pmax - pmin) * 0.025, 0.01)
            if use_range_path:
                trend_color = "#00a86b"
                trend_text = VIDEO_LABELS["two_way_scenario"]
            elif end_value > start_value + neutral_threshold:
                trend_color = "#00a86b"
                trend_text = VIDEO_LABELS["range_bullish"]
            elif end_value < start_value - neutral_threshold:
                trend_color = "#00a86b"
                trend_text = VIDEO_LABELS["range_bearish"]
            else:
                trend_color = "#00a86b"
                trend_text = VIDEO_LABELS["range_consolidation"]

            # The alternate path is lighter and appears just after the primary.
            if len(visible_alternate) >= 2:
                draw.line(
                    visible_alternate,
                    fill="#e53935",
                    width=3,
                )
                draw.polygon(
                    _arrow_head(
                        visible_alternate[-1],
                        visible_alternate[-2],
                        size=14,
                    ),
                    fill="#e53935",
                )

            draw.line(visible_trend, fill=trend_color, width=5)
            draw.polygon(
                _arrow_head(
                    visible_trend[-1],
                    visible_trend[-2],
                    size=19,
                ),
                fill=trend_color,
            )

    # Support/resistance are the only non-axis labels retained on the chart.
    # Current-price, FVG and other auxiliary labels are intentionally removed.
    right_labels = [
        (py(float(level)), color, name, _price(level))
        for level, color, name in levels_to_show
    ]
    level_label_face = _level_label_face(
        draw,
        level_lane_right - level_lane_left,
        chart_bottom - chart_top,
    )
    label_positions = _spread_label_positions(
        [desired_y for desired_y, _, _, _ in right_labels],
        chart_top + 28,
        chart_bottom - 78,
        minimum_gap=max(90, level_label_face.size * 2 + 22),
    )
    for (desired_y, color, text, price), y in zip(
        right_labels,
        label_positions,
    ):
        _centered_level_label(
            draw,
            level_lane_left,
            level_lane_right,
            y,
            text,
            str(price or ""),
            level_label_face,
            color,
        )

    # Forecast arrows are the top chart layer: repaint their geometry after
    # candles, zones, levels, price lines, and right-lane connectors.
    if prediction_phase and cumulative_segment_paths:
        forecast_left = history_end_x + 4
        forecast_right = chart_right - 18
        for segment_id, segment_path, path_progress in cumulative_segment_paths:
            top_points = _simplify_visual_polyline([
                (
                    forecast_left + ratio * (forecast_right - forecast_left),
                    py(value),
                )
                for ratio, value, _ in _structure_path_values(
                    {"path_points": segment_path}
                )
            ])
            top_points = _partial_polyline(top_points, path_progress)
            if len(top_points) < 2:
                continue
            color, line_width, head_size = _prediction_arrow_style(
                segment_id,
                active_segment_id,
                current_time,
            )
            _draw_clean_arrow(
                draw,
                top_points,
                color,
                line_width,
                head_size,
            )

    # The main output is an audit version: Chinese is above the chart and the
    # English word is below it. render_job() also creates an English-only copy.
    if (payload.get("style") or {}).get("show_subtitles", True):
        subtitle = _subtitle_at(narration, current_time, progress)
        subtitle_zh = _chinese_subtitle_at(narration, current_time)
        subtitle_left = 48
        subtitle_right = width - 48
        subtitle_lines = _wrap_text(
            draw,
            subtitle,
            subtitle_face,
            subtitle_right - subtitle_left - 54,
            max_lines=2,
        )
        english_y = layout["english_y"]
        for line_no, line in enumerate(subtitle_lines):
            bbox = draw.textbbox((0, 0), line, font=subtitle_face)
            text_width = bbox[2] - bbox[0]
            draw.text(
                ((subtitle_left + subtitle_right - text_width) / 2, english_y + line_no * 124),
                line,
                font=subtitle_face,
                fill="#131722",
            )
        if subtitle_zh and (payload.get("style") or {}).get(
            "show_review_chinese_subtitles", True,
        ):
            chinese_lines = _wrap_text(
                draw,
                subtitle_zh,
                subtitle_zh_face,
                subtitle_right - subtitle_left - 54,
                max_lines=2,
            )
            chinese_y = layout["review_chinese_y"]
            for line_no, line in enumerate(chinese_lines):
                bbox = draw.textbbox((0, 0), line, font=subtitle_zh_face)
                text_width = bbox[2] - bbox[0]
                draw.text(
                    ((subtitle_left + subtitle_right - text_width) / 2, chinese_y + line_no * 62),
                    line,
                    font=subtitle_zh_face,
                    fill="#4b5563",
                )

    image.save(path, "PNG")
