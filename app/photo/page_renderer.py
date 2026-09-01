import math
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .chart_renderer import LABEL_LEFT, CYAN, INK, RED, _english_font, _font

MUTED, PANEL = "#66717D", "#F3F7FA"
CHINESE_DISCLAIMER = "教学示意图｜不代表实时行情"
ENGLISH_DISCLAIMER = "Educational illustration | Not real-time market data"
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


def _english_copy_valid(title: str, body: str) -> bool:
    combined = f"{title} {body}"
    return (
        _text_contract_valid(title)
        and _text_contract_valid(body)
        and any(character.isalpha() and ord(character) < 128 for character in combined)
        and not _contains_chinese(combined)
    )


def _intersects(first: tuple[int, int, int, int] | None,
                second: tuple[int, int, int, int] | None) -> bool:
    if first is None or second is None:
        return False
    return not (
        first[2] <= second[0] or second[2] <= first[0] or
        first[3] <= second[1] or second[3] <= first[1]
    )


def _merge_bounds(*boxes: tuple[int, int, int, int] | None) -> tuple[int, int, int, int] | None:
    """Return the smallest pixel rectangle containing the supplied drawn bounds."""
    visible = [box for box in boxes if box is not None]
    if not visible:
        return None
    return (
        min(box[0] for box in visible), min(box[1] for box in visible),
        max(box[2] for box in visible), max(box[3] for box in visible),
    )


def _drawn_text_bounds(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font,
    shadow_padding: int = 0,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = draw.textbbox(position, text, font=font)
    return (
        int(left) - shadow_padding,
        int(top) - shadow_padding,
        int(right) + shadow_padding,
        int(bottom) + shadow_padding,
    )


def _layout_overlap(regions: dict[str, tuple[int, int, int, int]]) -> bool:
    """Check top-level content regions; title and body are children of header."""
    top_level = ("header", "checklist", "summary", "chart", "character", "footer")
    visible = [(name, regions[name]) for name in top_level if name in regions]
    return any(
        _intersects(first_box, second_box)
        for index, (_, first_box) in enumerate(visible)
        for _, second_box in visible[index + 1:]
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


def _wrapped_word_lines(text: str, max_width: int, font) -> list[str]:
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines: list[str] = []
    current = ""
    for word in str(text or "").split():
        candidate = f"{current} {word}".strip()
        if current and measure.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _draw_shadow_text(
    draw: ImageDraw.ImageDraw,
    position: tuple[float, float],
    text: str,
    font,
    fill: str,
    shadow: bool = True,
) -> None:
    if shadow:
        layer = Image.new("RGBA", draw._image.size, (255, 255, 255, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.text(
            (position[0] + 2, position[1] + 3), text,
            font=font, fill=(0, 0, 0, 41),
        )
        layer = layer.filter(ImageFilter.GaussianBlur(radius=3))
        draw._image.paste(layer, (0, 0), layer)
    draw.text(position, text, font=font, fill=fill)


def _draw_english_header(
    draw: ImageDraw.ImageDraw,
    page: dict[str, Any],
    width: int,
    cover: bool,
    primary_color: str = INK,
    accent_color: str = CYAN,
) -> tuple[int, bool, tuple[int, int, int, int], dict[str, Any]]:
    title = str(page.get("title") or "").strip().upper()
    body = str(page.get("body") or "").strip()
    focus = _cover_focus_label(page).upper()
    title_size = 58 if cover else 50
    title_font = _english_font(title_size, 800)
    title_lines = _wrapped_word_lines(title, 920, title_font)
    body_line_limit = 4
    body_sizes = (31, 29, 27, 26) if cover else (34, 32, 30, 28, 26)
    body_size = body_sizes[-1]
    body_font = _english_font(body_size, 400)
    body_lines = _wrapped_word_lines(body, 900, body_font)
    for candidate_size in body_sizes:
        candidate_font = _english_font(candidate_size, 400)
        candidate_lines = _wrapped_word_lines(body, 900, candidate_font)
        if len(candidate_lines) <= body_line_limit:
            body_size = candidate_size
            body_font = candidate_font
            body_lines = candidate_lines
            break
    overflow = (
        len(title_lines) > 2 or len(body_lines) > body_line_limit or
        any(draw.textlength(line, font=title_font) > 920 for line in title_lines) or
        any(draw.textlength(line, font=body_font) > 900 for line in body_lines)
    )
    highlight_words: list[str] = []
    title_bounds: list[tuple[int, int, int, int]] = []
    body_bounds: list[tuple[int, int, int, int]] = []
    y = 62 if cover else 72
    for line in title_lines[:2]:
        words = line.split()
        widths = [draw.textlength(word, font=title_font) for word in words]
        space = draw.textlength(" ", font=title_font)
        total = sum(widths) + space * max(0, len(words) - 1)
        x = (width - total) / 2
        for word, word_width in zip(words, widths):
            clean = "".join(character for character in word if character.isalnum()).upper()
            highlighted = clean == focus or (focus and focus in clean)
            color = accent_color if highlighted else primary_color
            if highlighted:
                highlight_words.append(word)
            _draw_shadow_text(draw, (x, y), word, title_font, color, shadow=True)
            title_bounds.append(_drawn_text_bounds(draw, (x, y), word, title_font, shadow_padding=8))
            x += word_width + space
        y += title_size + 16
    y += 20
    for line in body_lines[:body_line_limit]:
        line_width = draw.textlength(line, font=body_font)
        position = ((width - line_width) / 2, y)
        _draw_shadow_text(draw, position, line, body_font, primary_color, shadow=True)
        body_bounds.append(
            _drawn_text_bounds(draw, position, line, body_font, shadow_padding=8)
        )
        y += body_size + 15
    title_box = _merge_bounds(*title_bounds)
    body_box = _merge_bounds(*body_bounds)
    header_box = _merge_bounds(title_box, body_box)
    return y, overflow, header_box or (70, 62, width - 70, y), {
        "font_family": "Montserrat",
        "title_size": title_size,
        "body_size": body_size,
        "title_weight": 800,
        "body_weight": 400,
        "chart_label_weight": 600,
        "alignment": "center",
        "highlight_words": highlight_words,
        "title_shadow": {
            "offset_x": 2, "offset_y": 3, "blur": 3, "opacity": 0.16,
        },
        "body_shadow": {
            "offset_x": 2, "offset_y": 3, "blur": 3, "opacity": 0.16,
        },
        "body_line_width": 900,
        "primary_color": primary_color,
        "accent_color": accent_color,
        "title_line_count": len(title_lines),
        "body_line_count": len(body_lines),
        "layout_regions": {
            "header": header_box,
            "title": title_box,
            "body": body_box,
        },
    }


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


def _paste_template_background(
    image: Image.Image,
    visual_assets: list[dict[str, Any]],
) -> tuple[bool, str]:
    item = next((
        asset for asset in visual_assets
        if asset.get("asset_type") == "background"
        and asset.get("purpose") == "page_template"
    ), None)
    path = Path(str((item or {}).get("asset_path") or ""))
    if not path.is_file():
        return False, ""
    try:
        template = Image.open(path).convert("RGB")
    except (OSError, ValueError):
        return False, ""
    template = template.resize(image.size, Image.Resampling.LANCZOS)
    image.paste(template, (0, 0))
    return True, str(item.get("asset_key") or "")


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
    title_bounds: list[tuple[int, int, int, int]] = []
    body_bounds: list[tuple[int, int, int, int]] = []
    for line in title_lines[:2]:
        draw.text((margin, y), line, fill=INK, font=title_font)
        title_bounds.append(_drawn_text_bounds(draw, (margin, y), line, title_font))
        y += title_size + 12
    first_title_width = draw.textbbox((0, 0), title_lines[0] if title_lines else title, font=title_font)[2]
    underline_width = min(150, max(88, first_title_width // 3))
    underline_box = (margin, y, margin + underline_width, y + 7)
    draw.rectangle(underline_box, fill=CYAN)
    y += 38
    for line in body_lines[:3]:
        draw.text((margin, y), line, fill=INK, font=body_font)
        body_bounds.append(_drawn_text_bounds(draw, (margin, y), line, body_font))
        y += body_size + 14
    title_box = _merge_bounds(*title_bounds)
    body_box = _merge_bounds(*body_bounds)
    header_box = _merge_bounds(title_box, body_box, underline_box)
    return y, overflow, header_box or (margin, 62, width - margin, y), {
        "title_size": title_size,
        "body_size": body_size,
        "title_weight": "regular",
        "body_weight": "regular",
        "body_line_width": body_width,
        "layout_regions": {
            "header": header_box,
            "title": title_box,
            "body": body_box,
        },
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
    focus_position = ((width - focus_width) // 2, 35)
    draw.text(focus_position, focus, fill=INK, font=focus_font)
    title_bounds = [_drawn_text_bounds(draw, focus_position, focus, focus_font)]
    body_bounds: list[tuple[int, int, int, int]] = []
    y = 145
    secondary = title_lines[0] if title_lines else title
    if secondary.upper().replace(" ", "") != focus.upper().replace(" ", ""):
        secondary_width = draw.textbbox((0, 0), secondary, font=title_font)[2]
        secondary_position = ((width - secondary_width) // 2, y)
        draw.text(secondary_position, secondary, fill="#2F6B9A", font=title_font)
        title_bounds.append(_drawn_text_bounds(draw, secondary_position, secondary, title_font))
        y += 44
    if body_lines:
        subtitle = body_lines[0]
        subtitle_width = draw.textbbox((0, 0), subtitle, font=body_font)[2]
        subtitle_position = ((width - subtitle_width) // 2, y)
        draw.text(subtitle_position, subtitle, fill=MUTED, font=body_font)
        body_bounds.append(_drawn_text_bounds(draw, subtitle_position, subtitle, body_font))
        y += 38
    title_box = _merge_bounds(*title_bounds)
    body_box = _merge_bounds(*body_bounds)
    header_box = _merge_bounds(title_box, body_box)
    return y, overflow, header_box or (80, 35, width - 80, y), {
        "focus_size": 96, "title_size": 31, "body_size": 25,
        "title_weight": "regular", "body_weight": "regular",
        "body_line_width": 720,
        "layout_regions": {
            "header": header_box,
            "title": title_box,
            "body": body_box,
        },
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
    language: str = "zh-CN",
) -> tuple[str, int, int, float, int, float]:
    topic = _topic_key(page)
    left, right = 0, width
    chart_top, chart_bottom = 350, 735
    close_levels = [
        .70, .74, .68, .72, .63, .66, .59, .55, .62, .50,
        .54, .47, .60, .38, .34, .41, .44, .40, .47, .43,
        .50, .36, .30, .22, .46, .34, .40, .38, .45, .36,
        .42, .30, .34, .26, .32, .28, .38, .31, .35, .25,
        .30, .20, .24, .17, .29, .22, .27, .16, .21, .12,
        .18, .10, .14, .08,
        .13, .09, .15, .11, .18, .14, .20, .16, .22, .17,
        .24, .20, .26, .22,
    ]
    step = (right - left) / len(close_levels)
    body_half = 6.0
    candle_gap_ratio = 1 - body_half * 2 / step
    for index, close_ratio in enumerate(close_levels):
        previous_close = close_levels[index - 1] if index else .82
        open_ratio = previous_close
        up = close_ratio <= open_ratio
        x = left + (index + .5) * step
        open_y = int(chart_top + open_ratio * (chart_bottom - chart_top))
        close_y = int(chart_top + close_ratio * (chart_bottom - chart_top))
        upper_wick = 7 + int(((math.sin(index * 1.91) + 1.0) / 2.0) * 24)
        lower_wick = 6 + int(((math.cos(index * 1.17 + .63) + 1.0) / 2.0) * 27)
        high = max(chart_top, min(open_y, close_y) - upper_wick)
        low = min(chart_bottom, max(open_y, close_y) + lower_wick)
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
        indicator_font = _english_font(24, 600) if language == "en" else _font(24, True)
        indicator_label = "RSI (14)" if language == "en" else "RSI（14）"
        indicator_x = LABEL_LEFT
        indicator_box = draw.textbbox(
            (indicator_x, panel_top - 35), indicator_label, font=indicator_font,
        )
        if indicator_box[0] < LABEL_LEFT:
            indicator_x += LABEL_LEFT - indicator_box[0]
        draw.text(
            (indicator_x, panel_top - 35), indicator_label,
            fill=INK, font=indicator_font,
        )
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


def _draw_checklist_page(
    draw: ImageDraw.ImageDraw,
    page: dict[str, Any],
    width: int,
    top: int,
    max_bottom: int,
    language: str = "zh-CN",
) -> tuple[int, tuple[int, int, int, int] | None, bool]:
    items = [str(item).strip() for item in page.get("required_elements") or [] if str(item).strip()]
    if not items:
        return 0, None, False
    if len(items) > 4:
        return 0, None, True
    y = max(top, 360)
    item_font = _english_font(27, 400) if language == "en" else _font(28)
    item_boxes: list[tuple[int, int, int, int]] = []
    for index, item in enumerate(items[:4], start=1):
        item_box = (105, y, width - 105, y + 105)
        draw.rounded_rectangle(item_box, radius=20, fill=PANEL, outline="#DCE5EB", width=2)
        draw.ellipse((135, y + 24, 191, y + 80), fill=CYAN)
        number = str(index)
        number_width = draw.textbbox((0, 0), number, font=_font(21, True))[2]
        draw.text((163 - number_width // 2, y + 38), number, fill="white", font=_font(21, True))
        lines = _wrapped_lines(item, width - 360, item_font)
        if len(lines) > 2:
            return 0, None, True
        line_y = y + (31 if len(lines) == 1 else 15)
        for line in lines:
            draw.text((225, line_y), line, fill=INK, font=item_font)
            line_y += 38
        item_boxes.append(item_box)
        y += 125
    return len(items), _merge_bounds(*item_boxes), y - 20 > max_bottom


def _paste_chart(
    image: Image.Image,
    chart: dict[str, Any] | None,
    box: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    if not chart:
        return None
    path = Path(str(chart.get("asset_path") or ""))
    if not path.is_file():
        return None
    chart_image = Image.open(path).convert("RGB")
    left, top, right, bottom = box
    chart_image = chart_image.resize(
        (right - left, bottom - top), Image.Resampling.LANCZOS,
    )
    x, y = left, top
    image.paste(chart_image, (x, y))
    return (x, y, x + chart_image.width, y + chart_image.height)


def _draw_summary(
    draw: ImageDraw.ImageDraw,
    page: dict[str, Any],
    width: int,
    max_bottom: int,
    language: str = "zh-CN",
    start_y: int = 390,
) -> tuple[tuple[int, int, int, int] | None, bool]:
    defaults = (
        ["Identify the indicator state", "Confirm with price", "Do not rely on one indicator"]
        if language == "en" else
        ["识别指标状态", "结合价格确认", "不要依赖单一指标"]
    )
    source_items = [str(item).strip() for item in page.get("required_elements") or [] if str(item).strip()]
    if len(source_items) > 4:
        return None, True
    items = (source_items + defaults)[:4]
    y = start_y
    item_boxes: list[tuple[int, int, int, int]] = []
    for index, item in enumerate(items[:4], start=1):
        item_box = (100, y, width - 100, y + 105)
        draw.rounded_rectangle(item_box, radius=22, fill=PANEL, outline="#DCE5EB", width=2)
        draw.ellipse((135, y + 20, 201, y + 86), fill=CYAN)
        draw.text((158, y + 36), str(index), fill="white", font=_font(24, True))
        item_font = _english_font(27, 600) if language == "en" else _font(29, True)
        lines = _wrapped_lines(item, width - 360, item_font)
        if len(lines) > 2:
            return None, True
        line_y = y + (31 if len(lines) == 1 else 13)
        for line in lines:
            draw.text((235, line_y), line, fill=INK, font=item_font)
            line_y += 38
        item_boxes.append(item_box)
        y += 125
    return _merge_bounds(*item_boxes), y - 20 > max_bottom


def render_page(page: dict[str, Any], chart: dict[str, Any] | None,
                visual_assets: list[dict[str, Any]], output_path: Path,
                width: int, height: int, compact: bool = False,
                language: str = "zh-CN",
                style_contract: dict[str, Any] | None = None) -> dict[str, Any]:
    style = style_contract or {}
    style_version = str(style.get("style_version") or "")
    palette = style.get("palette") if isinstance(style.get("palette"), dict) else {}
    if style and style_version != "trading-editorial-v1":
        raise ValueError("PHOTO_STYLE_VERSION_INVALID")
    expected_palette = {
        "background": "#F7F8FA",
        "primary": "#123B5D",
        "secondary": "#2E7896",
        "accent": "#D8A12E",
        "grid": "#DCE4EA",
    }
    if style and any(palette.get(key) != value for key, value in expected_palette.items()):
        raise ValueError("PHOTO_STYLE_PALETTE_INVALID")
    primary_color = str(palette.get("primary") or INK)
    accent_color = str(palette.get("accent") or CYAN)
    image = Image.new("RGB", (width, height), "white")
    template_background_present, template_asset_key = _paste_template_background(
        image, visual_assets,
    )
    draw = ImageDraw.Draw(image)
    role = str(page.get("page_role") or "")
    visual_type = str(page.get("visual_type") or "")
    layout = "cover" if role == "cover" or visual_type == "cover_illustration" else "summary" if role == "summary" or visual_type == "summary_card" else "checklist" if role in {"checklist", "mistakes"} or visual_type == "checklist" else "example" if role == "example" or visual_type == "candlestick_demo" else "standard"
    source_title = str(page.get("title") or "").strip()
    source_body = str(page.get("body") or "").strip()
    source_copy_valid = (
        _english_copy_valid(source_title, source_body)
        if language == "en" else
        _chinese_copy_valid(source_title, source_body)
    )
    if not source_copy_valid:
        contract = "ENGLISH" if language == "en" else "CHINESE"
        raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_{contract}_COPY_REQUIRED")
    title = source_title.upper() if language == "en" else source_title
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
    chart_box = None
    checklist_box = None
    summary_box = None
    swipe_box = None
    disclaimer_count = 0

    if layout == "cover":
        if language == "en":
            header_bottom, overflow, content_box, typography_metrics = _draw_english_header(
                draw, page, width, cover=True,
                primary_color=primary_color, accent_color=accent_color,
            )
        else:
            header_bottom, overflow, content_box, typography_metrics = _draw_cover_header(draw, page, width)
        if overflow:
            raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_LAYOUT_OVERFLOW")
        if template_background_present:
            cover_visual_type = "template_background"
            topic_visual_present = True
        else:
            (
                cover_visual_type,
                cover_candle_count,
                cover_indicator_point_count,
                cover_candle_gap_ratio,
                cover_indicator_supersample,
                cover_candle_body_width,
            ) = _draw_cover_topic_visual(draw, page, width, height, language=language)
            topic_visual_present = True
            cover_asset_box, cover_asset_key, cover_asset_opacity = _paste_cover_illustration(
                image, visual_assets, (600, 285, width - 55, 700)
            )
            character_box = _paste_character(image, visual_assets, (320, 350, width - 320, height - 90))
            character_present = character_box is not None
        swipe_text = "SWIPE  →" if language == "en" else "滑动查看  →"
        swipe_font = _english_font(20, 600) if language == "en" else _font(20, True)
        swipe_position = (width - 205, height - 65)
        draw.text(swipe_position, swipe_text, fill=primary_color, font=swipe_font)
        swipe_box = _drawn_text_bounds(draw, swipe_position, swipe_text, swipe_font)
    else:
        if language == "en":
            header_bottom, overflow, content_box, typography_metrics = _draw_english_header(
                draw, page, width, cover=False,
                primary_color=primary_color, accent_color=accent_color,
            )
        else:
            header_bottom, overflow, content_box, typography_metrics = _draw_header(
                draw, title, body, width, compact
            )
        if overflow:
            raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_LAYOUT_OVERFLOW")
        if layout == "summary":
            summary_box, summary_overflow = _draw_summary(
                draw,
                page,
                width,
                height - 95,
                language=language,
                start_y=max(390, header_bottom + 28),
            )
            if summary_overflow:
                raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_LAYOUT_OVERFLOW")
        elif layout == "checklist":
            checklist_item_count, checklist_box, checklist_overflow = _draw_checklist_page(
                draw, page, width, header_bottom + 28, height - 95, language=language
            )
            if checklist_overflow:
                raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_LAYOUT_OVERFLOW")
            if checklist_item_count == 0:
                raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_CHECKLIST_REQUIRED")
        else:
            top = max(330, header_bottom + 25)
            chart_box = _paste_chart(image, chart, (0, top, width, height - 105))
            chart_present = chart_box is not None
            if visual_type in CHART_REQUIRED_TYPES and not chart_present:
                raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_CHART_REQUIRED")
        # Characters are cover-only. Content pages reserve the canvas for teaching evidence.

    disclaimer = ENGLISH_DISCLAIMER if language == "en" else CHINESE_DISCLAIMER
    disclaimer_font = _english_font(19, 400) if language == "en" else _font(22)
    footer_position = (72, height - 55)
    draw.text(footer_position, disclaimer, fill=primary_color, font=disclaimer_font)
    disclaimer_count += 1
    footer_box = _merge_bounds(
        _drawn_text_bounds(draw, footer_position, disclaimer, disclaimer_font),
        swipe_box,
    )
    layout_regions = {
        name: bounds
        for name, bounds in (typography_metrics.get("layout_regions") or {}).items()
        if bounds is not None
    }
    layout_regions["footer"] = footer_box
    if checklist_box is not None:
        layout_regions["checklist"] = checklist_box
    if summary_box is not None:
        layout_regions["summary"] = summary_box
    if chart_box is not None:
        layout_regions["chart"] = chart_box
    if character_box is not None:
        layout_regions["character"] = character_box
    character_content_overlap = _intersects(character_box, content_box)
    character_footer_overlap = _intersects(character_box, footer_box)
    character_in_bounds = character_box is None or (
        character_box[0] >= 0 and character_box[1] >= 0 and
        character_box[2] <= width and character_box[3] <= height
    )
    character_safe = character_in_bounds and not character_content_overlap and not character_footer_overlap
    overlap = _layout_overlap(layout_regions)
    if overlap:
        raise ValueError(f"PAGE_{int(page.get('page_no') or 0)}_LAYOUT_OVERFLOW")
    chinese_contract_valid = (
        language == "zh-CN"
        and _chinese_copy_valid(title, body)
        and _contains_chinese(CHINESE_DISCLAIMER)
    )
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
        "template_background_present": template_background_present,
        "template_asset_key": template_asset_key,
        "style_version": style_version,
        "layout_overflow": overflow, "risk_note_present": True, "chart_present": chart_present,
        "character_present": character_present, "layout_template": layout, "render_language": language,
        "rendered_disclaimer": disclaimer, "disclaimer_count": 1,
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
        "copy_contract_valid": source_copy_valid,
        "character_in_safe_area": character_safe, "layout_overlap": overlap,
        "character_box": character_box, "content_box": content_box,
        "character_box_intersects_content": character_content_overlap,
        "layout_regions": layout_regions,
        "decorative_left_bar": False,
        "disclaimer_count": disclaimer_count,
        "teaching_evidence": teaching_evidence,
    }
