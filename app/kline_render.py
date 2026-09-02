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


class KlineRenderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["generated-kline-v1"]
    panels: list[KlinePanel] = Field(min_length=1, max_length=4)


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
TEXT_RENDER_SCALE = 4


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
    return max(2, min(12, int(cell_width * 0.70)))


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
) -> None:
    price_min, price_max = _panel_price_bounds(panel)

    cell_width = width / len(panel.bars)
    body_width = _body_width(cell_width)
    wick_width = 1

    if draw_zones:
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
        center_x = left + (index + 0.5) * cell_width
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
        if body_bottom - body_top < 2:
            middle = (body_top + body_bottom) / 2
            body_top = middle - 1
            body_bottom = middle + 1
        draw.rectangle(
            (
                center_x - body_width / 2,
                body_top,
                center_x + body_width / 2,
                body_bottom,
            ),
            fill=body_fill,
            outline=OUTLINE,
            width=1,
        )

    # Draw labels after both zones and candles so OB/PB stays on the front
    # layer and remains readable when a candle crosses the zone.  When a
    # separate layer is supplied, only the text is rendered at high
    # resolution; candle geometry stays at its original size.
    text_draw = ImageDraw.Draw(text_layer) if text_layer is not None else draw
    coordinate_scale = TEXT_RENDER_SCALE if text_layer is not None else 1.0
    font = _zone_font(coordinate_scale)
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
    canvas_width = 1080
    canvas_height = 720
    outer_x = 36
    outer_y = 28
    panel_gap = 24
    panel_width = canvas_width - outer_x * 2
    panel_height = (
        canvas_height - outer_y * 2 - panel_gap * (len(request.panels) - 1)
    ) // len(request.panels)

    image = Image.new(
        "RGBA",
        (canvas_width, canvas_height),
        BACKGROUND + (255,),
    )

    for index, panel in enumerate(request.panels):
        panel_top = outer_y + index * (panel_height + panel_gap)
        image = _draw_zone_layers(
            image,
            panel,
            outer_x,
            panel_top,
            panel_width,
            panel_height,
        )

    draw = ImageDraw.Draw(image)
    text_layer = Image.new(
        "RGBA",
        (canvas_width * TEXT_RENDER_SCALE, canvas_height * TEXT_RENDER_SCALE),
        (0, 0, 0, 0),
    )
    for index, panel in enumerate(request.panels):
        panel_top = outer_y + index * (panel_height + panel_gap)
        _draw_panel(
            draw,
            panel,
            outer_x,
            panel_top,
            panel_width,
            panel_height,
            draw_zones=False,
            text_layer=text_layer,
        )

    text_layer = text_layer.resize(
        (canvas_width, canvas_height),
        Image.Resampling.LANCZOS,
    )
    image = Image.alpha_composite(image, text_layer)

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
