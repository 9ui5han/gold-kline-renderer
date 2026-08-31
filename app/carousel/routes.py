"""Isolated educational-chart endpoint; legacy photo routes stay unchanged."""
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException

from app.photo.market_chart_renderer import render_market_chart, validate_market_request
from app.photo.models import PhotoChartRequest


class CarouselChartRequest(PhotoChartRequest):
    content_type: Literal["educational_reconstruction"]


def build_carousel_router(data_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/v1/carousel", tags=["carousel"])
    root = Path(data_dir) / "carousel-work"

    @router.post("/charts/render")
    def render_charts(payload: CarouselChartRequest) -> dict:
        try:
            pages = validate_market_request(
                [page.model_dump() for page in payload.pages],
                payload.route_payload,
            )
            root.mkdir(parents=True, exist_ok=True)
            destination = root / f"carousel-{uuid.uuid4().hex}"
            # Publish a complete batch only. Invalid later pages leave no partial assets.
            with tempfile.TemporaryDirectory(prefix=".pending-", dir=root) as temporary:
                assets = []
                for page in pages:
                    output = Path(temporary) / f"chart_{int(page['page_no']):02d}.png"
                    assets.append(render_market_chart(
                        page, output, payload.route_payload, language=payload.language,
                    ))
                Path(temporary).rename(destination)
            for asset in assets:
                asset["asset_path"] = str(destination / Path(asset["asset_path"]).name)
            return {"schema_version": "photo-chart-v1", "assets": assets}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
