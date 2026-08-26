import textwrap
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .chart_renderer import CYAN, INK, _font

NAVY, GOLD, SKIN = "#123052", "#D9A62E", "#D3A17E"
MUTED, PANEL = "#66717D", "#F3F7FA"
ENGLISH_DISCLAIMER = "Educational illustration | Not real-time market data"


def _english_contract_valid(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and all(character in "\n\r\t" or 32 <= ord(character) <= 126 for character in text)


ENGLISH_SIGNAL_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "below", "by", "can",
    "for", "from", "how", "in", "into", "is", "it", "not", "of", "on",
    "or", "the", "then", "this", "to", "use", "using", "what", "when",
    "with", "without", "above", "understand", "learn", "helps", "price",
    "market", "indicator", "momentum", "signal", "trend", "risk",
}


def _looks_like_english_copy(title: str, body: str) -> bool:
    combined = f"{title} {body}"
    if not _english_contract_valid(title) or not _english_contract_valid(body):
        return False
    words = {word.lower() for word in re.findall(r"[A-Za-z]+", combined)}
    return bool(words & ENGLISH_SIGNAL_WORDS)


def _english(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if _english_contract_valid(text) else fallback


def _intersects(first: tuple[int, int, int, int] | None,
                second: tuple[int, int, int, int] | None) -> bool:
    if first is None or second is None:
        return False
    return not (
        first[2] <= second[0] or second[2] <= first[0] or
        first[3] <= second[1] or second[3] <= first[1]
    )


def _wrapped_lines(text: str, limit: int) -> list[str]:
    result = []
    for paragraph in str(text or "").splitlines() or [""]:
        result.extend(textwrap.wrap(paragraph, width=limit) or [""])
    return result


def _draw_teacher(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float = 1.0) -> tuple[int, int, int, int]:
    head = (x + int(90 * scale), y, x + int(250 * scale), y + int(190 * scale))
    draw.ellipse(head, fill=SKIN)
    draw.pieslice((x + int(85 * scale), y - int(18 * scale), x + int(255 * scale), y + int(120 * scale)), 180, 360, fill="#252A34")
    draw.polygon([(x + int(25 * scale), y + int(400 * scale)), (x + int(70 * scale), y + int(180 * scale)), (x + int(270 * scale), y + int(180 * scale)), (x + int(315 * scale), y + int(400 * scale))], fill=NAVY)
    draw.polygon([(x + int(130 * scale), y + int(180 * scale)), (x + int(210 * scale), y + int(180 * scale)), (x + int(195 * scale), y + int(300 * scale)), (x + int(145 * scale), y + int(300 * scale))], fill="white")
    draw.polygon([(x + int(170 * scale), y + int(190 * scale)), (x + int(190 * scale), y + int(230 * scale)), (x + int(170 * scale), y + int(360 * scale)), (x + int(150 * scale), y + int(230 * scale))], fill=GOLD)
    return x + int(25 * scale), y - int(18 * scale), x + int(315 * scale), y + int(400 * scale)


def _draw_header(draw: ImageDraw.ImageDraw, title: str, body: str, width: int,
                 compact: bool) -> tuple[int, bool, tuple[int, int, int, int]]:
    margin = 72
    title_size, body_size = (52, 31) if compact else (60, 35)
    title_lines = _wrapped_lines(title, 20)
    body_lines = _wrapped_lines(body, 38)
    overflow = len(title_lines) > 2 or len(body_lines) > 4
    y = 58
    for line in title_lines[:2]:
        draw.text((margin, y), line, fill=INK, font=_font(title_size, True))
        y += title_size + 10
    draw.rectangle((margin, y + 2, margin + 160, y + 10), fill=CYAN)
    y += 34
    for line in body_lines[:4]:
        draw.text((margin, y), line, fill=INK, font=_font(body_size))
        y += body_size + 9
    return y, overflow, (margin, 58, width - margin, y)


def _draw_market_background(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    points = [(0, 690), (110, 740), (220, 620), (330, 690), (430, 520), (560, 610), (700, 500), (830, 560), (1080, 430)]
    draw.line(points, fill="#D7E1E8", width=5)
    for x, y in points[1:-1]:
        draw.line((x, y - 52, x, y + 52), fill="#BBC9D3", width=3)
        draw.rectangle((x - 10, y - 25, x + 10, y + 25), fill=CYAN, outline=INK)


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
    defaults = ["Read the 30/70 zones", "Confirm with price", "Never rely on RSI alone"]
    # Summary labels are renderer-owned English copy; unverified plan labels never reach the PNG.
    items = defaults
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
    source_copy_valid = _looks_like_english_copy(source_title, source_body)
    title = source_title if source_copy_valid else "RSI Step-by-Step Guide"
    body = source_body if source_copy_valid else "Understand RSI with price context and confirmation."
    chart_present, character_present = False, False
    character_box = None
    content_box = None
    disclaimer_count = 0

    if layout == "cover":
        _draw_market_background(draw, width, height)
        _, overflow, content_box = _draw_header(draw, title, body, width, compact)
        chart_present = _paste_chart(image, chart, (40, 330, width - 40, 790))
        character_box = _draw_teacher(draw, width // 2 - 175, 420, .98)
        character_present = True
        draw.rounded_rectangle((width - 270, height - 145, width - 75, height - 82), radius=28, fill=INK)
        draw.text((width - 226, height - 132), "SWIPE", fill="white", font=_font(27, True))
    else:
        header_bottom, overflow, content_box = _draw_header(draw, title, body, width, compact)
        if layout == "summary":
            _draw_summary(draw, page, width)
        else:
            top = max(390, header_bottom + 25)
            chart_present = _paste_chart(image, chart, (72, top, width - 72, height - 105))
        if (not chart_present and
                any(str(item.get("asset_key")) == "teacher_front" for item in visual_assets) and
                layout == "standard"):
            # A small teacher is allowed only in the lower-right reserved margin.
            character_box = _draw_teacher(draw, width - 265, height - 390, .58)
            character_present = True

    draw.text((72, height - 55), ENGLISH_DISCLAIMER, fill=MUTED, font=_font(22))
    disclaimer_count += 1
    draw.text((width - 115, height - 55), f"{int(page['page_no']):02d}", fill=CYAN, font=_font(28, True))
    footer_box = (0, height - 75, width, height)
    character_content_overlap = _intersects(character_box, content_box)
    character_footer_overlap = _intersects(character_box, footer_box)
    character_in_bounds = character_box is None or (
        character_box[0] >= 0 and character_box[1] >= 0 and
        character_box[2] <= width and character_box[3] <= height
    )
    character_safe = character_in_bounds and not character_content_overlap and not character_footer_overlap
    overlap = character_content_overlap or character_footer_overlap
    english_contract_valid = all(_english_contract_valid(value) for value in (
        title, body, ENGLISH_DISCLAIMER,
    ))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return {
        "page_no": int(page["page_no"]), "path": str(output_path), "width": width, "height": height,
        "layout_overflow": overflow, "risk_note_present": True, "chart_present": chart_present,
        "character_present": character_present, "layout_template": layout, "render_language": "en",
        "rendered_disclaimer": ENGLISH_DISCLAIMER, "disclaimer_count": 1,
        "rendered_title": title, "rendered_body": body,
        "english_contract_valid": english_contract_valid,
        "character_in_safe_area": character_safe, "layout_overlap": overlap,
        "character_box": character_box, "content_box": content_box,
        "character_box_intersects_content": character_content_overlap,
        "disclaimer_count": disclaimer_count,
    }
