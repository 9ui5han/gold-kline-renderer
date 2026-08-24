from __future__ import annotations

import json
import hashlib
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.job_store import IdempotencyConflict, JobStore


DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/gold-video"))
MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
STORE = JobStore(DATA_DIR / "segment_jobs")
router = APIRouter(tags=["segment-render"])
SEGMENT_RENDER_AWAIT_TIMEOUT_SEC = max(
    1.0,
    min(240.0, float(os.environ.get("SEGMENT_RENDER_AWAIT_TIMEOUT_SEC", "180"))),
)
SEGMENT_RENDER_AWAIT_POLL_INTERVAL_SEC = max(
    0.1,
    min(5.0, float(os.environ.get("SEGMENT_RENDER_AWAIT_POLL_INTERVAL_SEC", "1"))),
)


class VideoSpec(BaseModel):
    width: int = Field(default=1080, ge=320, le=3840)
    height: int = Field(default=1920, ge=320, le=3840)
    fps: int = Field(default=30, ge=24, le=60)
    format: str = "mp4"


class SegmentRenderRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)
    master_request_id: str = Field(min_length=1, max_length=100)
    segment_id: str = Field(min_length=1, max_length=60)
    order: int = Field(ge=1)
    symbol: str = Field(min_length=1, max_length=30)
    timeframe: str = Field(min_length=1, max_length=20)
    data_as_of: str = Field(min_length=1, max_length=50)
    historical_candles: list[dict[str, Any]] = Field(min_length=20, max_length=500)
    audio_url: str = Field(min_length=8)
    base_duration_sec: float = Field(gt=0, le=300)
    head_handle_sec: float = Field(ge=0, le=3)
    tail_handle_sec: float = Field(ge=0, le=3)
    render_duration_sec: float = Field(gt=0, le=306)
    visual_timeline: dict[str, Any]
    video: VideoSpec
    fallback_policy: dict[str, Any]


class Tool09SegmentRequest(BaseModel):
    """Compact request emitted by TOOL-09's per-segment Dify iteration."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^tool09-segment-request-v1$")
    master_request_id: str = Field(min_length=1, max_length=100)
    market_input: dict[str, Any]
    segment_item: dict[str, Any]

    @field_validator("master_request_id")
    @classmethod
    def validate_master_request_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("MASTER_REQUEST_ID_EMPTY")
        return normalized


class Tool09FinalizeRequest(BaseModel):
    """Collection request emitted after all TOOL-09 iterations finish."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^tool09-collection-request-v1$")
    master_request_id: str = Field(min_length=1, max_length=100)
    rendered_segments: list[Any]
    market_input: dict[str, Any]
    segment_media: dict[str, Any]

    @field_validator("master_request_id")
    @classmethod
    def validate_master_request_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("MASTER_REQUEST_ID_EMPTY")
        return normalized


def _dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _tool09_request_id(master_request_id: str, segment_id: str) -> str:
    safe_master = re.sub(r"[^A-Za-z0-9_-]+", "-", master_request_id).strip("-") or "tool09"
    safe_segment = re.sub(r"[^A-Za-z0-9_-]+", "-", segment_id).strip("-") or "segment"
    digest = hashlib.sha256(f"{master_request_id}|{segment_id}".encode("utf-8")).hexdigest()[:16]
    suffix = f"{digest}-{safe_segment}-visual-r0"
    return f"{safe_master[: max(1, 120 - len(suffix) - 1)]}-{suffix}"


def _tool09_candles(market_input: dict[str, Any], timeframe: str) -> list[dict[str, Any]]:
    normalized = market_input.get("normalized_market")
    if not isinstance(normalized, dict):
        normalized = {}
    timeframes = normalized.get("timeframes")
    if not isinstance(timeframes, dict):
        timeframes = {}
    frame = timeframes.get(timeframe)
    if not isinstance(frame, dict):
        frame = {}
    bars = frame.get("closed_bars") or frame.get("bars") or []
    if not isinstance(bars, list) or len(bars) < 20:
        raise HTTPException(status_code=422, detail={"code": "RENDER_CANDLES_LT_20"})
    candles: list[dict[str, Any]] = []
    for bar in bars[-200:]:
        if not isinstance(bar, dict):
            raise HTTPException(status_code=422, detail={"code": "RENDER_CANDLE_NOT_OBJECT"})
        try:
            candles.append({
                "time": str(bar["time"]),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "volume": float(bar.get("volume") or 0.0),
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={"code": "RENDER_CANDLE_INVALID"}) from exc
    return candles


def _tool09_render_request(payload: Tool09SegmentRequest) -> SegmentRenderRequest:
    market = payload.market_input
    if market.get("schema_version") != "market-input-contract-v1":
        raise HTTPException(status_code=422, detail={"code": "MARKET_INPUT_VERSION_INVALID"})
    item = payload.segment_item
    segment_id = str(item.get("segment_id") or "").strip()
    if not segment_id:
        raise HTTPException(status_code=422, detail={"code": "SEGMENT_ID_REQUIRED"})
    audio = item.get("audio") if isinstance(item.get("audio"), dict) else {}
    audio_url = str(audio.get("url") or "").strip()
    base_duration = float(audio.get("duration_sec") or 0.0)
    duration_validation = item.get("duration_validation")
    if not isinstance(duration_validation, dict) or duration_validation.get("valid") is not True:
        raise HTTPException(status_code=422, detail={"code": "AUDIO_DURATION_NOT_VALID"})
    if base_duration <= 0:
        raise HTTPException(status_code=422, detail={"code": "AUDIO_DURATION_INVALID"})

    normalized = market.get("normalized_market")
    if not isinstance(normalized, dict):
        normalized = {}
    job_config = market.get("job_config")
    if not isinstance(job_config, dict):
        job_config = {}
    forecast = job_config.get("forecast")
    if not isinstance(forecast, dict):
        forecast = {}
    visual = item.get("visual") if isinstance(item.get("visual"), dict) else {}
    timeframe = str(visual.get("source_timeframe") or forecast.get("timeframe") or "1h")
    video = job_config.get("video") if isinstance(job_config.get("video"), dict) else {}
    fps = int(video.get("fps") or 30)
    transition = item.get("transition_out") if isinstance(item.get("transition_out"), dict) else {}
    tail_handle = max(0.0, min(3.0, float(transition.get("duration_ms") or 0) / 1000.0))
    timeline = {
        "schema_version": "visual-timeline-v1",
        "segment_id": segment_id,
        "base_duration_sec": base_duration,
        "fps": fps,
        "scenes": [{
            "scene_id": "scene_01",
            "start_sec": 0.0,
            "end_sec": base_duration,
            "duration_sec": base_duration,
            "template_id": "chart_push",
        }],
        "camera_plan": [],
        "overlay_plan": [],
    }
    data_as_of = str(market.get("data_as_of") or normalized.get("data_as_of") or "").strip()
    request_data = {
        "request_id": _tool09_request_id(payload.master_request_id, segment_id),
        "master_request_id": payload.master_request_id,
        "segment_id": segment_id,
        "order": int(item.get("order") or 1),
        "symbol": str(normalized.get("symbol") or "XAUUSD"),
        "timeframe": timeframe,
        "data_as_of": data_as_of,
        "historical_candles": _tool09_candles(market, timeframe),
        "audio_url": audio_url,
        "base_duration_sec": base_duration,
        "head_handle_sec": 0.0,
        "tail_handle_sec": tail_handle,
        "render_duration_sec": base_duration + tail_handle,
        "visual_timeline": timeline,
        "video": {
            "width": int(video.get("width") or 1080),
            "height": int(video.get("height") or 1920),
            "fps": fps,
            "format": "mp4",
        },
        "fallback_policy": {
            "supported_effect": "static_hold",
            "unsupported_effect": "degrade_to_static_hold",
            "retry_current_segment": 1,
        },
    }
    return SegmentRenderRequest.model_validate(request_data)


def _tool09_failed_segment(payload: Tool09SegmentRequest, code: str, message: str) -> dict[str, Any]:
    return {
        "master_request_id": payload.master_request_id,
        "segment_id": str(payload.segment_item.get("segment_id") or ""),
        "order": int(payload.segment_item.get("order") or 0),
        "status": "failed",
        "render_error": {"code": code, "message": message, "retryable": code == "RENDER_WAIT_TIMEOUT"},
        "video_url": "",
        "base_duration_sec": 0.0,
        "head_handle_sec": 0.0,
        "tail_handle_sec": 0.0,
        "actual_render_duration_sec": 0.0,
        "probe_valid": False,
        "kline_main_visual_present": False,
        "degraded": False,
        "degradation_code": "",
        "degradation_records": [],
        "transition_out": {"type": "hard_cut", "duration_ms": 0},
    }


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload["video"]["format"] != "mp4":
        raise HTTPException(status_code=422, detail={"code": "FORMAT_MUST_BE_MP4"})
    expected = (
        float(payload["base_duration_sec"])
        + float(payload["head_handle_sec"])
        + float(payload["tail_handle_sec"])
    )
    if abs(expected - float(payload["render_duration_sec"])) > 0.002:
        raise HTTPException(status_code=422, detail={"code": "RENDER_DURATION_MISMATCH"})
    try:
        datetime.fromisoformat(str(payload["data_as_of"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "DATA_AS_OF_INVALID"}) from exc
    if not str(payload["audio_url"]).startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail={"code": "AUDIO_URL_INVALID"})
    for index, candle in enumerate(payload["historical_candles"]):
        try:
            open_price = float(candle["open"])
            high_price = float(candle["high"])
            low_price = float(candle["low"])
            close_price = float(candle["close"])
            candle_time = str(candle["time"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={"code": "CANDLE_INVALID", "index": index}) from exc
        try:
            datetime.fromisoformat(candle_time.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "CANDLE_TIME_INVALID", "index": index}) from exc
        if low_price > min(open_price, close_price) or high_price < max(open_price, close_price) or low_price > high_price:
            raise HTTPException(status_code=422, detail={"code": "CANDLE_OHLC_INVALID", "index": index})
    timeline = payload.get("visual_timeline") or {}
    if timeline.get("segment_id") != payload["segment_id"]:
        raise HTTPException(status_code=422, detail={"code": "TIMELINE_SEGMENT_MISMATCH"})
    scenes = timeline.get("scenes") or []
    if not scenes:
        raise HTTPException(status_code=422, detail={"code": "TIMELINE_SCENES_EMPTY"})
    if abs(float(timeline.get("base_duration_sec", -1)) - float(payload["base_duration_sec"])) > 0.002:
        raise HTTPException(status_code=422, detail={"code": "TIMELINE_BASE_DURATION_MISMATCH"})
    if int(timeline.get("fps", 0)) != int(payload["video"]["fps"]):
        raise HTTPException(status_code=422, detail={"code": "TIMELINE_FPS_MISMATCH"})
    previous_end = 0.0
    for index, scene in enumerate(scenes):
        try:
            start = float(scene["start_sec"])
            end = float(scene["end_sec"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={"code": "SCENE_TIME_INVALID", "index": index}) from exc
        if start < 0 or end <= start or end > float(payload["base_duration_sec"]) + 0.002:
            raise HTTPException(status_code=422, detail={"code": "SCENE_BOUNDS_INVALID", "index": index})
        if abs(start - previous_end) > 0.002:
            raise HTTPException(status_code=422, detail={"code": "SCENE_NOT_CONTIGUOUS", "index": index})
        previous_end = end
    if abs(previous_end - float(payload["base_duration_sec"])) > 0.002:
        raise HTTPException(status_code=422, detail={"code": "SCENES_DO_NOT_COVER_BASE"})
    for plan_name in ("camera_plan", "overlay_plan"):
        for index, event in enumerate(timeline.get(plan_name) or []):
            try:
                start = float(event["start_sec"])
                end = float(event["end_sec"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail={"code": "VISUAL_EVENT_TIME_INVALID", "plan": plan_name, "index": index}) from exc
            if start < 0 or end <= start or end > float(payload["base_duration_sec"]) + 0.002:
                raise HTTPException(status_code=422, detail={"code": "VISUAL_EVENT_BOUNDS_INVALID", "plan": plan_name, "index": index})
    if not isinstance(payload.get("fallback_policy"), dict) or not payload["fallback_policy"]:
        raise HTTPException(status_code=422, detail={"code": "FALLBACK_POLICY_EMPTY"})


def _public(job: dict[str, Any]) -> dict[str, Any]:
    base = {
        "job_id": job["job_id"],
        "request_id": job["request_id"],
        "status": job["status"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }
    payload = job.get("payload") or {}
    base["segment_id"] = payload.get("segment_id", "")
    base["order"] = payload.get("order", 0)
    if job.get("result"):
        base.update(job["result"])
    base["error"] = job.get("error")
    return base


def _download_audio(url: str, destination: Path) -> None:
    if not url.startswith(("http://", "https://")):
        raise ValueError("AUDIO_URL_INVALID")
    request = urllib.request.Request(url, headers={"User-Agent": "GoldSegmentRenderer/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > 50 * 1024 * 1024:
                raise ValueError("AUDIO_TOO_LARGE")
            output.write(chunk)


def _line(pixels: bytearray, width: int, height: int, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        if 0 <= x0 < width and 0 <= y0 < height:
            offset = (y0 * width + x0) * 3
            pixels[offset:offset + 3] = bytes(color)
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


def _rect(pixels: bytearray, width: int, height: int, left: int, top: int, right: int, bottom: int, color: tuple[int, int, int]) -> None:
    for y in range(max(0, top), min(height, bottom + 1)):
        for x in range(max(0, left), min(width, right + 1)):
            offset = (y * width + x) * 3
            pixels[offset:offset + 3] = bytes(color)


def _write_kline_ppm(candles: list[dict[str, Any]], width: int, height: int, destination: Path) -> None:
    background = (8, 13, 24)
    pixels = bytearray(background * (width * height))
    margin_x = max(40, width // 18)
    top = max(80, height // 12)
    bottom = height - max(120, height // 9)
    chart_width = width - margin_x * 2
    chart_height = bottom - top
    for step in range(6):
        y = top + int(chart_height * step / 5)
        _line(pixels, width, height, margin_x, y, width - margin_x, y, (28, 39, 58))
    highs = [float(x["high"]) for x in candles]
    lows = [float(x["low"]) for x in candles]
    high = max(highs)
    low = min(lows)
    span = max(high - low, 1e-9)
    visible = candles[-min(len(candles), 80):]
    slot = chart_width / max(len(visible), 1)
    body_width = max(2, int(slot * 0.55))

    def y_for(price: float) -> int:
        return top + int((high - price) / span * chart_height)

    for index, candle in enumerate(visible):
        x = margin_x + int((index + 0.5) * slot)
        open_y = y_for(float(candle["open"]))
        close_y = y_for(float(candle["close"]))
        high_y = y_for(float(candle["high"]))
        low_y = y_for(float(candle["low"]))
        color = (35, 211, 156) if float(candle["close"]) >= float(candle["open"]) else (245, 92, 92)
        _line(pixels, width, height, x, high_y, x, low_y, color)
        _rect(pixels, width, height, x - body_width // 2, min(open_y, close_y), x + body_width // 2, max(open_y, close_y), color)
    with destination.open("wb") as output:
        output.write(f"P6\n{width} {height}\n255\n".encode("ascii"))
        output.write(pixels)


def _run(command: list[str], code: str) -> str:
    completed = subprocess.run(command, capture_output=True, text=True, timeout=360)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or code)[-2000:]
        raise RuntimeError(f"{code}:{message}")
    return completed.stdout


def _probe(path: Path, expected_duration: float, fps: int, width: int, height: int) -> dict[str, Any]:
    raw = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ], "FFPROBE_FAILED")
    data = json.loads(raw)
    streams = data.get("streams") or []
    video = next((x for x in streams if x.get("codec_type") == "video"), None)
    audio = next((x for x in streams if x.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    rate_text = str((video or {}).get("avg_frame_rate") or "0/1")
    numerator, denominator = (rate_text.split("/", 1) + ["1"])[:2]
    actual_fps = float(numerator) / max(float(denominator), 1.0)
    valid = bool(
        video and audio
        and int(video.get("width") or 0) == width
        and int(video.get("height") or 0) == height
        and str(video.get("codec_name") or "") == "h264"
        and str(video.get("pix_fmt") or "") == "yuv420p"
        and abs(actual_fps - fps) <= 0.01
        and str(audio.get("codec_name") or "") == "aac"
        and int(audio.get("sample_rate") or 0) == 48000
        and int(audio.get("channels") or 0) == 2
        and abs(duration - expected_duration) <= (1.0 / fps + 0.001)
    )
    return {
        "duration_sec": duration,
        "width": int((video or {}).get("width") or 0),
        "height": int((video or {}).get("height") or 0),
        "fps": actual_fps,
        "video_codec": str((video or {}).get("codec_name") or ""),
        "pixel_format": str((video or {}).get("pix_fmt") or ""),
        "audio_codec": str((audio or {}).get("codec_name") or ""),
        "audio_sample_rate": int((audio or {}).get("sample_rate") or 0),
        "audio_channels": int((audio or {}).get("channels") or 0),
        "probe_valid": valid,
    }


def _effect_degradations(payload: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    timeline = payload.get("visual_timeline") or {}
    records: list[dict[str, str]] = []
    requested_effects: list[str] = []
    for event in timeline.get("camera_plan") or []:
        effect = str(event.get("motion") or "static_hold")
        requested_effects.append(effect)
        if effect != "static_hold":
            records.append({
                "code": "UNSUPPORTED_CAMERA_STATIC_HOLD",
                "message": f"camera event {event.get('event_id') or ''} is planned but not implemented",
                "requested_effect": effect,
                "applied_effect": "static_hold",
            })
    for event in timeline.get("overlay_plan") or []:
        effect = str(event.get("event_type") or "overlay")
        requested_effects.append(effect)
        if effect != "static_hold":
            records.append({
                "code": "UNSUPPORTED_OVERLAY_HIDDEN",
                "message": f"overlay event {event.get('event_id') or ''} is planned but not implemented",
                "requested_effect": effect,
                "applied_effect": "hidden",
            })
    requested = ",".join(requested_effects) if requested_effects else "static_hold"
    return requested, records


def _render(job_id: str) -> None:
    work = Path(tempfile.mkdtemp(prefix=f"{job_id}-"))
    try:
        job = STORE.update(job_id, status="rendering", error=None)
        payload = job["payload"]
        audio_path = work / "audio.bin"
        frame_path = work / "frame.ppm"
        output_path = MEDIA_DIR / f"{job_id}.mp4"
        _download_audio(str(payload["audio_url"]), audio_path)
        video = payload["video"]
        _write_kline_ppm(payload["historical_candles"], int(video["width"]), int(video["height"]), frame_path)
        head_ms = int(round(float(payload["head_handle_sec"]) * 1000))
        audio_filter = f"adelay={head_ms}|{head_ms},apad"
        _run([
            "ffmpeg", "-y", "-loop", "1", "-framerate", str(video["fps"]),
            "-i", str(frame_path), "-i", str(audio_path),
            "-map", "0:v:0", "-map", "1:a:0", "-af", audio_filter,
            "-t", f"{float(payload['render_duration_sec']):.6f}",
            "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-r", str(video["fps"]), "-c:a", "aac", "-b:a", "192k",
            "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(output_path),
        ], "FFMPEG_RENDER_FAILED")
        probe = _probe(
            output_path,
            float(payload["render_duration_sec"]),
            int(video["fps"]),
            int(video["width"]),
            int(video["height"]),
        )
        if not probe["probe_valid"]:
            raise RuntimeError("FFPROBE_CONTRACT_FAILED")
        requested, records = _effect_degradations(payload)
        degraded = bool(records)
        result = {
            "video_url": f"{PUBLIC_BASE_URL}/media/{output_path.name}",
            "thumbnail_url": "",
            "base_duration_sec": float(payload["base_duration_sec"]),
            "head_handle_sec": float(payload["head_handle_sec"]),
            "tail_handle_sec": float(payload["tail_handle_sec"]),
            "render_duration_sec": float(payload["render_duration_sec"]),
            **probe,
            "kline_main_visual_present": True,
            "degraded": degraded,
            "degradation_code": records[0]["code"] if degraded else "",
            "requested_effect": requested,
            "applied_effect": "static_hold",
            "degradation_records": records,
        }
        STORE.update(job_id, status="completed", result=result, error=None)
    except Exception as exc:
        STORE.update(job_id, status="failed", result=None, error={
            "code": "SEGMENT_RENDER_FAILED",
            "message": str(exc)[:2000],
            "retryable": True,
        })
    finally:
        shutil.rmtree(work, ignore_errors=True)


@router.post("/v1/segment-render-jobs")
def _create_or_reuse_segment_render_job(request: SegmentRenderRequest) -> tuple[dict[str, Any], bool]:
    payload = _dump(request)
    _validate_payload(payload)
    try:
        job, created = STORE.create_or_get(request.request_id, payload)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail={
            "code": "IDEMPOTENCY_CONFLICT",
            "message": f"request_id already exists with different payload: {exc}",
        }) from exc
    if created:
        threading.Thread(target=_render, args=(job["job_id"],), daemon=True).start()
    return job, created


def _create_response(job: dict[str, Any], created: bool) -> JSONResponse:
    response = {
        "job_id": job["job_id"],
        "request_id": job["request_id"],
        "status": job["status"],
        "poll_url": f"/v1/segment-render-jobs/{job['job_id']}",
        "created_at": job["created_at"],
    }
    return JSONResponse(status_code=202 if created else 200, content=response)


def wait_for_segment_render_job(job_id: str) -> dict[str, Any]:
    """Wait for an existing render job without changing its worker lifecycle."""
    deadline = time.monotonic() + SEGMENT_RENDER_AWAIT_TIMEOUT_SEC
    latest: dict[str, Any] | None = None
    while True:
        try:
            latest = STORE.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={
                "code": "RENDER_JOB_NOT_FOUND",
                "job_id": job_id,
            }) from exc

        status = str(latest.get("status") or "")
        if status in {"completed", "failed"}:
            return {"wait_status": status, "job": _public(latest)}

        if time.monotonic() >= deadline:
            return {
                "wait_status": "timeout",
                "job": _public(latest),
                "error_code": "RENDER_WAIT_TIMEOUT",
                "error_message": "Render job did not finish before the synchronous wait limit.",
            }

        time.sleep(SEGMENT_RENDER_AWAIT_POLL_INTERVAL_SEC)


@router.post("/v1/segment-render-jobs")
def create_segment_render_job(
    request: SegmentRenderRequest,
) -> JSONResponse:
    job, created = _create_or_reuse_segment_render_job(request)
    return _create_response(job, created)


@router.post("/v1/segment-render-jobs/await")
def create_and_await_segment_render_job(
    request: SegmentRenderRequest,
) -> dict[str, Any]:
    """Create or reuse one segment render job, then wait briefly for its result.

    This route lets a Dify Iteration body call the renderer once without
    nesting a polling Loop. A timeout never cancels the background worker.
    """
    job, _created = _create_or_reuse_segment_render_job(request)
    return wait_for_segment_render_job(str(job["job_id"]))


@router.post("/v1/tool-09/segments/render-await")
def tool09_render_and_await(payload: Tool09SegmentRequest) -> dict[str, Any]:
    """Adapt TOOL-09's compact Dify contract to the segment renderer."""
    request = _tool09_render_request(payload)
    awaited = create_and_await_segment_render_job(request)
    job = awaited.get("job") if isinstance(awaited.get("job"), dict) else {}
    wait_status = str(awaited.get("wait_status") or job.get("status") or "")
    if wait_status != "completed" or str(job.get("status") or "") != "completed":
        error = job.get("error") if isinstance(job.get("error"), dict) else {}
        code = str(awaited.get("error_code") or error.get("code") or "SEGMENT_RENDER_FAILED")
        message = str(awaited.get("error_message") or error.get("message") or wait_status or code)
        rendered = _tool09_failed_segment(payload, code, message)
        return {
            "master_request_id": payload.master_request_id,
            "rendered_segment": rendered,
            "segment_result_valid": False,
            "segment_result_error": message,
        }

    transition = payload.segment_item.get("transition_out")
    if not isinstance(transition, dict):
        transition = {"type": "hard_cut", "duration_ms": 0}
    rendered = {
        "master_request_id": payload.master_request_id,
        "segment_id": str(payload.segment_item.get("segment_id") or ""),
        "order": int(payload.segment_item.get("order") or 1),
        "status": "completed",
        "render_error": {},
        "video_url": str(job.get("video_url") or ""),
        "thumbnail_url": str(job.get("thumbnail_url") or ""),
        "base_duration_sec": float(job.get("base_duration_sec") or 0.0),
        "head_handle_sec": float(job.get("head_handle_sec") or 0.0),
        "tail_handle_sec": float(job.get("tail_handle_sec") or 0.0),
        "actual_render_duration_sec": float(job.get("render_duration_sec") or 0.0),
        "probe_valid": bool(job.get("probe_valid")),
        "kline_main_visual_present": bool(job.get("kline_main_visual_present")),
        "degraded": bool(job.get("degraded")),
        "degradation_code": str(job.get("degradation_code") or ""),
        "degradation_records": job.get("degradation_records") if isinstance(job.get("degradation_records"), list) else [],
        "transition_out": transition,
    }
    valid = bool(rendered["video_url"] and rendered["probe_valid"] and rendered["kline_main_visual_present"])
    error_message = "" if valid else "SEGMENT_RENDER_RESULT_INVALID"
    if not valid:
        rendered["status"] = "failed"
        rendered["render_error"] = {"code": error_message, "message": error_message, "retryable": False}
    return {
        "master_request_id": payload.master_request_id,
        "rendered_segment": rendered,
        "segment_result_valid": valid,
        "segment_result_error": error_message,
    }


@router.post("/v1/tool-09/segments/finalize")
def tool09_finalize(payload: Tool09FinalizeRequest) -> dict[str, Any]:
    """Validate, order, and package all rendered segments for TOOL-10."""
    if payload.market_input.get("schema_version") != "market-input-contract-v1":
        raise HTTPException(status_code=422, detail={"code": "MARKET_INPUT_VERSION_INVALID"})
    if payload.segment_media.get("schema_version") != "segment-media-contract-v1":
        raise HTTPException(status_code=422, detail={"code": "SEGMENT_MEDIA_VERSION_INVALID"})

    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(payload.rendered_segments):
        try:
            item = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            item = None
        if not isinstance(item, dict):
            errors.append(f"RENDERED_SEGMENT_{index}_INVALID")
            continue
        if str(item.get("master_request_id") or "").strip() != payload.master_request_id:
            errors.append(f"RENDERED_SEGMENT_{index}_MASTER_ID_MISMATCH")
            continue
        parsed.append(item)
        if item.get("status") != "completed" or not item.get("video_url") or item.get("probe_valid") is not True:
            render_error = item.get("render_error") if isinstance(item.get("render_error"), dict) else {}
            errors.append(str(render_error.get("code") or f"RENDERED_SEGMENT_{index}_FAILED"))

    expected_media = payload.segment_media.get("segment_media_inputs")
    if not isinstance(expected_media, list) or not expected_media:
        errors.append("SEGMENT_MEDIA_INPUTS_EMPTY")
        expected_ids: list[str] = []
    else:
        expected_ids = [str(item.get("segment_id") or "") for item in expected_media if isinstance(item, dict)]
    rendered_ids = [str(item.get("segment_id") or "") for item in parsed]
    if len(expected_ids) != len(expected_media or []) or not all(expected_ids):
        errors.append("SEGMENT_MEDIA_IDS_INVALID")
    elif sorted(rendered_ids) != sorted(expected_ids):
        errors.append("RENDERED_SEGMENT_IDS_MISMATCH")

    order_by_id = {segment_id: index for index, segment_id in enumerate(expected_ids)}
    ordered = sorted(parsed, key=lambda item: order_by_id.get(str(item.get("segment_id") or ""), len(order_by_id)))
    errors = list(dict.fromkeys(errors))
    valid = not errors and bool(ordered)
    contract = {
        "schema_version": "rendered-segments-contract-v1",
        "master_request_id": payload.master_request_id,
        "rendered_segments": ordered,
        "segment_render_valid": valid,
        "segment_render_errors": errors,
    }
    return {
        "schema_version": "tool09-finalize-result-v1",
        "master_request_id": payload.master_request_id,
        "rendered_v1_json": json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
        "segment_render_valid": valid,
        "render_errors_json": json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
    }


@router.get("/v1/segment-render-jobs/{job_id}")
def get_segment_render_job(
    job_id: str,
) -> dict[str, Any]:
    try:
        return _public(STORE.get(job_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={
            "code": "RENDER_JOB_NOT_FOUND",
            "job_id": job_id,
        }) from exc
