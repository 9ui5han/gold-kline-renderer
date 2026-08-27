from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .chart_renderer import CYAN, INK, RED, _font

MUTED, PANEL = "#66717D", "#F3F7FA"
CHINESE_DISCLAIMER = "教学示意图｜不代表实时行情"
CHART_REQUIRED_TYPES = {"indicator_panel", "zone_diagram", "candlestick_demo", "market_chart"}


def _text_contract_valid(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and all(character in "\n\r\t" or ord(character) >= 32 for character in text)


def _contains_chinese(value: Any) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in str(value or ""))


def _chinese_copy_valid(title: str, body: str) -> bool:
    return (
        _text_contract_valid(title)
        and _text_contract_valid(body)
        and _contains_chinese(f"{title}{body}")
    )


def _intersects(first: tuple[int, int, int, int] | None,
                second: tuple[int, int, int, int] | None) -> bool:
    if first is None or second is None:
        return False
    return not (
        first[2] <= second[0] or second[2] <= first[0] or
        first[3] <= second[1] or second[3] <= first[1]
    )


def _wrapped_lines(text: str, max_width: int, font=None) -> list[str]:
    active_font = font or _font(35)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    result: list[str] = []
    for paragraph in str(text or "").splitlines() or [""]:
        if not paragraph:
            result.append("")
            continue
        current = ""
        for character in paragraph:
            candidate = current + character
            width = measure.textbbox((0, 0), candidate, font=active_font)[2]
            if current and width > max_width:
                result.append(current.rstrip())
                current = character.lstrip()
            else:
                current = candidate
        result.append(current.rstrip())
    return result


def _paste_character(image: Image.Image, visual_assets: list[dict[str, Any]],
                     box: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    item = next((asset for asset in visual_assets if asset.get("asset_key") == "teacher_front"), None)
    path = Path(str((item or {}).get("asset_path") or ""))
    if not path.is_file() or path.suffix.lower() != ".png":
        return None
    character = Image.open(path).convert("RGBA")
    alpha_box = character.getchannel("A").getbbox()
    if alpha_box:
        character = character.crop(alpha_box)
    left, top, right, bottom = box
    character.thumbnail((right - left, bottom - top), Image.Resampling.LANCZOS)
    x = left + (right - left - character.width) // 2
    y = top + (bottom - top - character.height) // 2
    image.paste(character, (x, y), character)
    return (x, y, x + character.width, y + character.height)


def _paste_cover_illustration(
    image: Image.Image,
    visual_assets: list[dict[str, Any]],
    box: tuple[int, int, int, int],
) -> tuple[tuple[int, int, int, int] | None, str, float]:
    item = next((
        asset for asset in visual_assets
        if asset.get("source") == "undraw" and asset.get("asset_type") == "background"
    ), None)
    path = Path(str((item or {}).get("asset_path") or ""))
    if not path.is_file() or path.suffix.lower() != ".svg":
        return None, "", 0.0
    try:
        import cairosvg

        png = cairosvg.svg2png(url=str(path), output_width=520)
        illustration = Image.open(BytesIO(png)).convert("RGBA")
    except (ImportError, OSError, ValueError):
        return None, "", 0.0
    alpha_box = illustration.getchannel("A").getbbox()
    if alpha_box:
        illustration = illustration.crop(alpha_box)
    left, top, right, bottom = box
    illustration.thumbnail((right - left, bottom - top), Image.Resampling.LANCZOS)
    x = left + (right - left - illustration.width) // 2
    y = top + (bottom - top - illustration.height) // 2
    image.paste(illustration, (x, y), illustration)
    return (
        (x, y, x + illustration.width, y + illustration.height),
        str(item.get("asset_key") or ""),
        1.0,
    )


def _draw_header(draw: ImageDraw.ImageDraw, title: str, body: str, width: int,
                 compact: bool) -> tuple[int, bool, tuple[int, int, int, int], dict[str, Any]]:
    margin = 72
    title_size, body_size = (41, 26) if compact else (44, 27)
    title_font = _font(title_size)
    body_font = _font(body_size)
    title_width = width - margin * 2
    body_width = min(820, title_width)
    title_lines = _wrapped_lines(title, title_width, title_font)
    body_lines = _wrapped_lines(body, body_width, body_font)
    overflow = len(title_lines) > 2 or len(body_lines) > 3
    y = 62
    for line in title_lines[:2]:
        draw.text((margin, y), line, fill=INK, font=title_font)
        y += title_size + 12
    first_title_width = draw.textbbox((0, 0), title_lines[0] if title_lines else title, font=title_font)[2]
    underline_width = min(150, max(88, first_title_width // 3))
    draw.rectangle((margin, y, margin + underline_width, y + 7), fill=CYAN)
    y += 38
    for line in body_lines[:3]:
        draw.text((margin, y), line, fill=INK, font=body_font)
        y += body_size + 14
    return y, overflow, (margin, 62, width - margin, y), {
        "title_size": title_size,
        "body_size": body_size,
        "title_weight": "regular",
        "body_weight": "regular",
        "body_line_width": body_width,
    }


def _draw_market_background(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    points = [(0, 690), (110, 740), (220, 620), (330, 690), (430, 520), (560, 610), (700, 500), (830, 560), (1080, 430)]
    draw.line(points, fill="#D7E1E8", width=5)
    for x, y in points[1:-1]:
        draw.line((x, y - 52, x, y + 52), fill="#BBC9D3", width=3)
        draw.rectangle((x - 10, y - 25, x + 10, y + 25), fill=CYAN, outline=INK)


def _topic_key(page: dict[str, Any]) -> str:
    haystack = " ".join(str(page.get(key) or "") for key in (
        "title", "body", "key_message", "visual_focus",
    )).upper()
    for key in ("RSI", "MACD", "KDJ", "ATR", "OBV", "ICT"):
        if key in haystack:
            return key.lower()
    if "布林" in haystack or "BOLL" in haystack:
        return "bollinger"
    if "均线" in haystack or "MA" in haystack:
        return "moving_average"
    return "generic_finance"


def _cover_focus_label(page: dict[str, Any]) -> str:
    topic = _topic_key(page)
    return {
        "rsi": "RSI", "macd": "MACD", "kdj": "KDJ", "atr": "ATR",
        "obv": "OBV", "ict": "ICT", "bollinger": "BOLL",
        "moving_average": "MA",
    }.get(topic, "市场分析")


def _draw_cover_header(draw: ImageDraw.ImageDraw, page: dict[str, Any], width: int) -> tuple[int, bool, tuple[int, int, int, int], dict[str, Any]]:
    title = str(page.get("title") or "").strip()
    body = str(page.get("body") or "").strip()
    focus = _cover_focus_label(page)
    focus_font = _font(96, True)
    title_font = _font(31)
    body_font = _font(25)
    title_lines = _wrapped_lines(title, 760, title_font)
    body_lines = _wrapped_lines(body, 720, body_font)
    overflow = len(title_lines) > 1 or len(body_lines) > 1
    focus_width = draw.textbbox((0, 0), focus, font=focus_font)[2]
    draw.text(((width - focus_width) // 2, 35), focus, fill=INK, font=focus_font)
    y = 145
    secondary = title_lines[0] if title_lines else title
    if secondary.upper().replace(" ", "") != focus.upper().replace(" ", ""):
        secondary_width = draw.textbbox((0, 0), secondary, font=title_font)[2]
        draw.text(((width - secondary_width) // 2, y), secondary, fill="#2F6B9A", font=title_font)
        y += 44
    if body_lines:
        subtitle = body_lines[0]
        subtitle_width = draw.textbbox((0, 0), subtitle, font=body_font)[2]
        draw.text(((width - subtitle_width) // 2, y), subtitle, fill=MUTED, font=body_font)
        y += 38
    return y, overflow, (80, 35, width - 80, y), {
        "focus_size": 96, "title_size": 31, "body_size": 25,
        "title_weight": "regular", "body_weight": "regular",
        "body_line_width": 720,
    }


def _draw_magnifier(draw: ImageDraw.ImageDraw, center: tuple[int, int], radius: int) -> None:
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=INK, width=9)
    draw.line((x + radius - 5, y + radius - 5, x + radius + 62, y + radius + 62), fill=INK, width=13)


def _smooth_curve(points: list[tuple[float, float]], samples_per_segment: int = 8) -> list[tuple[float, float]]:
    """Return a Catmull-Rom interpolation that passes through the source points."""
    if len(points) < 3:
        return points
    padded = [points[0], *points, points[-1]]
    result: list[tuple[float, float]] = []
    for index in range(1, len(padded) - 2):
        p0, p1, p2, p3 = padded[index - 1:index + 3]
        for sample in range(samples_per_segment):
            t = sample / samples_per_segment
            t2, t3 = t * t, t * t * t
            x = .5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t +
                      (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                      (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = .5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t +
                      (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                      (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            result.append((x, y))
    result.append(points[-1])
    return result


def _draw_antialiased_line(
    image: Image.Image,
    points: list[tuple[float, float]],
    fill: str,
    width: int,
    scale: int = 4,
) -> None:
    """Draw a high-resolution line and shrink it for smooth mobile output."""
    if len(points) < 2:
        return
    min_x = max(0, int(min(point[0] for point in points)) - width * 2)
    min_y = max(0, int(min(point[1] for point in points)) - width * 2)
    max_x = min(image.width, int(max(point[0] for point in points)) + width * 2 + 1)
    max_y = min(image.height, int(max(point[1] for point in points)) + width * 2 + 1)
    hi_size = ((max_x - min_x) * scale, (max_y - min_y) * scale)
    layer = Image.new("RGBA", hi_size, (255, 255, 255, 0))
    layer_draw = ImageDraw.Draw(layer)
    hi_points = [((x - min_x) * scale, (y - min_y) * scale) for x, y in points]
    layer_draw.line(hi_points, fill=fill, width=width * scale, joint="curve")
    layer = layer.filter(ImageFilter.GaussianBlur(radius=1.0))
    layer = layer.resize((max_x - min_x, max_y - min_y), Image.Resampling.LANCZOS)
    image.paste(layer, (min_x, min_y), layer)


def _draw_cover_topic_visual(
    draw: ImageDraw.ImageDraw,
    page: dict[str, Any],
    width: int,
    height: int,
) -> tuple[str, int, int, float, int, float]:
    topic = _topic_key(page)
    left, right = 70, width - 70
    chart_top, chart_bottom = 350, 735
    close_levels = [
        .82, .78, .74, .77, .71, .68, .70, .64, .59, .62,
        .56, .52, .49, .53, .47, .43, .46, .41, .38, .42,
        .45, .40, .44, .48, .43, .39, .35, .38, .33, .29,
        .32, .27, .24, .28, .23, .20, .24, .19, .16, .20,
        .17, .13, .16,
    ]
    step = (right - left) / len(close_levels)
    body_half = step * .45
    candle_gap_ratio = 1 - body_half * 2 / step
    for index, close_ratio in enumerate(close_levels):
        open_ratio = close_levels[index - 1] if index else .82
        up = close_ratio <= open_ratio
        x = left + (index + .5) * step
        open_y = int(chart_top + open_ratio * (chart_bottom - chart_top))
        close_y = int(chart_top + close_ratio * (chart_bottom - chart_top))
        high = min(open_y, close_y) - 25
        low = max(open_y, close_y) + 25
        draw.line((x, high, x, low), fill=INK, width=2)
        color = CYAN if up else "#6C7782"
        draw.rectangle(
            (x - body_half, min(open_y, close_y), x + body_half, max(min(open_y, close_y) + 4, max(open_y, close_y))),
            fill=color, outline=INK, width=1,
        )

    if topic == "rsi":
        panel_top, panel_bottom = 760, 940
        overlay = Image.new("RGBA", (width, height), (255, 255, 255, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle((left, panel_top, right, panel_top + 48), fill=(233, 154, 165, 72))
        overlay_draw.rectangle((left, panel_bottom - 48, right, panel_bottom), fill=(50, 196, 234, 65))
        draw._image.paste(overlay, (0, 0), overlay)
        values = [48, 58, 72, 77, 66, 54, 38, 25, 31, 46, 62, 74, 69]
        points = []
        for index, value in enumerate(values):
            x = left + index * (right - left) / (len(values) - 1)
            y = panel_bottom - value / 100 * (panel_bottom - panel_top)
            points.append((x, y))
        smooth_points = _smooth_curve(points, samples_per_segment=40)
        _draw_antialiased_line(draw._image, smooth_points, fill="#1597C3", width=6, scale=8)
        draw.line((left, panel_top + .3 * (panel_bottom - panel_top), right, panel_top + .3 * (panel_bottom - panel_top)), fill=RED, width=2)
        draw.line((left, panel_top + .7 * (panel_bottom - panel_top), right, panel_top + .7 * (panel_bottom - panel_top)), fill=CYAN, width=2)
        draw.text((left, panel_top - 35), "RSI（14）", fill=INK, font=_font(24, True))
        return (
            "indicator_rsi", len(close_levels), len(smooth_points),
            candle_gap_ratio, 8, body_half * 2,
        )

    label = {
        "macd": "MACD", "kdj": "KDJ", "atr": "ATR", "obv": "OBV",
        "ict": "ICT", "bollinger": "BOLL", "moving_average": "MA",
    }.get(topic, "MARKET")
    draw.rounded_rectangle((350, 780, 730, 875), radius=26, fill=PANEL, outline="#D9E2E8", width=2)
    label_width = draw.textbbox((0, 0), label, font=_font(40, True))[2]
    draw.text(((width - label_width) // 2, 805), label, fill=INK, font=_font(40, True))
    visual_type = f"indicator_{topic}" if topic != "generic_finance" else "topic_magnifier"
    return visual_type, len(close_levels), 0, candle_gap_ratio, 0, body_half * 2


def _draw_checklist_page(draw: ImageDraw.ImageDraw, page: dict[str, Any], width: int, top: int) -> int:
    items = [str(item).strip() for item in page.get("required_elements") or [] if str(item).strip()]
    if not items:
        return 0
    y = max(top, 360)
    item_font = _font(28)
    for index, item in enumerate(items[:4], start=1):
        draw.rounded_rectangle((105, y, width - 105, y + 105), radius=20, fill=PANEL, outline="#DCE5EB", width=2)
        draw.ellipse((135, y + 24, 191, y + 80), fill=CYAN)
        number = str(index)
        number_width = draw.textbbox((0, 0), number, font=_font(21, True))[2]
        draw.text((163 - number_width // 2, y + 38), number, fill="white", font=_font(21, True))
        lines = _wrapped_lines(item, width - 360, item_font)
        line_y = y + (31 if len(lines) == 1 else 15)
        for line in lines[:2]:
            draw.text((225, line_y), line, fill=INK, font=item_font)
            line_y += 38
        y += 125
    return min(4, len(items))


def _paste_chart(image: Image.Image, chart: dict[str, Any] | None, box: tuple[int, int, int, int]) -> bool:
    if not chart:
        return False
    path = Path(str(chart.get("asset_path") or ""))
    if not path.is_file():
        return False
    chart_image = Image.open(path).convert("RGB")
    left, top, right, bottom = box
    chart_image.thumbnail((right - left, bottom - top))
    image.paste(chart_image, (left + (right - left - chart_image.width) // 2, top + (bottom - top - chart_image.height) // 2))
    return True


def _draw_summary(draw: ImageDraw.ImageDraw, page: dict[str, Any], width: int) -> None:
    defaults = ["识别指标状态", "结合价格确认", "不要依赖单一指标"]
    source_items = [str(item).strip() for item in page.get("required_elements") or [] if str(item).strip()]
    items = (source_items + defaults)[:3]
    y = 390
    for index, item in enumerate(items[:3], start=1):
        draw.rounded_rectangle((100, y, width - 100, y + 120), radius=22, fill=PANEL, outline="#DCE5EB", width=2)
        draw.ellipse((135, y + 27, 201, y + 93), fill=CYAN)
        draw.text((158, y + 43), str(index), fill="white", font=_font(24, True))
        draw.text((235, y + 38), item[:42], fill=INK, font=_font(29, True))
        y += 145


def render_page(page: dict[str, Any], chart: dict[str, Any] | None,
                visual_assets: list[dict[str, Any]], output_path: Path,
                width: int, height: int, compact: bool = False) -> dict[str, Any]:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.line((38, 72, 38, height - 72), fill="#E5F4F8", width=5)
    role = str(page.get("page_role") or "")
    visual_type = str(page.get("visual_type") or "")
    layout = "cover" if role == "cover" or visual_type == "cover_illustration" else "summary" if role == "summary" or visual_type == "summary_card" else "checklist" if role in {"checklist", "mistakes"} or visual_type == "checklist" else "example" if role == "example" or visual_type == "candlestick_demo" else "standard"
    source_title = str(page.get("title") or "").strip()
    source_body = str(page.get("body") or "").strip()
    source_copy_valid = _chinese_copy_valid(source_title, source_body)
    if not source_copy_valid:
        raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_CHINESE_COPY_REQUIRED")
    title = source_title
    body = source_body
    chart_present, character_present = False, False
    checklist_item_count = 0
    cover_visual_type = ""
    topic_visual_present = False
    cover_asset_box = None
    cover_asset_key = ""
    cover_asset_opacity = 0.0
    cover_candle_count = 0
    cover_indicator_point_count = 0
    cover_candle_gap_ratio = 1.0
    cover_indicator_supersample = 0
    cover_candle_body_width = 0.0
    typography_metrics: dict[str, Any] = {}
    character_box = None
    content_box = None
    disclaimer_count = 0

    if layout == "cover":
        _, overflow, content_box, typography_metrics = _draw_cover_header(draw, page, width)
        (
            cover_visual_type,
            cover_candle_count,
            cover_indicator_point_count,
            cover_candle_gap_ratio,
            cover_indicator_supersample,
            cover_candle_body_width,
        ) = _draw_cover_topic_visual(draw, page, width, height)
        topic_visual_present = True
        cover_asset_box, cover_asset_key, cover_asset_opacity = _paste_cover_illustration(
            image, visual_assets, (600, 285, width - 55, 700)
        )
        character_box = _paste_character(image, visual_assets, (320, 350, width - 320, height - 90))
        character_present = character_box is not None
        draw.text((width - 205, height - 65), "滑动查看  →", fill=INK, font=_font(20, True))
    else:
        header_bottom, overflow, content_box, typography_metrics = _draw_header(draw, title, body, width, compact)
        if layout == "summary":
            _draw_summary(draw, page, width)
        elif layout == "checklist":
            checklist_item_count = _draw_checklist_page(draw, page, width, header_bottom + 28)
            if checklist_item_count == 0:
                raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_CHECKLIST_REQUIRED")
        else:
            top = max(390, header_bottom + 25)
            chart_present = _paste_chart(image, chart, (72, top, width - 72, height - 105))
            if visual_type in CHART_REQUIRED_TYPES and not chart_present:
                raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_CHART_REQUIRED")
        # Characters are cover-only. Content pages reserve the canvas for teaching evidence.

    draw.text((72, height - 55), CHINESE_DISCLAIMER, fill=MUTED, font=_font(22))
    disclaimer_count += 1
    footer_box = (0, height - 75, width, height)
    character_content_overlap = _intersects(character_box, content_box)
    character_footer_overlap = _intersects(character_box, footer_box)
    character_in_bounds = character_box is None or (
        character_box[0] >= 0 and character_box[1] >= 0 and
        character_box[2] <= width and character_box[3] <= height
    )
    character_safe = character_in_bounds and not character_content_overlap and not character_footer_overlap
    overlap = character_content_overlap or character_footer_overlap
    chinese_contract_valid = _chinese_copy_valid(title, body) and _contains_chinese(CHINESE_DISCLAIMER)
    teaching_evidence = {
        "engine_version": str((chart or {}).get("teaching_engine_version") or ""),
        "indicator_id": str((chart or {}).get("indicator_id") or ""),
        "scenario_id": str((chart or {}).get("scenario_id") or ""),
        "ohlc_count": int((chart or {}).get("ohlc_count") or 0),
        "signal_anchors": list((chart or {}).get("signal_anchors") or []),
        "visual_layers": list((chart or {}).get("visual_layers") or []),
        "signal_contract_valid": (chart or {}).get("signal_contract_valid") is True,
        "data_fingerprint": str((chart or {}).get("data_fingerprint") or ""),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return {
        "page_no": int(page["page_no"]), "path": str(output_path), "width": width, "height": height,
        "layout_overflow": overflow, "risk_note_present": True, "chart_present": chart_present,
        "character_present": character_present, "layout_template": layout, "render_language": "zh-CN",
        "rendered_disclaimer": CHINESE_DISCLAIMER, "disclaimer_count": 1,
        "rendered_title": title, "rendered_body": body,
        "typography_metrics": typography_metrics,
        "visible_page_number": False,
        "cover_visual_type": cover_visual_type,
        "cover_focus_label": _cover_focus_label(page) if layout == "cover" else "",
        "topic_visual_present": topic_visual_present,
        "cover_asset_present": cover_asset_box is not None,
        "cover_asset_key": cover_asset_key,
        "cover_asset_box": cover_asset_box,
        "cover_asset_opacity": cover_asset_opacity,
        "cover_candle_count": cover_candle_count,
        "cover_indicator_point_count": cover_indicator_point_count,
        "cover_candle_gap_ratio": cover_candle_gap_ratio,
        "cover_indicator_supersample": cover_indicator_supersample,
        "cover_candle_body_width": cover_candle_body_width,
        "checklist_present": checklist_item_count > 0,
        "checklist_item_count": checklist_item_count,
        "chinese_contract_valid": chinese_contract_valid,
        "character_in_safe_area": character_safe, "layout_overlap": overlap,
        "character_box": character_box, "content_box": content_box,
        "character_box_intersects_content": character_content_overlap,
        "disclaimer_count": disclaimer_count,
        "teaching_evidence": teaching_evidence,
    }
