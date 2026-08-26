import textwrap
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from .chart_renderer import _font


INK = "#141A21"
CYAN = "#32C4EA"
NAVY = "#123052"
GOLD = "#D9A62E"
SKIN = "#D3A17E"


def _wrapped_lines(text: str, limit: int) -> list[str]:
    result = []
    for paragraph in str(text or "").splitlines() or [""]:
        result.extend(textwrap.wrap(paragraph, width=limit) or [""])
    return result


def _draw_teacher(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float = 1.0) -> None:
    def box(coords):
        return tuple(int(value * scale) for value in coords)

    draw.ellipse((x + box((90, 0, 250, 190))[0], y, x + box((90, 0, 250, 190))[2], y + box((90, 0, 250, 190))[3]), fill=SKIN)
    draw.pieslice((x + int(85 * scale), y - int(18 * scale), x + int(255 * scale), y + int(120 * scale)), 180, 360, fill="#252A34")
    draw.polygon([
        (x + int(25 * scale), y + int(400 * scale)),
        (x + int(70 * scale), y + int(180 * scale)),
        (x + int(270 * scale), y + int(180 * scale)),
        (x + int(315 * scale), y + int(400 * scale)),
    ], fill=NAVY)
    draw.polygon([
        (x + int(130 * scale), y + int(180 * scale)),
        (x + int(210 * scale), y + int(180 * scale)),
        (x + int(195 * scale), y + int(300 * scale)),
        (x + int(145 * scale), y + int(300 * scale)),
    ], fill="white")
    draw.polygon([
        (x + int(170 * scale), y + int(190 * scale)),
        (x + int(190 * scale), y + int(230 * scale)),
        (x + int(170 * scale), y + int(360 * scale)),
        (x + int(150 * scale), y + int(230 * scale)),
    ], fill=GOLD)


def render_page(
    page: dict[str, Any],
    chart: dict[str, Any] | None,
    visual_assets: list[dict[str, Any]],
    output_path: Path,
    width: int,
    height: int,
    compact: bool = False,
) -> dict[str, Any]:
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    margin = 72
    title_size = 54 if compact else 62
    body_size = 32 if compact else 36
    title_lines = _wrapped_lines(str(page.get("title") or ""), 18)
    body_lines = _wrapped_lines(str(page.get("body") or ""), 34)
    overflow = len(title_lines) > 2 or len(body_lines) > 5

    y = 60
    for line in title_lines[:2]:
        draw.text((margin, y), line, fill=INK, font=_font(title_size, True))
        y += title_size + 12
    draw.rectangle((margin, y + 4, margin + 160, y + 12), fill=CYAN)
    y += 38
    for line in body_lines[:5]:
        draw.text((margin, y), line, fill=INK, font=_font(body_size))
        y += body_size + 10

    chart_present = bool(
        chart and Path(str(chart.get("asset_path") or "")).is_file()
    )
    if chart_present:
        chart_image = Image.open(chart["asset_path"]).convert("RGB")
        chart_image.thumbnail((width - 2 * margin, 480))
        chart_x = (width - chart_image.width) // 2
        chart_y = min(max(y + 25, 420), height - chart_image.height - 90)
        image.paste(chart_image, (chart_x, chart_y))

    keys = {str(item.get("asset_key")) for item in visual_assets}
    character_present = "teacher_front" in keys
    if character_present:
        _draw_teacher(draw, width - 390, height - 465, 0.95)

    risk_note = str(page.get("risk_note") or "").strip()
    if risk_note:
        draw.text((margin, height - 55), risk_note, fill="#66717D", font=_font(24))
    draw.text((width - 115, height - 55), f"{int(page['page_no']):02d}", fill=CYAN, font=_font(28, True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return {
        "page_no": int(page["page_no"]),
        "path": str(output_path),
        "width": width,
        "height": height,
        "layout_overflow": overflow,
        "risk_note_present": bool(risk_note),
        "chart_present": chart_present,
        "character_present": character_present,
    }
