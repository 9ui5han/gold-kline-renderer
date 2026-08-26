import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


CYAN = "#32C4EA"
INK = "#17212B"
MUTED = "#6B7785"


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_indicator_panel(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    left, top, right, bottom = 90, 55, width - 55, height - 70
    for value, color in ((70, "#E9A5AC"), (30, "#9DE1F1")):
        y = bottom - (bottom - top) * value / 100
        draw.line((left, y, right, y), fill=color, width=4)
        draw.text((24, y - 17), str(value), fill=INK, font=_font(25, True))
    draw.text((left, 12), "RSI 教学示意", fill=INK, font=_font(30, True))

    points = []
    for index in range(70):
        ratio = index / 69
        value = 50 + 25 * math.sin(index / 7) + 7 * math.sin(index / 2.7)
        x = left + ratio * (right - left)
        y = bottom - max(5, min(95, value)) / 100 * (bottom - top)
        points.append((x, y))
    draw.line(points, fill=CYAN, width=6, joint="curve")
    draw.rectangle((left, top, right, bottom), outline="#CCD4DC", width=2)


def _draw_candlestick_demo(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    left, top, right, bottom = 55, 45, width - 45, height - 70
    values = [42, 48, 45, 55, 63, 58, 67, 61, 73, 78, 72, 84]
    step = (right - left) / len(values)
    for index, close in enumerate(values):
        open_value = values[index - 1] if index else 38
        high = max(open_value, close) + 7
        low = min(open_value, close) - 7
        x = left + step * (index + 0.5)
        to_y = lambda value: bottom - value / 100 * (bottom - top)
        color = CYAN if close >= open_value else "#6F7780"
        draw.line((x, to_y(high), x, to_y(low)), fill=INK, width=2)
        body_top = min(to_y(open_value), to_y(close))
        body_bottom = max(to_y(open_value), to_y(close))
        draw.rectangle((x - 10, body_top, x + 10, max(body_top + 3, body_bottom)), fill=color, outline=INK)
    draw.text((left, 5), "K线教学示意", fill=INK, font=_font(30, True))


def render_chart(page: dict[str, Any], output_path: Path) -> dict[str, Any]:
    width, height = 900, 480
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    visual_type = str(page.get("visual_type") or "")
    if visual_type == "indicator_panel":
        _draw_indicator_panel(draw, width, height)
    else:
        _draw_candlestick_demo(draw, width, height)
    draw.text((width - 330, height - 40), "教学示意图｜不代表实时行情", fill=MUTED, font=_font(22))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return {
        "page_no": int(page["page_no"]),
        "asset_key": f"chart_page_{int(page['page_no']):02d}",
        "asset_type": visual_type,
        "asset_path": str(output_path),
        "is_teaching_demo": True,
        "included_elements": list(page.get("required_elements") or []),
    }
