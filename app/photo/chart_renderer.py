import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

CYAN, INK, MUTED = "#32C4EA", "#17212B", "#6B7785"
GRID, RED = "#D9E0E6", "#E99AA5"
BLUE_FILL, RED_FILL = "#D9F3FA", "#FBE3E6"


def _english_contract_valid(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and all(character in "\n\r\t" or 32 <= ord(character) <= 126 for character in text)


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc" if bold else "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _english(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text if _english_contract_valid(text) else fallback


def _rsi_points(left: int, top: int, right: int, bottom: int, mode: str) -> list[tuple[float, float]]:
    points = []
    for index in range(72):
        ratio = index / 71
        if mode == "overbought":
            value = 50 + 30 * math.sin(index / 11) + 10 * math.sin(index / 4.2)
        elif mode == "oversold":
            value = 48 - 30 * math.sin(index / 11) - 10 * math.sin(index / 4.2)
        else:
            value = 50 + 25 * math.sin(index / 7) + 7 * math.sin(index / 2.7)
        value = max(5, min(95, value))
        points.append((left + ratio * (right - left), bottom - value / 100 * (bottom - top)))
    return points


def _draw_rsi_panel(draw: ImageDraw.ImageDraw, width: int, height: int, mode: str) -> None:
    left, top, right, bottom = 92, 64, width - 42, height - 42
    if mode == "overbought":
        draw.rectangle((left, top, right, bottom - (bottom - top) * .70), fill=RED_FILL)
    elif mode == "oversold":
        draw.rectangle((left, bottom - (bottom - top) * .30, right, bottom), fill=BLUE_FILL)
    for value, color in ((100, GRID), (70, RED), (30, CYAN), (0, GRID)):
        y = bottom - (bottom - top) * value / 100
        draw.line((left, y, right, y), fill=color, width=3)
        draw.text((25, y - 14), str(value), fill=INK, font=_font(22, True))
    label = {"overbought": "OVERBOUGHT ZONE", "oversold": "OVERSOLD ZONE"}.get(mode, "RSI (0-100)")
    draw.text((left, 15), label, fill=INK, font=_font(28, True))
    points = _rsi_points(left, top, right, bottom, mode)
    draw.line(points, fill=CYAN, width=6, joint="curve")
    if mode == "overbought":
        target = min(points, key=lambda item: item[1])
        draw.ellipse((target[0] - 8, target[1] - 8, target[0] + 8, target[1] + 8), fill=RED)
        draw.text((target[0] - 80, max(top, target[1] - 38)), "Above 70", fill=INK, font=_font(20, True))
    elif mode == "oversold":
        target = max(points, key=lambda item: item[1])
        draw.ellipse((target[0] - 8, target[1] - 8, target[0] + 8, target[1] + 8), fill=CYAN)
        draw.text((target[0] - 75, min(bottom - 28, target[1] + 12)), "Below 30", fill=INK, font=_font(20, True))
    draw.rectangle((left, top, right, bottom), outline="#BFCAD4", width=2)


def _draw_candles(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    values = [42, 48, 45, 55, 63, 58, 67, 61, 73, 78, 72, 84]
    step = (right - left) / len(values)
    for index, close in enumerate(values):
        open_value = values[index - 1] if index else 38
        high, low = max(open_value, close) + 7, min(open_value, close) - 7
        x = left + step * (index + 0.5)
        to_y = lambda value: bottom - value / 100 * (bottom - top)
        color = CYAN if close >= open_value else "#727B85"
        draw.line((x, to_y(high), x, to_y(low)), fill=INK, width=2)
        body_top, body_bottom = sorted((to_y(open_value), to_y(close)))
        draw.rectangle((x - 10, body_top, x + 10, max(body_top + 3, body_bottom)), fill=color, outline=INK)


def _draw_price_rsi_example(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    draw.text((55, 10), "PRICE + RSI CONFIRMATION", fill=INK, font=_font(28, True))
    _draw_candles(draw, (55, 55, width - 45, 265))
    left, top, right, bottom = 75, 305, width - 45, height - 35
    y30 = bottom - (bottom - top) * .30
    draw.rectangle((left, y30, right, bottom), fill=BLUE_FILL)
    draw.line((left, y30, right, y30), fill=CYAN, width=3)
    points = _rsi_points(left, top, right, bottom, "oversold")
    draw.line(points, fill="#248DB2", width=5, joint="curve")
    low = max(points, key=lambda item: item[1])
    draw.ellipse((low[0] - 7, low[1] - 7, low[0] + 7, low[1] + 7), fill=CYAN)
    draw.text((20, y30 - 12), "30", fill=INK, font=_font(20, True))
    draw.text((left, top - 28), "RSI", fill=INK, font=_font(22, True))


def _draw_checklist(draw: ImageDraw.ImageDraw, width: int, height: int, elements: list[Any]) -> None:
    draw.text((55, 18), "RSI USAGE CHECKLIST", fill=INK, font=_font(30, True))
    defaults = ["Confirm the broader trend", "Check price structure", "Wait for confirmation", "Manage risk"]
    # Checklist copy is renderer-owned English; plan labels are semantic hints only.
    items = defaults
    y = 88
    for item in items[:4]:
        draw.rounded_rectangle((55, y, width - 55, y + 68), radius=16, fill="#F3F7FA", outline="#DCE5EB", width=2)
        draw.ellipse((78, y + 18, 110, y + 50), fill=CYAN)
        draw.line((87, y + 34, 95, y + 42, 104, y + 25), fill="white", width=4)
        draw.text((132, y + 16), item[:42], fill=INK, font=_font(25))
        y += 82


def _draw_steps(draw: ImageDraw.ImageDraw, width: int, height: int, elements: list[Any]) -> None:
    defaults = ["Identify the setup", "Check context", "Wait for confirmation"]
    for index in range(3):
        x = 55 + index * 280
        draw.ellipse((x, 120, x + 76, 196), fill=CYAN)
        draw.text((x + 27, 136), str(index + 1), fill="white", font=_font(28, True))
        text = defaults[index]
        draw.text((x - 5, 225), text[:20], fill=INK, font=_font(22, True))
        if index < 2:
            draw.line((x + 90, 158, x + 250, 158), fill="#AAB7C2", width=4)


def _template_key(page: dict[str, Any]) -> str:
    visual_type = str(page.get("visual_type") or "")
    combined = f"{page.get('visual_focus', '')} {' '.join(str(x) for x in page.get('required_elements') or [])}".lower()
    if visual_type == "indicator_panel":
        return "rsi_panel"
    if visual_type == "zone_diagram":
        return "rsi_oversold" if any(word in combined for word in ("oversold", "below 30", "low rsi")) else "rsi_overbought"
    if visual_type == "checklist":
        return "checklist"
    if visual_type in {"candlestick_demo", "market_chart"}:
        return "price_rsi_example"
    if visual_type in {"comparison", "step_diagram", "flow_diagram"}:
        return "steps"
    return "rsi_panel"


def render_chart(page: dict[str, Any], output_path: Path) -> dict[str, Any]:
    width, height = 900, 480
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    template = _template_key(page)
    if template == "rsi_panel":
        _draw_rsi_panel(draw, width, height, "panel")
    elif template == "rsi_overbought":
        _draw_rsi_panel(draw, width, height, "overbought")
    elif template == "rsi_oversold":
        _draw_rsi_panel(draw, width, height, "oversold")
    elif template == "checklist":
        _draw_checklist(draw, width, height, list(page.get("required_elements") or []))
    elif template == "price_rsi_example":
        _draw_price_rsi_example(draw, width, height)
    else:
        _draw_steps(draw, width, height, list(page.get("required_elements") or []))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG")
    return {
        "page_no": int(page["page_no"]), "asset_key": f"chart_page_{int(page['page_no']):02d}",
        "asset_type": str(page.get("visual_type") or ""), "asset_path": str(output_path),
        "is_teaching_demo": True, "included_elements": list(page.get("required_elements") or []),
        "template_key": template, "render_language": "en", "disclaimer_drawn": False,
    }
