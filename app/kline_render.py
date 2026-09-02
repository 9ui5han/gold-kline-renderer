"""Render the generated-kline-v1 contract as a clean candlestick image."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field


class KlineBar(BaseModel):
    model_config = ConfigDict(extra="ignore")

    t: int | float | str
    o: float
    h: float
    l: float
    c: float


class KlinePanel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    panel_id: str = Field(min_length=1, max_length=64)
    visual_type: Literal["candlestick", "price_path", "mixed"]
    bars: list[KlineBar] = Field(min_length=20, max_length=300)


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


def _price_y(price: float, price_min: float, price_max: float, top: int, height: int) -> float:
    span = max(price_max - price_min, 1e-9)
    return top + (price_max - price) / span * height


def _body_width(cell_width: float) -> int:
    return max(2, min(12, int(cell_width * 0.70)))


def _draw_panel(
    draw: ImageDraw.ImageDraw,
    panel: KlinePanel,
    left: int,
    top: int,
    width: int,
    height: int,
) -> None:
    values = [value for bar in panel.bars for value in (bar.h, bar.l)]
    price_min = min(values)
    price_max = max(values)
    span = max(price_max - price_min, 1e-9)
    padding = span * 0.06
    price_min -= padding
    price_max += padding

    cell_width = width / len(panel.bars)
    body_width = _body_width(cell_width)
    wick_width = 1

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


def render_kline_image(request: KlineRenderRequest, output_path: Path) -> None:
    """Render all panels vertically without axes, grid, labels, or annotations."""
    canvas_width = 1080
    canvas_height = 720
    outer_x = 36
    outer_y = 28
    panel_gap = 24
    panel_width = canvas_width - outer_x * 2
    panel_height = (
        canvas_height - outer_y * 2 - panel_gap * (len(request.panels) - 1)
    ) // len(request.panels)

    image = Image.new("RGB", (canvas_width, canvas_height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    for index, panel in enumerate(request.panels):
        panel_top = outer_y + index * (panel_height + panel_gap)
        _draw_panel(draw, panel, outer_x, panel_top, panel_width, panel_height)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, format="PNG", optimize=True)


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
