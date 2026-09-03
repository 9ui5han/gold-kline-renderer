"""Render the generated-kline-v1 contract as a clean candlestick image."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, model_validator


class KlineBar(BaseModel):
    model_config = ConfigDict(extra="ignore")

    t: int | float | str
    o: float
    h: float
    l: float
    c: float


class NormalizedBox(BaseModel):
    model_config = ConfigDict(extra="ignore")

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class KlineAnnotation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    annotation_id: str = Field(min_length=1, max_length=64)
    type: Literal["ob", "pb"]
    label: str = Field(min_length=1, max_length=32)
    direction: Literal["bullish", "bearish", "neutral"]
    start_index: int = Field(ge=0)
    end_index: int = Field(ge=0)
    price_low: float
    price_high: float

    @model_validator(mode="after")
    def validate_range(self) -> "KlineAnnotation":
        if self.end_index < self.start_index:
            raise ValueError("ANNOTATION_INDEX_RANGE_INVALID")
        if self.price_high < self.price_low:
            raise ValueError("ANNOTATION_PRICE_RANGE_INVALID")
        return self


class KlinePanel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    panel_id: str = Field(min_length=1, max_length=64)
    visual_type: Literal["candlestick", "price_path", "mixed"]
    bars: list[KlineBar] = Field(min_length=20, max_length=300)
    annotations: list[KlineAnnotation] = Field(default_factory=list, max_length=20)
    plot_box: NormalizedBox | None = None


class TextOverlay(BaseModel):
    model_config = ConfigDict(extra="ignore")

    block_id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=500)
    role: Literal["title", "body", "label", "list", "unknown"] = "body"
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    align: Literal["left", "center", "right", "unknown"] = "left"
    font_size_ratio: float = Field(gt=0, le=0.2)
    confidence: float = Field(ge=0, le=1)


class KlineRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["generated-kline-v1"]
    panels: list[KlinePanel] = Field(min_length=1, max_length=4)
    text_overlays: list[TextOverlay] = Field(default_factory=list, max_length=40)


class KlineRenderResponse(BaseModel):
    schema_version: Literal["kline-render-v1"] = "kline-render-v1"
    status: Literal["completed"] = "completed"
    image_url: str
    panel_count: int
    bar_count: int


UP_FILL = (242, 245, 248)
DOWN_FILL = (48, 70, 126)
OUTLINE = (24, 30, 40)
BACKGROUND = (255, 255, 255)
ZONE_OB_COLOR = (112, 163, 201, 105)
ZONE_PB_COLOR = (232, 173, 88, 105)
ZONE_LABEL_PB = (173, 103, 20)
TITLE_ACCENT = DOWN_FILL
BODY_TEXT = (28, 31, 36)
RENDER_SCALE = 4
TEXT_RENDER_SCALE = RENDER_SCALE
CANVAS_WIDTH = 1024
CANVAS_HEIGHT = 1024


def _visible_zone_color(color: tuple[int, int, int, int]) -> tuple[int, int, int]:
    alpha = color[3] / 255
    return tuple(
        round(color[index] * alpha + BACKGROUND[index] * (1 - alpha))
        for index in range(3)
    )


# These are the colors visible after compositing the translucent zones on the
# white canvas. They are kept as named RGB values for callers and tests.
ZONE_FILL = _visible_zone_color(ZONE_OB_COLOR)
ZONE_FILL_PB = _visible_zone_color(ZONE_PB_COLOR)
ZONE_LABEL = (218, 72, 84)


def _price_y(price: float, price_min: float, price_max: float, top: int, height: int) -> float:
    span = max(price_max - price_min, 1e-9)
    return top + (price_max - price) / span * height


def _body_width(cell_width: float) -> int:
    # Target another 2x increase over the original candle width.  The layout
    # helper below clamps this target when the requested gap and canvas width
    # leave less room, while never allowing a sub-3px candle body.
    return max(3, min(96, int(cell_width * 5.60)))


def _bar_layout(width: int, bar_count: int, render_scale: int) -> tuple[float, float, float]:
    """Return (first_center, step, body_width) with a visible candle gap."""
    if bar_count <= 0:
        return float(width) / 2, float(width), 2.0
    nominal_cell = width / bar_count
    target_body = _body_width(nominal_cell / render_scale) * render_scale
    minimum_gap = 2.5 * render_scale
    max_body = (width - minimum_gap * max(0, bar_count - 1)) / bar_count
    body_width = max(3.0 * render_scale, min(float(target_body), max_body))
    step = body_width + minimum_gap
    data_width = body_width * bar_count + minimum_gap * max(0, bar_count - 1)
    first_center = (width - data_width) / 2 + body_width / 2
    return first_center, step, body_width


def _zone_font(scale: float = 1.0) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    font_size = max(1, round(19 * scale))
    bold_font_paths = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    )
    for font_path in bold_font_paths:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=font_size)
    return ImageFont.load_default(size=font_size)


def _text_font(overlay: TextOverlay, canvas_width: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    ratio = min(overlay.font_size_ratio, 0.032) if overlay.role == "title" else overlay.font_size_ratio
    font_size = max(12, round(canvas_width * ratio))
    bold = overlay.role in {"title", "label"}
    paths = (
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ) if bold else (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for path in paths:
        if Path(path).exists():
            return ImageFont.truetype(path, size=font_size)
    return ImageFont.load_default(size=font_size)


def _draw_text_overlays(image: Image.Image, overlays: list[TextOverlay], render_scale: int) -> None:
    if not overlays:
        return
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for overlay in overlays:
        # OB/PB labels are rendered from the generated K-line annotations.
        # Drawing the reference labels again would duplicate them and could
        # leave labels floating away from the newly generated zones.
        if overlay.role == "label":
            continue
        left = overlay.x * width
        top = overlay.y * height
        box_width = overlay.width * width
        box_height = overlay.height * height
        font = _text_font(overlay, width)
        lines = _wrap_text(draw, overlay.text, font, box_width)
        wrapped_text = "\n".join(lines)
        text_box = draw.multiline_textbbox((0, 0), wrapped_text, font=font, spacing=max(2, round(font.size * 0.22)))
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        if overlay.align == "center":
            text_x = left + (box_width - text_width) / 2
        elif overlay.align == "right":
            text_x = left + box_width - text_width
        else:
            text_x = left
        text_y = top + max(0, (box_height - text_height) / 2)
        if overlay.role == "title" and "PROPULSION BLOCK" in wrapped_text and "\n" not in wrapped_text:
            prefix, accent = wrapped_text.split("PROPULSION BLOCK", 1)
            prefix_width = draw.textlength(prefix, font=font)
            draw.text((text_x, text_y), prefix, font=font, fill=BODY_TEXT)
            draw.text((text_x + prefix_width, text_y), "PROPULSION BLOCK", font=font, fill=TITLE_ACCENT)
        else:
            draw.multiline_text(
                (text_x, text_y),
                wrapped_text,
                font=font,
                fill=BODY_TEXT,
                spacing=max(2, round(font.size * 0.22)),
                align=overlay.align if overlay.align != "unknown" else "left",
            )


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: float,
) -> list[str]:
    """Wrap English text by words, hard-wrapping only unusually long tokens."""
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if draw.textlength(candidate, font=font) <= max_width or not current:
                current = candidate
                continue
            lines.append(current)
            current = word
        if current:
            lines.append(current)
    return lines or [""]


def _box_intersects(left: NormalizedBox, right: NormalizedBox, gap: float) -> bool:
    return not (
        left.x + left.width + gap <= right.x
        or right.x + right.width + gap <= left.x
        or left.y + left.height + gap <= right.y
        or right.y + right.height + gap <= left.y
    )


def _panel_box(
    panel: KlinePanel,
    fallback: NormalizedBox,
    text_boxes: list[NormalizedBox],
) -> NormalizedBox:
    """Use the reference plot box and move it below text when boxes overlap."""
    margin_x = 36 / CANVAS_WIDTH
    margin_y = 28 / CANVAS_HEIGHT
    source = panel.plot_box or fallback
    x = max(margin_x, min(source.x, 1 - margin_x - 0.01))
    y = max(margin_y, min(source.y, 1 - margin_y - 0.05))
    width = min(source.width, 1 - margin_x - x)
    height = min(source.height, 1 - margin_y - y)
    gap = max(margin_x, margin_y)
    current = NormalizedBox(x=x, y=y, width=max(width, 0.05), height=max(height, 0.05))
    for text_box in text_boxes:
        if not _box_intersects(current, text_box, gap):
            continue
        below = text_box.y + text_box.height + gap
        if below < 1 - margin_y - 0.08:
            current = NormalizedBox(
                x=current.x,
                y=max(current.y, below),
                width=current.width,
                height=max(0.08, min(current.height, 1 - margin_y - below)),
            )
        else:
            available_height = max(0.08, text_box.y - gap - current.y)
            current = NormalizedBox(
                x=current.x,
                y=current.y,
                width=current.width,
                height=min(current.height, available_height),
            )
    return current


def _panel_price_bounds(panel: KlinePanel) -> tuple[float, float]:
    values = [value for bar in panel.bars for value in (bar.h, bar.l)]
    values.extend(
        value
        for annotation in panel.annotations
        for value in (annotation.price_high, annotation.price_low)
    )
    price_min = min(values)
    price_max = max(values)
    span = max(price_max - price_min, 1e-9)
    padding = span * 0.06
    return price_min - padding, price_max + padding


def _draw_zone_layers(
    image: Image.Image,
    panel: KlinePanel,
    left: int,
    top: int,
    width: int,
    height: int,
) -> Image.Image:
    """Composite each zone separately so overlapping zones blend together."""
    price_min, price_max = _panel_price_bounds(panel)
    cell_width = width / len(panel.bars)

    for annotation in panel.annotations:
        start_index = max(0, min(len(panel.bars) - 1, annotation.start_index))
        end_index = max(0, min(len(panel.bars) - 1, annotation.end_index))
        if end_index < start_index:
            continue

        left_x = left + start_index * cell_width
        right_x = left + (end_index + 1) * cell_width
        top_y = _price_y(annotation.price_high, price_min, price_max, top, height)
        bottom_y = _price_y(annotation.price_low, price_min, price_max, top, height)
        zone_color = ZONE_OB_COLOR if annotation.type == "ob" else ZONE_PB_COLOR

        layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.rectangle(
            (left_x, top_y, right_x, bottom_y),
            fill=zone_color,
        )
        image = Image.alpha_composite(image, layer)

    return image


def _draw_panel(
    draw: ImageDraw.ImageDraw,
    panel: KlinePanel,
    left: int,
    top: int,
    width: int,
    height: int,
    draw_zones: bool = True,
    text_layer: Image.Image | None = None,
    render_scale: int = 1,
) -> None:
    price_min, price_max = _panel_price_bounds(panel)

    first_center, cell_width, body_width = _bar_layout(
        width,
        len(panel.bars),
        render_scale,
    )
    # The complete generated candle sequence is the horizontal centering
    # reference.  OB/PB zones use the same bar coordinates and therefore move
    # with their corresponding candles instead of being centered separately.
    # Keep the complete candle span inside the plot's horizontal margins after
    # the annotation-centering shift.
    data_left = first_center - cell_width / 2
    data_right = first_center + (len(panel.bars) - 1) * cell_width + cell_width / 2
    min_edge = 0.02 * width
    max_edge = 0.98 * width
    if data_left < min_edge:
        first_center += min_edge - data_left
    if data_right > max_edge:
        first_center -= data_right - max_edge
    wick_width = max(1, round(render_scale))

    if draw_zones:
        for annotation in panel.annotations:
            start_index = max(0, min(len(panel.bars) - 1, annotation.start_index))
            end_index = max(0, min(len(panel.bars) - 1, annotation.end_index))
            if end_index < start_index:
                continue

            left_x = left + max(0.0, first_center + start_index * cell_width - cell_width / 2)
            right_x = left + min(width, first_center + (end_index + 1) * cell_width - cell_width / 2)
            top_y = _price_y(
                annotation.price_high,
                price_min,
                price_max,
                top,
                height,
            )
            bottom_y = _price_y(
                annotation.price_low,
                price_min,
                price_max,
                top,
                height,
            )

            zone_color = (
                ZONE_OB_COLOR
                if annotation.type == "ob"
                else ZONE_PB_COLOR
            )
            draw.rectangle(
                (left_x, top_y, right_x, bottom_y),
            fill=zone_color,
            )

    for index, bar in enumerate(panel.bars):
        center_x = left + first_center + index * cell_width
        body_left = center_x - body_width / 2
        body_right = center_x + body_width / 2
        # Do not draw clipped half-candles at the plot boundary.  The entire
        # candle is omitted when its body cannot fit inside the safe edge.
        if body_left < left or body_right > left + width:
            continue
        high_y = _price_y(bar.h, price_min, price_max, top, height)
        low_y = _price_y(bar.l, price_min, price_max, top, height)
        open_y = _price_y(bar.o, price_min, price_max, top, height)
        close_y = _price_y(bar.c, price_min, price_max, top, height)
        body_fill = UP_FILL if bar.c >= bar.o else DOWN_FILL

        draw.line(
            (center_x, high_y, center_x, low_y),
            fill=OUTLINE,
            width=wick_width,
        )
        body_top = min(open_y, close_y)
        body_bottom = max(open_y, close_y)
        if body_bottom - body_top < 2 * render_scale:
            middle = (body_top + body_bottom) / 2
            half_min_height = render_scale
            body_top = middle - half_min_height
            body_bottom = middle + half_min_height
        draw.rectangle(
            (
                body_left,
                body_top,
                body_right,
                body_bottom,
            ),
            fill=body_fill,
            outline=OUTLINE,
            width=wick_width,
        )

    # Draw labels after both zones and candles so OB/PB stays on the front
    # layer and remains readable when a candle crosses the zone.  When a
    # separate layer is supplied, only the text is rendered at high
    # resolution; candle geometry stays at its original size.
    text_draw = ImageDraw.Draw(text_layer) if text_layer is not None else draw
    coordinate_scale = TEXT_RENDER_SCALE if text_layer is not None else 1.0
    font_scale = TEXT_RENDER_SCALE if text_layer is not None else render_scale
    font = _zone_font(font_scale)
    for annotation in panel.annotations:
        start_index = max(0, min(len(panel.bars) - 1, annotation.start_index))
        end_index = max(0, min(len(panel.bars) - 1, annotation.end_index))
        if end_index < start_index:
            continue

        left_x = left + start_index * cell_width
        right_x = left + (end_index + 1) * cell_width
        top_y = _price_y(
            annotation.price_high,
            price_min,
            price_max,
            top,
            height,
        )
        bottom_y = _price_y(
            annotation.price_low,
            price_min,
            price_max,
            top,
            height,
        )
        label_box = text_draw.textbbox(
            (0, 0),
            annotation.label,
            font=font,
            stroke_width=0,
        )
        label_width = label_box[2] - label_box[0]
        label_height = label_box[3] - label_box[1]
        text_draw.text(
            (
                ((left_x + right_x) * coordinate_scale - label_width) / 2,
                ((top_y + bottom_y) * coordinate_scale - label_height) / 2,
            ),
            annotation.label,
            fill=(
                ZONE_LABEL
                if annotation.type == "ob"
                else ZONE_LABEL_PB
            ),
            font=font,
            stroke_width=0,
        )


def render_kline_image(request: KlineRenderRequest, output_path: Path) -> None:
    """Render all panels vertically without axes or grid lines."""
    canvas_width = CANVAS_WIDTH
    canvas_height = CANVAS_HEIGHT
    render_scale = RENDER_SCALE
    outer_x = 36
    outer_y = 28
    panel_gap = 24
    panel_width = canvas_width - outer_x * 2
    panel_height = (
        canvas_height - outer_y * 2 - panel_gap * (len(request.panels) - 1)
    ) // len(request.panels)

    image = Image.new(
        "RGBA",
        (canvas_width * render_scale, canvas_height * render_scale),
        BACKGROUND + (255,),
    )

    # Only title/body/list text reserves space above the chart.  Labels such
    # as OB and PB belong inside generated zones and must not shrink the chart.
    text_boxes = [
        NormalizedBox(x=item.x, y=item.y, width=item.width, height=item.height)
        for item in request.text_overlays
        if item.role != "label"
    ]
    fallback_boxes = [
        NormalizedBox(
            x=outer_x / canvas_width,
            y=(outer_y + index * (panel_height + panel_gap)) / canvas_height,
            width=panel_width / canvas_width,
            height=panel_height / canvas_height,
        )
        for index in range(len(request.panels))
    ]

    panel_boxes = [
        _panel_box(panel, fallback_boxes[index], text_boxes)
        for index, panel in enumerate(request.panels)
    ]

    for panel, box in zip(request.panels, panel_boxes):
        left = round(box.x * canvas_width * render_scale)
        top = round(box.y * canvas_height * render_scale)
        width = round(box.width * canvas_width * render_scale)
        height = round(box.height * canvas_height * render_scale)
        image = _draw_zone_layers(
            image,
            panel,
            left,
            top,
            width,
            height,
        )

    draw = ImageDraw.Draw(image)
    for panel, box in zip(request.panels, panel_boxes):
        left = round(box.x * canvas_width * render_scale)
        top = round(box.y * canvas_height * render_scale)
        width = round(box.width * canvas_width * render_scale)
        height = round(box.height * canvas_height * render_scale)
        _draw_panel(
            draw,
            panel,
            left,
            top,
            width,
            height,
            draw_zones=False,
            render_scale=render_scale,
        )

    _draw_text_overlays(image, request.text_overlays, render_scale)

    image = image.resize(
        (canvas_width, canvas_height),
        Image.Resampling.LANCZOS,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Flatten the RGBA drawing onto the white canvas so the returned PNG stays
    # RGB while preserving the translucent appearance of both zone colors.
    flattened = Image.alpha_composite(
        Image.new("RGBA", image.size, BACKGROUND + (255,)),
        image,
    ).convert("RGB")
    flattened.save(output_path, format="PNG", optimize=True)


def build_kline_router(media_dir: Path, public_base_url: str) -> APIRouter:
    router = APIRouter(prefix="/v1/kline", tags=["kline"])

    @router.post("/render", response_model=KlineRenderResponse)
    def render_kline(request: KlineRenderRequest) -> KlineRenderResponse:
        file_name = f"kline-{uuid.uuid4().hex}.png"
        output_path = media_dir / file_name
        render_kline_image(request, output_path)
        return KlineRenderResponse(
            image_url=f"{public_base_url}/media/{file_name}",
            panel_count=len(request.panels),
            bar_count=sum(len(panel.bars) for panel in request.panels),
        )

    return router
