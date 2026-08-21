from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.job_store import IdempotencyConflict, JobStore


DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/gold-video"))
MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
STORE = JobStore(DATA_DIR / "segment_jobs")
router = APIRouter(tags=["segment-render"])


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


def _dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


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
def create_segment_render_job(
    request: SegmentRenderRequest,
) -> JSONResponse:
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
    response = {
        "job_id": job["job_id"],
        "request_id": job["request_id"],
        "status": job["status"],
        "poll_url": f"/v1/segment-render-jobs/{job['job_id']}",
        "created_at": job["created_at"],
    }
    return JSONResponse(status_code=202 if created else 200, content=response)


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
