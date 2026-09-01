import uuid
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException
from PIL import Image

from .asset_registry import AssetRegistry
from .chart_renderer import render_chart
from .market_chart_renderer import render_market_chart, validate_market_request
from .models import (
    PhotoAssetRequest,
    PhotoChartRequest,
    PhotoQaRequest,
    PhotoRenderRequest,
    PhotoRepairRequest,
)
from .page_renderer import render_page
from .store import PhotoStore
from .validator import validate_post


def materialize_template_assets(visual_assets: dict, job_dir: Path) -> dict:
    source = visual_assets.get("assets") if isinstance(visual_assets, dict) else None
    if not isinstance(source, list):
        raise ValueError("VISUAL_ASSETS_INVALID")
    output = []
    template_dir = job_dir / "templates"
    for raw in source:
        if not isinstance(raw, dict):
            raise ValueError("VISUAL_ASSET_INVALID")
        item = dict(raw)
        url = str(item.get("asset_url") or "").strip()
        if item.get("purpose") != "page_template" or not url:
            output.append(item)
            continue
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "file.302.ai":
            raise ValueError("TEMPLATE_HOST_INVALID")
        try:
            response = httpx.get(url, follow_redirects=True, timeout=20.0)
        except httpx.HTTPError as exc:
            raise ValueError("TEMPLATE_DOWNLOAD_FAILED") from exc
        if response.status_code != 200 or not str(response.headers.get("content-type") or "").lower().startswith("image/"):
            raise ValueError("TEMPLATE_DOWNLOAD_FAILED")
        if not response.content or len(response.content) > 12 * 1024 * 1024:
            raise ValueError("TEMPLATE_FILE_INVALID")
        try:
            with Image.open(BytesIO(response.content)) as candidate:
                candidate.verify()
        except (OSError, ValueError) as exc:
            raise ValueError("TEMPLATE_FILE_INVALID") from exc
        template_dir.mkdir(parents=True, exist_ok=True)
        target = template_dir / f"page_{int(item.get('page_no') or 0):02d}.png"
        with Image.open(BytesIO(response.content)) as candidate:
            candidate.convert("RGB").save(target, "PNG")
        item.pop("asset_url", None)
        item["asset_path"] = str(target)
        output.append(item)
    return {"schema_version": "photo-assets-v1", "assets": output}


def build_photo_router(
    data_dir: Path,
    asset_root: Path,
    public_base_url: str,
) -> APIRouter:
    router = APIRouter(prefix="/v1/photo", tags=["photo"])
    store = PhotoStore(data_dir)
    registry = AssetRegistry(asset_root)
    base_url = public_base_url.rstrip("/")

    def page_visuals(page: dict, supplied: list[dict]) -> list[dict]:
        result = [dict(item) for item in supplied]
        role = str(page.get("page_role") or "")
        visual_type = str(page.get("visual_type") or "")
        has_undraw = any(item.get("source") == "undraw" for item in result)
        if (role == "cover" or visual_type == "cover_illustration") and not has_undraw:
            topic = " ".join(str(page.get(key) or "") for key in (
                "title", "body", "key_message", "visual_focus",
            ))
            selected = registry.select_cover_asset(topic)
            selected["page_no"] = int(page.get("page_no") or 0)
            result.append(selected)
        return result

    @router.post("/charts/render")
    def charts_render(payload: PhotoChartRequest) -> dict:
        if payload.content_type not in {"knowledge", "market", "educational_reconstruction"}:
            raise HTTPException(
                status_code=422,
                detail="PHOTO_CHART_CONTENT_TYPE_UNSUPPORTED",
            )
        photo_job_id = f"photo-{uuid.uuid4().hex[:16]}"
        chart_dir = store.job_dir(photo_job_id) / "charts"
        assets = []
        if payload.content_type == "market":
            raise HTTPException(
                status_code=422,
                detail="PHOTO_MARKET_CHART_NOT_IMPLEMENTED",
            )
        if payload.content_type == "educational_reconstruction":
            if payload.route_payload.get("schema_version") != "carousel-route-v2":
                raise HTTPException(
                    status_code=422,
                    detail="EDUCATIONAL_ROUTE_VERSION_INVALID",
                )
            try:
                analysis_pages = validate_market_request(
                    [page.model_dump() for page in payload.pages],
                    payload.route_payload,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            pages_to_render = analysis_pages
            renderer = render_market_chart
        else:
            pages_to_render = [page.model_dump() for page in payload.pages]
            renderer = render_chart
        for page in pages_to_render:
            output = chart_dir / f"chart_{int(page['page_no']):02d}.png"
            try:
                assets.append(
                    renderer(
                        page,
                        output,
                        payload.route_payload,
                        language=payload.language,
                    )
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        fingerprints: dict[str, int] = {}
        for asset in assets:
            value = str(asset.get("data_fingerprint") or "")
            if payload.content_type == "knowledge" and value in fingerprints:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "DUPLICATE_TEACHING_CHART:"
                        f"page_{fingerprints[value]}:page_{asset['page_no']}"
                    ),
                )
            fingerprints[value] = int(asset["page_no"])
        return {"schema_version": "photo-chart-v1", "assets": assets}

    @router.post("/assets/resolve")
    def assets_resolve(payload: PhotoAssetRequest) -> dict:
        assets = []
        for request in payload.requests:
            try:
                item = registry.resolve(request.asset_key, request.asset_type)
            except (KeyError, ValueError) as exc:
                if request.required:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                continue
            item["page_no"] = request.page_no
            assets.append(item)
        return {"schema_version": "photo-assets-v1", "assets": assets}

    @router.post("/render-post")
    def render_post(payload: PhotoRenderRequest) -> dict:
        photo_job_id = payload.photo_request_id
        job_dir = store.job_dir(photo_job_id)
        store.save_context(photo_job_id, payload.model_dump(mode="json"))
        chart_by_page = {
            item.get("page_no"): item
            for item in payload.chart_assets.get("assets", [])
            if isinstance(item, dict)
        }
        try:
            local_visual_assets = materialize_template_assets(payload.visual_assets, job_dir)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        visual_by_page: dict[int, list[dict]] = {}
        for item in local_visual_assets.get("assets", []):
            if isinstance(item, dict):
                visual_by_page.setdefault(int(item.get("page_no") or 0), []).append(item)
        images = []
        for page in payload.photo_plan.get("pages", []):
            page_no = int(page.get("page_no") or 0)
            output = job_dir / f"page_{page_no:02d}.png"
            try:
                item = render_page(
                    page,
                    chart_by_page.get(page_no),
                    page_visuals(page, visual_by_page.get(page_no, [])),
                    output,
                    payload.canvas.width,
                    payload.canvas.height,
                    language=payload.language,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            item["url"] = f"{base_url}/photo-media/{photo_job_id}/{output.name}"
            images.append(item)
        result = {
            "schema_version": "photo-render-v1",
            "status": "completed",
            "photo_job_id": photo_job_id,
            "images": images,
            "error": "",
        }
        store.save_job(photo_job_id, result)
        return result

    @router.post("/validate")
    def validate(payload: PhotoQaRequest) -> dict:
        return validate_post(
            payload.photo_plan,
            payload.render_result,
            expected_language=payload.language,
        )

    @router.post("/repair")
    def repair(payload: PhotoRepairRequest) -> dict:
        if payload.repair_count != 1:
            raise HTTPException(status_code=422, detail="PHOTO_REPAIR_COUNT_INVALID")
        protected_codes = (
            "FACT_",
            "PRICE_",
            "TIME_",
            "SYMBOL_",
            "INDICATOR_VALUE_",
            "TEACHING_",
            "MARKET_DIRECTION_",
            "FORECAST_CONDITION_",
            "INVALIDATION_",
        )
        for error in payload.errors:
            if str(error.get("code") or "").startswith(protected_codes):
                raise HTTPException(status_code=422, detail="PHOTO_FACT_REBUILD_REQUIRED")
        result = dict(payload.render_result)
        try:
            context = store.get_context(str(result.get("photo_job_id") or ""))
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        chart_by_page = {
            int(item.get("page_no") or 0): item
            for item in context.get("chart_assets", {}).get("assets", [])
            if isinstance(item, dict)
        }
        visual_by_page: dict[int, list[dict]] = {}
        for item in context.get("visual_assets", {}).get("assets", []):
            if isinstance(item, dict):
                visual_by_page.setdefault(int(item.get("page_no") or 0), []).append(item)
        images = [dict(item) for item in result.get("images", [])]
        image_by_page = {int(item.get("page_no") or 0): item for item in images}
        for page in payload.photo_plan.get("pages", []):
            page_no = int(page.get("page_no") or 0)
            if page_no not in payload.bad_pages:
                continue
            existing = image_by_page.get(page_no)
            if not existing:
                continue
            output = Path(existing["path"])
            try:
                repaired = render_page(
                    page,
                    chart_by_page.get(page_no),
                    page_visuals(page, visual_by_page.get(page_no, [])),
                    output,
                    int(existing["width"]),
                    int(existing["height"]),
                    compact=True,
                    language=str(context.get("language") or "zh-CN"),
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            repaired["url"] = existing.get("url", "")
            image_by_page[page_no] = repaired
        result["images"] = [image_by_page[key] for key in sorted(image_by_page)]
        result["status"] = "completed"
        result["repair_count"] = 1
        store.save_job(str(result["photo_job_id"]), result)
        return result

    @router.get("/jobs/{photo_job_id}")
    def get_job(photo_job_id: str) -> dict:
        try:
            return store.get_job(photo_job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="PHOTO_JOB_NOT_FOUND") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    return router
