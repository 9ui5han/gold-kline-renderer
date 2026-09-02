"""Reference-authorized content-page renderer.

Unlike the legacy propulsion renderer, this route never invents visual elements.
"""
from __future__ import annotations

import math
import tempfile
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from PIL import Image, ImageDraw, ImageFont


CANVAS = 1080
ALLOWED_TYPES = {"candlestick_chart", "indicator_panel", "zone", "line", "point", "text", "diagram"}
ANNOTATION_TYPES = {"zone", "line", "point", "diagram"}
DRAWABLE_BOX_TYPES = {"line", "point", "text", "diagram"}
REFERENCE_FONT = ImageFont.load_default()


def _error(code: str) -> None:
    raise ValueError(code)


def _box(value: Any, page_no: int) -> tuple[int, int, int, int]:
    if not isinstance(value, dict):
        _error(f"REFERENCE_ELEMENT_INVALID:{page_no}")
    try:
        x, y, w, h = (float(value[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        _error(f"REFERENCE_ELEMENT_INVALID:{page_no}")
    if (
        not all(math.isfinite(item) for item in (x, y, w, h))
        or min(x, y) < 0
        or w <= 0
        or h <= 0
        or x + w > 1
        or y + h > 1
    ):
        _error(f"REFERENCE_ELEMENT_INVALID:{page_no}")
    return round(x * CANVAS), round(y * CANVAS), round((x + w) * CANVAS), round((y + h) * CANVAS)


def _validate(page: Any) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    if not isinstance(page, dict) or type(page.get("page_no")) is not int or page["page_no"] < 1:
        _error("REFERENCE_PAGE_INVALID")
    number = page["page_no"]
    layout = page.get("layout_spec")
    if not isinstance(layout, dict) or layout.get("canvas_ratio") != "1:1":
        _error(f"REFERENCE_LAYOUT_MISSING:{number}")
    for key in ("title_box", "body_box", "visual_box"):
        _box(layout.get(key), number)
    elements = page.get("visible_elements")
    if not isinstance(elements, list):
        _error(f"REFERENCE_ELEMENT_INVALID:{number}")
    indexed: dict[str, dict[str, Any]] = {}
    for element in elements:
        if not isinstance(element, dict) or not isinstance(element.get("element_id"), str):
            _error(f"REFERENCE_ELEMENT_INVALID:{number}")
        identifier = element["element_id"]
        if not identifier or identifier in indexed or element.get("type") not in ALLOWED_TYPES:
            _error(f"REFERENCE_ELEMENT_INVALID:{number}")
        if element["type"] in DRAWABLE_BOX_TYPES and "box" not in element:
            _error(f"REFERENCE_ELEMENT_INVALID:{number}")
        if "box" in element:
            _box(element["box"], number)
        indexed[identifier] = element
    for element in indexed.values():
        parent = element.get("parent_id")
        if parent is not None and parent not in indexed:
            _error(f"REFERENCE_ELEMENT_INVALID:{number}")
    concept = page.get("concept_spec")
    if not isinstance(concept, dict) or not all(isinstance(concept.get(key), str) and concept[key] for key in ("concept_id", "direction", "lesson_intent", "rule_version")):
        _error(f"CONCEPT_EVIDENCE_MISSING:{number}")
    copy = page.get("copy")
    if not isinstance(copy, dict) or not all(isinstance(copy.get(key), str) and copy[key].strip() for key in ("title", "body")):
        _error(f"COPY_VISUAL_CONCEPT_MISMATCH:{number}")
    annotations = page.get("annotations", [])
    if not isinstance(annotations, list):
        _error(f"REFERENCE_ELEMENT_INVALID:{number}")
    for annotation in annotations:
        if not isinstance(annotation, dict) or annotation.get("source_element_id") not in indexed:
            _error(f"ANNOTATION_NOT_IN_REFERENCE:{number}")
        annotation_type = annotation.get("type")
        source_type = indexed[annotation["source_element_id"]]["type"]
        if annotation_type not in ANNOTATION_TYPES or source_type != annotation_type:
            _error(f"ANNOTATION_NOT_IN_REFERENCE:{number}")
        if annotation.get("rule_version") != concept["rule_version"]:
            _error(f"CONCEPT_EVIDENCE_MISSING:{number}")
    return page, indexed


def _render(page: dict[str, Any], elements: dict[str, dict[str, Any]], path: Path) -> dict[str, Any]:
    image = Image.new("RGB", (CANVAS, CANVAS), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    rendered_ids: list[str] = []

    def mark_rendered(identifier: str) -> None:
        if identifier not in rendered_ids:
            rendered_ids.append(identifier)

    def draw_text_in_box(text: str, box: tuple[int, int, int, int]) -> None:
        bbox = draw.multiline_textbbox((0, 0), text, font=REFERENCE_FONT)
        if bbox[2] - bbox[0] > box[2] - box[0] or bbox[3] - bbox[1] > box[3] - box[1]:
            _error(f"LAYOUT_OVERFLOW:{page['page_no']}")
        draw.multiline_text((box[0], box[1]), text, fill="#123B5D", font=REFERENCE_FONT)

    title_box = _box(page["layout_spec"]["title_box"], page["page_no"])
    body_box = _box(page["layout_spec"]["body_box"], page["page_no"])
    draw_text_in_box(page["copy"]["title"], title_box)
    draw_text_in_box(page["copy"]["body"], body_box)
    visual = _box(page["layout_spec"]["visual_box"], page["page_no"])
    ohlc = page.get("visual_data", {}).get("ohlc") or []
    chart = next((item for item in elements.values() if item["type"] == "candlestick_chart"), None)
    if chart and ohlc:
        chart_identifier = next(identifier for identifier, item in elements.items() if item is chart)
        mark_rendered(chart_identifier)
        left, top, right, bottom = _box(chart.get("box", page["layout_spec"]["visual_box"]), page["page_no"])
        try:
            prices = [float(bar[key]) for bar in ohlc for key in ("h", "l")]
        except (KeyError, TypeError, ValueError):
            _error(f"REFERENCE_ELEMENT_INVALID:{page['page_no']}")
        low, high = min(prices), max(prices)
        span = max(high - low, 0.01)
        pitch = (right - left) / len(ohlc)
        for index, bar in enumerate(ohlc):
            try:
                o, h, l, c = (float(bar[key]) for key in ("o", "h", "l", "c"))
            except (KeyError, TypeError, ValueError):
                _error(f"REFERENCE_ELEMENT_INVALID:{page['page_no']}")
            if l > min(o, c) or h < max(o, c): _error(f"REFERENCE_ELEMENT_INVALID:{page['page_no']}")
            x = left + (index + .5) * pitch
            y = lambda value: bottom - (value - low) / span * (bottom - top)
            color = "#D8A12E" if c >= o else "#123B5D"
            draw.line((x, y(h), x, y(l)), fill=color, width=3)
            draw.rectangle((x - pitch*.32, min(y(o), y(c)), x + pitch*.32, max(y(o), y(c), min(y(o), y(c))+3)), fill=color)
        for annotation in page.get("annotations", []):
            if annotation.get("type") == "zone":
                mark_rendered(annotation["source_element_id"])
                start, end = int(annotation["start_bar"]), int(annotation["end_bar"])
                if not 0 <= start <= end < len(ohlc): _error(f"REFERENCE_ELEMENT_INVALID:{page['page_no']}")
                y1, y2 = sorted((y(float(annotation["price_high"])), y(float(annotation["price_low"]))))
                draw.rectangle((left + start*pitch, y1, left + (end+1)*pitch, y2), fill=(46,120,150,70), outline=(46,120,150,230), width=2)
    panels = []
    series = page.get("visual_data", {}).get("indicator_series")
    for identifier, element in elements.items():
        if element["type"] != "indicator_panel":
            continue
        if not isinstance(series, list) or len(series) < 2:
            _error(f"INDICATOR_DATA_INSUFFICIENT:{page['page_no']}")
        values = [float(value) for value in series]
        if not all(math.isfinite(value) for value in values):
            _error(f"INDICATOR_DATA_INSUFFICIENT:{page['page_no']}")
        left, top, right, bottom = _box(element.get("box"), page["page_no"])
        low, high = min(values), max(values)
        span = max(high - low, .01)
        points = [(left + index * (right-left) / (len(values)-1), bottom - (value-low) / span * (bottom-top)) for index, value in enumerate(values)]
        draw.line(points, fill="#2E7896", width=3)
        mark_rendered(identifier)
        panels.append(identifier)
    for identifier, element in elements.items():
        element_type = element["type"]
        if element_type not in DRAWABLE_BOX_TYPES:
            continue
        box = _box(element["box"], page["page_no"])
        if element_type == "line":
            draw.line((box[0], box[1], box[2], box[3]), fill="#D8A12E", width=4)
        elif element_type == "point":
            center_x = (box[0] + box[2]) // 2
            center_y = (box[1] + box[3]) // 2
            radius = max(4, min(box[2] - box[0], box[3] - box[1]) // 5)
            draw.ellipse((center_x - radius, center_y - radius, center_x + radius, center_y + radius), fill="#E05A47")
        elif element_type == "diagram":
            draw.rectangle(box, outline="#536270", width=3)
        label = element.get("label_text")
        if isinstance(label, str) and label:
            draw_text_in_box(label, box)
        mark_rendered(identifier)
    image.save(path, "PNG")
    return {"page_no": page["page_no"], "asset_type": "reference_content", "asset_path": str(path), "width": CANVAS, "height": CANVAS, "rendered_element_ids": rendered_ids, "rendered_indicator_panels": panels, "rendered_copy_boxes": ["title_box", "body_box"], "visual_box": visual}


def build_reference_carousel_router(data_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/v1/carousel/reference", tags=["carousel-reference"])

    @router.post("/render")
    def render(payload: dict[str, Any]) -> dict:
        try:
            if payload.get("schema_version") != "reference-carousel-render-v1" or payload.get("language") not in {"zh-CN", "en"}:
                _error("REFERENCE_RENDER_CONTRACT_INVALID")
            pages = payload.get("pages")
            if not isinstance(pages, list) or not pages: _error("REFERENCE_PAGE_INVALID")
            root = Path(data_dir) / "reference-carousel-work"; root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
                assets = []
                for raw in pages:
                    page, elements = _validate(raw)
                    assets.append(_render(page, elements, Path(temporary) / f"page_{page['page_no']:02d}.png"))
                destination = root / f"reference-{uuid.uuid4().hex}"; Path(temporary).rename(destination)
            for item in assets: item["asset_path"] = str(destination / Path(item["asset_path"]).name)
            return {"schema_version": "reference-carousel-assets-v1", "assets": assets}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
