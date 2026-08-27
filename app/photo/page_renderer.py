from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .chart_renderer import CYAN, INK, _font

MUTED, PANEL = "#66717D", "#F3F7FA"
CHINESE_DISCLAIMER = "教学示意图｜不代表实时行情"


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


def _draw_header(draw: ImageDraw.ImageDraw, title: str, body: str, width: int,
                 compact: bool) -> tuple[int, bool, tuple[int, int, int, int]]:
    margin = 72
    title_size, body_size = (52, 31) if compact else (60, 35)
    title_font = _font(title_size, True)
    body_font = _font(body_size)
    available_width = width - margin * 2
    title_lines = _wrapped_lines(title, available_width, title_font)
    body_lines = _wrapped_lines(body, available_width, body_font)
    overflow = len(title_lines) > 2 or len(body_lines) > 4
    y = 58
    for line in title_lines[:2]:
        draw.text((margin, y), line, fill=INK, font=title_font)
        y += title_size + 10
    draw.rectangle((margin, y + 2, margin + 160, y + 10), fill=CYAN)
    y += 34
    for line in body_lines[:4]:
        draw.text((margin, y), line, fill=INK, font=body_font)
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
    character_box = None
    content_box = None
    disclaimer_count = 0

    if layout == "cover":
        _draw_market_background(draw, width, height)
        _, overflow, content_box = _draw_header(draw, title, body, width, compact)
        chart_present = _paste_chart(image, chart, (40, 330, width - 40, 790))
        character_box = _paste_character(image, visual_assets, (285, 350, width - 285, height - 90))
        character_present = character_box is not None
        draw.rounded_rectangle((width - 270, height - 145, width - 75, height - 82), radius=28, fill=INK)
        draw.text((width - 235, height - 132), "滑动查看", fill="white", font=_font(25, True))
    else:
        header_bottom, overflow, content_box = _draw_header(draw, title, body, width, compact)
        if layout == "summary":
            _draw_summary(draw, page, width)
        else:
            top = max(390, header_bottom + 25)
            chart_present = _paste_chart(image, chart, (72, top, width - 72, height - 105))
        # Characters are cover-only. Content pages reserve the canvas for teaching evidence.

    draw.text((72, height - 55), CHINESE_DISCLAIMER, fill=MUTED, font=_font(22))
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
        "chinese_contract_valid": chinese_contract_valid,
        "character_in_safe_area": character_safe, "layout_overlap": overlap,
        "character_box": character_box, "content_box": content_box,
        "character_box_intersects_content": character_content_overlap,
        "disclaimer_count": disclaimer_count,
        "teaching_evidence": teaching_evidence,
    }
