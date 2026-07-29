"""TradingView-inspired chart renderer for the long-form K-line video."""

from __future__ import annotations

import math
from datetime import datetime
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
FONT_SMOKE_TEXT = "关注关键支撑区，观察压力变化，黄金K线预测"
_FONT_VERIFIED = False


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


def _price(value: float) -> str:
    return f"{float(value):,.2f}"


def _time_label(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.strftime("%m-%d %H:%M")
    except Exception:
        return str(value or "")[:10]


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    face: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int = 2,
) -> list[str]:
    lines: list[str] = []
    current = ""

    for char in str(text or ""):
        trial = current + char
        bbox = draw.textbbox((0, 0), trial, font=face)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break
        else:
            current = trial

    if current and len(lines) < max_lines:
        lines.append(current)

    return lines


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


def _pivot_points(
    candles: list[dict[str, Any]],
    key: str,
    window: int = 2,
) -> list[tuple[int, float]]:
    result: list[tuple[int, float]] = []
    start = max(window, len(candles) - 80)

    for index in range(start, len(candles) - window):
        value = float(candles[index][key])
        segment = candles[index - window : index + window + 1]
        values = [float(item[key]) for item in segment]

        if key == "high" and value == max(values):
            result.append((index, value))
        elif key == "low" and value == min(values):
            result.append((index, value))

    return result


def _trendline(
    candles: list[dict[str, Any]],
) -> tuple[str, tuple[int, float], tuple[int, float]] | None:
    highs = _pivot_points(candles, "high")
    lows = _pivot_points(candles, "low")

    if len(highs) >= 2:
        first, second = highs[-2], highs[-1]
        if second[1] < first[1]:
            return "下降压力线", first, second

    if len(lows) >= 2:
        first, second = lows[-2], lows[-1]
        if second[1] > first[1]:
            return "上升支撑线", first, second

    return None


def _subtitle_at(
    narration: dict[str, Any],
    current_time: float,
    progress: float,
) -> str:
    for cue in narration.get("subtitle_cues") or []:
        start = float(cue.get("start_sec") or 0)
        end = float(cue.get("end_sec") or 0)
        if start <= current_time < end:
            return str(cue.get("text") or "")

    segments = sorted(
        narration.get("segments") or [],
        key=lambda item: item.get("order", 0),
    )
    if segments:
        index = min(int(progress * len(segments)), len(segments) - 1)
        return str(segments[index].get("text") or "")

    return str(narration.get("full_text") or "")


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
    history = payload["historical_candles"]
    selected = _scenario(analysis, payload["style"]["scenario"])
    forecast_all = selected.get("candles") or []

    # Observation/support rectangles belong to the forecast discussion.
    prediction_phase = progress >= 0.62
    if prediction_phase:
        reveal_progress = (progress - 0.62) / 0.25
        forecast_count = min(
            len(forecast_all),
            max(0, math.ceil(reveal_progress * 6)),
        )
    else:
        forecast_count = 0

    visible_forecast = forecast_all[:forecast_count]
    visible_history = history[-min(len(history), 90) :]
    candles = visible_history + visible_forecast

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)

    title_face = _font(36, True)
    meta_face = _font(24)
    axis_face = _font(20)
    label_face = _font(21, True)
    subtitle_face = _font(34, True)
    risk_face = _font(19)

    # Compact TradingView-like header.
    draw.text(
        (52, 36),
        f"{payload['symbol']} · {payload['timeframe']}",
        font=title_face,
        fill="#131722",
    )
    draw.text(
        (52, 88),
        f"数据截止 {_time_label(payload['data_as_of'])}",
        font=meta_face,
        fill="#787b86",
    )

    chart_left = 48
    chart_top = 145
    chart_right = width - 84
    chart_bottom = height - 330
    price_axis_x = chart_right
    time_axis_y = chart_bottom
    draw.rectangle(
        (chart_left, chart_top, chart_right, chart_bottom),
        fill="#ffffff",
        outline="#e0e3eb",
        width=1,
    )

    prices = [
        float(candle[key])
        for candle in candles
        for key in ("high", "low")
    ]
    if prediction_phase:
        for zone_key in ("potential_buy_zones", "potential_sell_zones"):
            for zone in analysis.get(zone_key) or []:
                prices.extend((float(zone["low"]), float(zone["high"])))
    if not prices:
        prices = [0.0, 1.0]

    pmin, pmax = min(prices), max(prices)
    price_span = max(pmax - pmin, 0.01)
    pmin -= price_span * 0.06
    pmax += price_span * 0.06

    def py(value: float) -> float:
        return chart_bottom - 34 - (
            (float(value) - pmin)
            / max(pmax - pmin, 0.01)
            * (chart_bottom - chart_top - 68)
        )

    # TradingView-like grid and right price scale.
    for grid_index in range(7):
        fraction = grid_index / 6
        y = chart_top + 28 + fraction * (chart_bottom - chart_top - 62)
        value = pmax - fraction * (pmax - pmin)
        draw.line(
            (chart_left, y, chart_right, y),
            fill="#eef0f3",
            width=1,
        )
        draw.text(
            (price_axis_x + 8, y - 11),
            _price(value),
            font=axis_face,
            fill="#787b86",
        )

    count = max(len(candles), 1)
    slot = (chart_right - chart_left - 24) / count
    body_width = max(4, min(14, int(slot * 0.62)))

    def px(index: int) -> float:
        return chart_left + 12 + slot * (index + 0.5)

    # Vertical time grid.
    time_marks = 6
    for mark_index in range(time_marks):
        candle_index = round(
            mark_index * max(len(candles) - 1, 0) / max(time_marks - 1, 1)
        )
        x = px(candle_index)
        draw.line(
            (x, chart_top, x, chart_bottom),
            fill="#f2f3f5",
            width=1,
        )
        if candle_index < len(candles):
            draw.text(
                (x - 38, time_axis_y + 10),
                _time_label(candles[candle_index].get("time", "")),
                font=axis_face,
                fill="#787b86",
            )

    # Show one reliable trendline during analysis; it is not a large zone.
    trend = _trendline(visible_history)
    if progress >= 0.18 and trend:
        trend_label, first, second = trend
        start_index = first[0]
        end_index = second[0]
        x1, y1 = px(start_index), py(first[1])
        x2 = px(min(len(visible_history) + 4, count - 1))
        slope = (second[1] - first[1]) / max(second[0] - first[0], 1)
        projected_price = first[1] + slope * (
            min(len(visible_history) + 4, count - 1) - start_index
        )
        y2 = py(projected_price)
        draw.line((x1, y1, x2, y2), fill="#296b5f", width=3)
        _round_rect_label(
            draw,
            min(x2 - 150, chart_right - 170),
            y2 - 22,
            trend_label,
            label_face,
            "#296b5f",
        )

    # Key level lines appear only after the historical overview.
    if payload["style"].get("show_support_resistance", True) and progress >= 0.34:
        level_start_x = chart_left + (chart_right - chart_left) * 0.50
        levels_to_show = []
        supports = analysis.get("support_levels") or []
        resistances = analysis.get("resistance_levels") or []
        if supports:
            levels_to_show.append((supports[0], "#7e57c2", "支撑"))
        if resistances:
            levels_to_show.append((resistances[0], "#9c4dcc", "压力"))

        for level, color, name in levels_to_show:
            y = py(float(level))
            _dashed_line(
                draw,
                (level_start_x, y, chart_right, y),
                fill=color,
                width=2,
                dash=10,
            )
            _round_rect_label(
                draw,
                chart_right,
                y,
                _price(level),
                axis_face,
                color,
                anchor="rm",
            )
            _round_rect_label(
                draw,
                level_start_x + 12,
                y - 22,
                name,
                label_face,
                color,
            )

    # Local rectangles are intentionally restricted to the prediction section.
    if (
        prediction_phase
        and payload["style"].get("show_observation_zones", True)
    ):
        zone_start_index = max(len(visible_history) - 12, 0)
        zone_x1 = px(zone_start_index)
        zone_x2 = chart_right - 6
        zone_specs = (
            (
                "potential_buy_zones",
                "#ede1f7",
                "#8653a6",
                "主要支撑区",
            ),
            (
                "potential_sell_zones",
                "#f1e3f6",
                "#9c4dcc",
                "强压力区",
            ),
        )

        for key, fill, outline, label in zone_specs:
            zones = analysis.get(key) or []
            if not zones:
                continue
            zone = zones[0]
            y1 = py(float(zone["high"]))
            y2 = py(float(zone["low"]))
            draw.rectangle(
                (zone_x1, y1, zone_x2, y2),
                fill=fill,
                outline=outline,
                width=2,
            )
            _round_rect_label(
                draw,
                zone_x1 + 12,
                (y1 + y2) / 2,
                label,
                label_face,
                outline,
            )

    # Divider between real and forecast bars.
    if prediction_phase and visible_forecast:
        divider_x = px(len(visible_history)) - slot / 2
        _dashed_line(
            draw,
            (divider_x, chart_top, divider_x, chart_bottom),
            fill="#2962ff",
            width=2,
            dash=8,
        )
        _round_rect_label(
            draw,
            divider_x + 10,
            chart_top + 24,
            "预测开始",
            label_face,
            "#2962ff",
        )

    # Candles are drawn after zones so they remain readable.
    for index, candle in enumerate(candles):
        x = px(index)
        is_forecast = index >= len(visible_history)
        rising = float(candle["close"]) >= float(candle["open"])
        color = "#089981" if rising else "#f23645"
        if is_forecast:
            color = "#2962ff"

        draw.line(
            (x, py(candle["high"]), x, py(candle["low"])),
            fill=color,
            width=2,
        )
        open_y = py(candle["open"])
        close_y = py(candle["close"])
        y1, y2 = min(open_y, close_y), max(open_y, close_y)
        y2 = max(y2, y1 + 3)

        if is_forecast:
            draw.rectangle(
                (x - body_width / 2, y1, x + body_width / 2, y2),
                outline=color,
                width=2,
            )
        else:
            draw.rectangle(
                (x - body_width / 2, y1, x + body_width / 2, y2),
                fill=color,
            )

    # Current real price line and label.
    last_close = float(visible_history[-1]["close"])
    last_y = py(last_close)
    _dashed_line(
        draw,
        (chart_left, last_y, chart_right, last_y),
        fill="#787b86",
        width=1,
        dash=7,
    )
    _round_rect_label(
        draw,
        width - 8,
        last_y,
        _price(last_close),
        axis_face,
        "#787b86",
        anchor="rm",
    )

    # Compact subtitle area; narration and timing are preserved unchanged.
    subtitle = _subtitle_at(payload["narration"], current_time, progress)
    subtitle_lines = _wrap_text(
        draw,
        subtitle,
        subtitle_face,
        width - 150,
        max_lines=2,
    )
    subtitle_top = height - 245
    draw.rounded_rectangle(
        (48, subtitle_top, width - 48, height - 92),
        radius=20,
        fill="#f4f4f6",
        outline="#d9dce3",
        width=1,
    )
    for line_no, line in enumerate(subtitle_lines):
        bbox = draw.textbbox((0, 0), line, font=subtitle_face)
        text_width = bbox[2] - bbox[0]
        draw.text(
            ((width - text_width) / 2, subtitle_top + 25 + line_no * 52),
            line,
            font=subtitle_face,
            fill="#131722",
        )

    draw.text(
        (52, height - 61),
        "AI辅助分析 · 预测可能完全错误 · 仅供研究教育 · 不构成投资建议",
        font=risk_face,
        fill="#787b86",
    )
    image.save(path, "PNG")
