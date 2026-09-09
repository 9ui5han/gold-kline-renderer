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
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.job_store import IdempotencyConflict, JobStore
from app.kline_precision import normalize_kline_numbers


DATA_DIR = Path(os.environ.get("DATA_DIR", "/tmp/gold-video"))
MEDIA_DIR = DATA_DIR / "media"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
STORE = JobStore(DATA_DIR / "segment_jobs")
TOOL09_BATCH_STORE = JobStore(DATA_DIR / "tool09_batch_jobs", job_prefix="t9bj_")
_ACTIVE_RENDER_JOBS: set[str] = set()
_ACTIVE_RENDER_LOCK = threading.RLock()
SEGMENT_RENDER_CONCURRENCY = max(
    1,
    min(4, int(os.environ.get("SEGMENT_RENDER_CONCURRENCY", "1"))),
)
_RENDER_SLOTS = threading.BoundedSemaphore(SEGMENT_RENDER_CONCURRENCY)
DATA_RETENTION_DAYS = max(
    1,
    int(os.environ.get("DATA_RETENTION_DAYS", "15")),
)
DATA_CLEANUP_INTERVAL_SEC = max(
    300,
    int(os.environ.get("DATA_CLEANUP_INTERVAL_SEC", "3600")),
)
router = APIRouter(tags=["segment-render"])
SEGMENT_RENDER_AWAIT_TIMEOUT_SEC = max(
    1.0,
    min(240.0, float(os.environ.get("SEGMENT_RENDER_AWAIT_TIMEOUT_SEC", "180"))),
)
SEGMENT_RENDER_AWAIT_POLL_INTERVAL_SEC = max(
    0.1,
    min(5.0, float(os.environ.get("SEGMENT_RENDER_AWAIT_POLL_INTERVAL_SEC", "1"))),
)


def _cleanup_expired_data() -> int:
    """Delete generated files older than the retention window.

    Keep the list explicit so a persistent DATA_DIR can also contain files
    that are not generated runtime data (for example, templates or config).
    """
    cutoff = time.time() - (DATA_RETENTION_DAYS * 86400)
    roots = (
        MEDIA_DIR,
        DATA_DIR / "work",
        DATA_DIR / "photo-work",
        DATA_DIR / "segment_jobs",
        DATA_DIR / "tool09_batch_jobs",
        DATA_DIR / "compose_jobs",
        DATA_DIR / "compose",
        DATA_DIR / "composed",
    )
    removed = 0
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                continue
    return removed


def _start_cleanup_worker() -> None:
    _cleanup_expired_data()

    def run() -> None:
        while True:
            time.sleep(DATA_CLEANUP_INTERVAL_SEC)
            _cleanup_expired_data()

    threading.Thread(target=run, daemon=True, name="data-retention-cleanup").start()


_start_cleanup_worker()


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
    visual_facts: list[dict[str, Any]] = Field(default_factory=list)
    continuous_chart: dict[str, Any] = Field(default_factory=dict)
    video: VideoSpec
    fallback_policy: dict[str, Any]
    transition_out: dict[str, Any] = Field(default_factory=dict)


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


class Tool09BatchRequest(BaseModel):
    """One TOOL-09 batch that references already submitted segment jobs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^tool09-batch-request-v1$")
    master_request_id: str = Field(min_length=1, max_length=100)
    segment_job_ids: list[str] = Field(min_length=1, max_length=20)
    market_input: dict[str, Any]
    segment_media: dict[str, Any]

    @field_validator("master_request_id")
    @classmethod
    def validate_master_request_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("MASTER_REQUEST_ID_EMPTY")
        return normalized

    @field_validator("segment_job_ids")
    @classmethod
    def validate_segment_job_ids(cls, values: list[str]) -> list[str]:
        normalized = [str(value or "").strip() for value in values]
        if not all(normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("SEGMENT_JOB_IDS_INVALID")
        return normalized


def _dump(model: BaseModel) -> dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _tool09_request_id(master_request_id: str, segment_id: str) -> str:
    safe_master = re.sub(r"[^A-Za-z0-9_-]+", "-", master_request_id).strip("-") or "tool09"
    safe_segment = re.sub(r"[^A-Za-z0-9_-]+", "-", segment_id).strip("-") or "segment"
    digest = hashlib.sha256(f"{master_request_id}|{segment_id}".encode("utf-8")).hexdigest()[:16]
    # The renderer now uses a continuous rolling chart timeline.  Bump the
    # idempotency revision so a rerun cannot reuse old, reset-per-segment MP4s.
    suffix = f"{digest}-{safe_segment}-visual-r4"
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


def _build_visual_timeline(
    item: dict[str, Any],
    segment_id: str,
    base_duration: float,
    fps: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Convert TOOL-08's scene contract into the renderer timeline.

    TOOL-09 must consume resolved facts and scene timing from TOOL-08.  It is
    deliberately not allowed to replace the plan with a guessed chart scene.
    """
    visual = item.get("visual")
    scenes = item.get("scenes")
    facts = item.get("resolved_visual_facts")
    if not isinstance(visual, dict) or not isinstance(scenes, list) or not scenes:
        raise HTTPException(status_code=422, detail={"code": "VISUAL_PLAN_REQUIRED"})
    if not isinstance(facts, list) or not facts:
        raise HTTPException(status_code=422, detail={"code": "RESOLVED_VISUAL_FACTS_REQUIRED"})

    normalized_scenes: list[dict[str, Any]] = []
    camera_plan: list[dict[str, Any]] = []
    overlay_plan: list[dict[str, Any]] = []
    fact_ids = {
        str(fact.get("anchor_id"))
        for fact in facts
        if isinstance(fact, dict) and fact.get("anchor_id")
    }
    previous_end = 0.0
    default_motion = str(visual.get("camera_motion") or "static_hold")
    for index, raw_scene in enumerate(scenes, start=1):
        if not isinstance(raw_scene, dict):
            raise HTTPException(status_code=422, detail={"code": "SCENE_NOT_OBJECT", "index": index - 1})
        try:
            start = float(raw_scene.get("start_sec", previous_end))
            duration = float(raw_scene.get("duration_sec"))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail={"code": "SCENE_TIME_INVALID", "index": index - 1}) from exc
        end = float(raw_scene.get("end_sec", start + duration))
        if start < 0 or duration <= 0 or end <= start:
            raise HTTPException(status_code=422, detail={"code": "SCENE_TIME_INVALID", "index": index - 1})
        if abs(start - previous_end) > 0.05:
            raise HTTPException(status_code=422, detail={"code": "SCENE_NOT_CONTIGUOUS", "index": index - 1})
        motion = str(raw_scene.get("camera_motion") or default_motion)
        template_id = str(raw_scene.get("template_id") or "chart_push")
        if motion == "static_hold" and template_id != "closing_card":
            raise HTTPException(status_code=422, detail={"code": "STATIC_HOLD_NOT_ALLOWED", "scene_id": str(raw_scene.get("scene_id") or index)})
        scene = {
            "scene_id": str(raw_scene.get("scene_id") or f"scene_{index:02d}"),
            "template_id": template_id,
            "start_sec": start,
            "end_sec": end,
            "duration_sec": end - start,
            "camera_motion": motion,
            "overlay_events": raw_scene.get("overlay_events") if isinstance(raw_scene.get("overlay_events"), list) else [],
        }
        normalized_scenes.append(scene)
        focus_anchor_ids = [
            str(anchor)
            for event in scene["overlay_events"]
            if isinstance(event, dict)
            for anchor in (event.get("fact_anchor_ids") or [])
        ]
        camera_plan.append({
            "event_id": f"{scene['scene_id']}:camera",
            "start_sec": start,
            "end_sec": end,
            "motion": motion,
            "focus_target": str(visual.get("visual_mode") or "full_chart"),
            "focus_anchor_ids": list(dict.fromkeys(focus_anchor_ids)),
        })
        for event_index, raw_event in enumerate(scene["overlay_events"], start=1):
            if not isinstance(raw_event, dict):
                raise HTTPException(status_code=422, detail={"code": "VISUAL_EVENT_NOT_OBJECT", "index": event_index - 1})
            try:
                event_start = start + float(raw_event.get("start_sec", 0.0))
                event_duration = float(raw_event.get("duration_sec", end - start))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail={"code": "VISUAL_EVENT_TIME_INVALID"}) from exc
            event_end = event_start + event_duration
            if event_start < start - 0.05 or event_end > end + 0.05 or event_end <= event_start:
                raise HTTPException(status_code=422, detail={"code": "VISUAL_EVENT_BOUNDS_INVALID"})
            anchors = raw_event.get("fact_anchor_ids") if isinstance(raw_event.get("fact_anchor_ids"), list) else []
            if any(str(anchor) not in fact_ids for anchor in anchors):
                raise HTTPException(status_code=422, detail={"code": "RESOLVED_VISUAL_FACT_NOT_FOUND", "event_id": str(raw_event.get("event_id") or "")})
            overlay_plan.append({
                "event_id": str(raw_event.get("event_id") or f"{scene['scene_id']}:overlay:{event_index}"),
                "event_type": str(raw_event.get("event_type") or "caption"),
                "start_sec": event_start,
                "end_sec": event_end,
                "fact_anchor_ids": anchors,
            })
        previous_end = end

    if abs(previous_end - base_duration) > 0.08:
        raise HTTPException(status_code=422, detail={"code": "SCENES_DO_NOT_COVER_BASE"})
    return {
        "schema_version": "visual-timeline-v1",
        "segment_id": segment_id,
        "base_duration_sec": base_duration,
        "fps": fps,
        "visual_mode": str(visual.get("visual_mode") or "chart_intro"),
        "highlight_levels": visual.get("highlight_levels") if isinstance(visual.get("highlight_levels"), list) else [],
        "show_volume": bool(visual.get("show_volume")),
        "show_macro_marker": bool(visual.get("show_macro_marker")),
        "scenes": normalized_scenes,
        "camera_plan": camera_plan,
        "overlay_plan": overlay_plan,
    }, facts


def _tool09_render_request(payload: Tool09SegmentRequest) -> SegmentRenderRequest:
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

    market = normalize_kline_numbers(payload.market_input)
    if market.get("schema_version") != "market-input-contract-v1":
        raise HTTPException(status_code=422, detail={"code": "MARKET_INPUT_VERSION_INVALID"})

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
    timeline, visual_facts = _build_visual_timeline(item, segment_id, base_duration, fps)
    continuous_chart = item.get("continuous_chart") if isinstance(item.get("continuous_chart"), dict) else {}
    if continuous_chart.get("schema_version") == "continuous-chart-v1":
        timeline["continuous_chart"] = continuous_chart
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
        "visual_facts": visual_facts,
        "continuous_chart": continuous_chart,
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
        "transition_out": transition,
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
        "planned_effects": [],
        "applied_effects": [],
        "visual_timeline_valid": False,
        "transition_out": payload.segment_item.get("transition_out") if isinstance(payload.segment_item.get("transition_out"), dict) else {"type": "hard_cut", "duration_ms": 0},
    }


def _tool09_rendered_from_job(
    master_request_id: str,
    job: dict[str, Any],
) -> dict[str, Any]:
    """Convert one stored renderer job into TOOL-09's stable segment contract."""
    request = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    status = str(job.get("status") or "failed")
    segment_id = str(request.get("segment_id") or "")
    order = int(request.get("order") or 0)
    if status != "completed":
        error = job.get("error") if isinstance(job.get("error"), dict) else {}
        code = str(error.get("code") or "SEGMENT_RENDER_FAILED")
        message = str(error.get("message") or code)
        return {
            "master_request_id": master_request_id,
            "segment_id": segment_id,
            "order": order,
            "status": "failed",
            "render_error": {"code": code, "message": message, "retryable": bool(error.get("retryable"))},
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
            "planned_effects": [],
            "applied_effects": [],
            "visual_timeline_valid": False,
            "transition_out": request.get("transition_out") if isinstance(request.get("transition_out"), dict) else {"type": "hard_cut", "duration_ms": 0},
        }

    rendered = {
        "master_request_id": master_request_id,
        "segment_id": segment_id,
        "order": order,
        "status": "completed",
        "render_error": {},
        "video_url": str(result.get("video_url") or ""),
        "thumbnail_url": str(result.get("thumbnail_url") or ""),
        "base_duration_sec": float(result.get("base_duration_sec") or 0.0),
        "head_handle_sec": float(result.get("head_handle_sec") or 0.0),
        "tail_handle_sec": float(result.get("tail_handle_sec") or 0.0),
        "actual_render_duration_sec": float(result.get("render_duration_sec") or 0.0),
        "probe_valid": bool(result.get("probe_valid")),
        "kline_main_visual_present": bool(result.get("kline_main_visual_present")),
        "degraded": bool(result.get("degraded")),
        "degradation_code": str(result.get("degradation_code") or ""),
        "degradation_records": result.get("degradation_records") if isinstance(result.get("degradation_records"), list) else [],
        "planned_effects": result.get("planned_effects") if isinstance(result.get("planned_effects"), list) else [],
        "applied_effects": result.get("applied_effects") if isinstance(result.get("applied_effects"), list) else [],
        "visual_timeline_valid": bool(result.get("visual_timeline_valid")),
        "transition_out": request.get("transition_out") if isinstance(request.get("transition_out"), dict) else {"type": "hard_cut", "duration_ms": 0},
    }
    if not (rendered["video_url"] and rendered["probe_valid"] and rendered["kline_main_visual_present"]):
        rendered["status"] = "failed"
        rendered["render_error"] = {
            "code": "SEGMENT_RENDER_RESULT_INVALID",
            "message": "SEGMENT_RENDER_RESULT_INVALID",
            "retryable": False,
        }
    return rendered


def _tool09_batch_request_id(payload: Tool09BatchRequest) -> str:
    digest = hashlib.sha256(
        f"{payload.master_request_id}|{'|'.join(payload.segment_job_ids)}".encode("utf-8")
    ).hexdigest()[:24]
    return f"tool09-batch-{digest}"


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


SUPPORTED_CAMERA_MOTIONS = {
    "static_hold",
    "micro_drift",
    "slow_zoom_in",
    "slow_zoom_out",
    "focus_zoom",
    "light_zoom",
    "cross_zoom",
    "blur_zoom",
    "pan_left",
    "pan_right",
    "whip_left",
    "whip_right",
}
SUPPORTED_OVERLAY_EVENTS = {
    "caption",
    "hook_text",
    "technical_label",
    "risk_notice",
    "scenario_path",
    "closing_question",
}


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, value))


def _active_event(events: list[dict[str, Any]], elapsed_sec: float) -> tuple[dict[str, Any] | None, float]:
    if not events:
        return None, 0.0
    candidate = events[-1]
    for event in events:
        if float(event.get("start_sec") or 0.0) <= elapsed_sec <= float(event.get("end_sec") or 0.0):
            candidate = event
            break
    start = float(candidate.get("start_sec") or 0.0)
    end = float(candidate.get("end_sec") or start + 1.0)
    return candidate, _clamp((elapsed_sec - start) / max(end - start, 1e-6))


def _rolling_chart_window(
    candles: list[dict[str, Any]],
    progress: float,
    window_candles: int = 70,
) -> tuple[list[tuple[int, dict[str, Any]]], float, int]:
    """Keep a fixed chart window and move old candles out through the left."""
    maximum = max(1, min(int(window_candles), len(candles)))
    if not candles:
        return [], 0.0, maximum
    # Start with the first candle and build the chart left-to-right.  Only
    # after the display is full do older candles leave through the left edge.
    reveal = 1.0 + (len(candles) - 1.0) * _clamp(progress)
    left_position = max(0.0, reveal - maximum)
    first = max(0, int(math.floor(left_position)))
    last = min(len(candles), max(first + 1, int(math.ceil(reveal))))
    return list(enumerate(candles[first:last], start=first)), left_position, maximum


def _ema(closes: list[float], period: int) -> list[float]:
    """Deterministic exponential moving average over the real candle closes."""
    if not closes:
        return []
    alpha = 2.0 / (max(1, int(period)) + 1.0)
    values = [float(closes[0])]
    for close in closes[1:]:
        values.append(round(alpha * float(close) + (1.0 - alpha) * values[-1], 6))
    return values


def _camera_view(motion: str, progress: float) -> tuple[float, float]:
    """Return a deliberately visible camera crop for the reference-video pace."""
    progress = _clamp(progress)
    if motion == "focus_zoom":
        return 1.60, 0.72
    if motion in {"slow_zoom_in", "light_zoom", "cross_zoom", "blur_zoom"}:
        return 1.0 + 0.45 * progress, 0.62
    if motion == "slow_zoom_out":
        return 1.45 - 0.38 * progress, 0.38
    if motion in {"pan_left", "whip_left"}:
        return 1.40, 1.0 - progress
    if motion in {"pan_right", "whip_right"}:
        return 1.40, progress
    if motion == "micro_drift":
        return 1.08, 0.5 + math.sin(progress * math.pi * 2.0) * 0.18
    return 1.0, 0.5


def _camera_focus_price(
    facts_by_id: dict[str, dict[str, Any]],
    anchor_ids: list[Any],
) -> float | None:
    """Resolve the price the active scene is actually discussing."""
    for raw_anchor in anchor_ids:
        fact = facts_by_id.get(str(raw_anchor))
        if not isinstance(fact, dict):
            continue
        for key in ("center_price", "price"):
            try:
                return float(fact[key])
            except (KeyError, TypeError, ValueError):
                pass
        points = fact.get("path_points")
        if isinstance(points, list):
            for point in reversed(points):
                try:
                    return float(point["price"])
                except (KeyError, TypeError, ValueError):
                    continue
    return None


def _render_dynamic_frame(
    candles: list[dict[str, Any]],
    width: int,
    height: int,
    timeline: dict[str, Any],
    visual_facts: list[dict[str, Any]],
    elapsed_sec: float,
) -> bytes:
    """Render one RGB frame from the validated TOOL-08 visual timeline."""
    base_duration = max(float(timeline.get("base_duration_sec") or 1.0), 1e-6)
    elapsed = _clamp(float(elapsed_sec), 0.0, base_duration)
    camera, camera_progress = _active_event(timeline.get("camera_plan") or [], elapsed)
    motion = str((camera or {}).get("motion") or "static_hold")
    if motion not in SUPPORTED_CAMERA_MOTIONS:
        motion = "micro_drift"

    scale, horizontal = _camera_view(motion, camera_progress)
    canvas_width = max(width, int(round(width * scale)))
    canvas_height = max(height, int(round(height * scale)))
    image = Image.new("RGB", (canvas_width, canvas_height), (8, 13, 24))
    draw = ImageDraw.Draw(image, "RGBA")
    font = ImageFont.load_default()
    margin_x = max(24, canvas_width // 18)
    top = max(44, canvas_height // 12)
    bottom = canvas_height - max(72, canvas_height // 9)
    chart_width = canvas_width - margin_x * 2
    chart_height = max(1, bottom - top)

    for step in range(6):
        y = top + int(chart_height * step / 5)
        draw.line((margin_x, y, canvas_width - margin_x, y), fill=(28, 39, 58, 255), width=1)

    continuous = timeline.get("continuous_chart") if isinstance(timeline.get("continuous_chart"), dict) else {}
    global_start = float(continuous.get("global_start_sec") or 0.0)
    global_duration = float(continuous.get("global_duration_sec") or base_duration)
    chart_progress = _clamp((global_start + elapsed) / max(global_duration, 1e-6))
    window_candles = int(continuous.get("window_candles") or 70)
    visible, left_position, visible_capacity = _rolling_chart_window(
        candles, chart_progress, window_candles,
    )
    highs = [float(x["high"]) for x in candles]
    lows = [float(x["low"]) for x in candles]
    high = max(highs)
    low = min(lows)
    span = max(high - low, 1e-9)
    slot = chart_width / visible_capacity
    body_width = max(2, int(slot * 0.55))

    def y_for(price: float) -> int:
        return top + int((high - price) / span * chart_height)

    for candle_index, candle in visible:
        x = margin_x + int((candle_index - left_position + 0.5) * slot)
        open_y = y_for(float(candle["open"]))
        close_y = y_for(float(candle["close"]))
        high_y = y_for(float(candle["high"]))
        low_y = y_for(float(candle["low"]))
        color = (35, 211, 156, 255) if float(candle["close"]) >= float(candle["open"]) else (245, 92, 92, 255)
        draw.line((x, high_y, x, low_y), fill=color, width=max(1, body_width // 3))
        draw.rectangle((x - body_width // 2, min(open_y, close_y), x + body_width // 2, max(open_y, close_y)), fill=color)

    closes = [float(candle["close"]) for candle in candles]
    for period, color in ((20, (245, 194, 66, 255)), (50, (154, 120, 255, 255))):
        average = _ema(closes, period)
        points = [
            (
                margin_x + int((candle_index - left_position + 0.5) * slot),
                y_for(average[candle_index]),
            )
            for candle_index, _candle in visible
            if candle_index < len(average)
        ]
        if len(points) >= 2:
            draw.line(points, fill=color, width=max(2, body_width // 2), joint="curve")
    draw.text((margin_x + 6, top + 6), "EMA20", fill=(245, 194, 66, 230), font=font)
    draw.text((margin_x + 54, top + 6), "EMA50", fill=(154, 120, 255, 230), font=font)

    if timeline.get("show_volume"):
        volume_max = max((float(x.get("volume") or 0.0) for x in visible), default=1.0) or 1.0
        for candle_index, candle in visible:
            x = margin_x + int((candle_index - left_position + 0.5) * slot)
            bar_height = int((float(candle.get("volume") or 0.0) / volume_max) * max(12, chart_height * 0.12))
            draw.rectangle((x - body_width // 2, bottom - bar_height, x + body_width // 2, bottom), fill=(83, 120, 180, 90))

    facts_by_id = {
        str(fact.get("anchor_id")): fact
        for fact in visual_facts
        if isinstance(fact, dict) and fact.get("anchor_id")
    }
    for level_id in timeline.get("highlight_levels") or []:
        fact = facts_by_id.get(f"level:{level_id}") or facts_by_id.get(str(level_id))
        if not fact:
            continue
        try:
            y = y_for(float(fact.get("center_price")))
        except (TypeError, ValueError):
            continue
        draw.line((margin_x, y, canvas_width - margin_x, y), fill=(245, 194, 66, 230), width=2)
        draw.text((margin_x + 4, max(0, y - 12)), str(fact.get("display_text") or level_id), fill=(245, 220, 120, 255), font=font)

    for event in timeline.get("overlay_plan") or []:
        event_start = float(event.get("start_sec") or 0.0)
        event_end = float(event.get("end_sec") or event_start)
        if not (event_start <= elapsed <= event_end):
            continue
        event_progress = _clamp((elapsed - event_start) / max(event_end - event_start, 1e-6))
        event_type = str(event.get("event_type") or "caption")
        anchor_ids = event.get("fact_anchor_ids") if isinstance(event.get("fact_anchor_ids"), list) else []
        facts = [facts_by_id[str(anchor)] for anchor in anchor_ids if str(anchor) in facts_by_id]
        if event_type == "scenario_path":
            fact = next((fact for fact in facts if fact.get("fact_type") == "scenario_path"), None)
            points = fact.get("path_points") if isinstance(fact, dict) else []
            if isinstance(points, list) and len(points) >= 2:
                coords: list[tuple[int, int]] = []
                for point in points:
                    try:
                        ratio = _clamp(float(point.get("time_ratio")))
                        price = float(point.get("price"))
                    except (TypeError, ValueError):
                        continue
                    if ratio <= event_progress:
                        coords.append((margin_x + int(chart_width * ratio), y_for(price)))
                if len(coords) == 1:
                    coords.append(coords[0])
                if len(coords) >= 2:
                    draw.line(coords, fill=(76, 166, 255, 255), width=max(2, canvas_width // 240), joint="curve")
            continue
        # A technical label is already represented by the chart's EMA and
        # level layers.  Do not turn a raw number into a large blue banner at
        # the top of the screen; it obscures the chart and has no narration
        # context for the viewer.
        if event_type == "technical_label":
            continue
        display = next((str(fact.get("display_text") or "") for fact in facts if fact.get("display_text")), event_type)
        display = display.replace("\n", " ")[:120]
        box_top = max(8, top // 3)
        box_bottom = box_top + max(28, canvas_height // 18)
        draw.rounded_rectangle((margin_x, box_top, canvas_width - margin_x, box_bottom), radius=8, fill=(16, 28, 48, 225), outline=(76, 166, 255, 230), width=2)
        draw.text((margin_x + 10, box_top + 8), display, fill=(235, 242, 255, 255), font=font)

    if canvas_width == width and canvas_height == height:
        crop = image
    else:
        left = int((canvas_width - width) * _clamp(horizontal))
        focus_price = _camera_focus_price(
            facts_by_id,
            (camera or {}).get("focus_anchor_ids") or [],
        ) if motion == "focus_zoom" else None
        if focus_price is None:
            top_crop = int((canvas_height - height) * 0.5)
        else:
            target_y = y_for(focus_price)
            top_crop = int(_clamp(
                (target_y - height * 0.5) / max(canvas_height - height, 1),
            ) * (canvas_height - height))
        crop = image.crop((left, top_crop, left + width, top_crop + height))
    return crop.tobytes()


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
        if effect not in SUPPORTED_CAMERA_MOTIONS:
            records.append({
                "code": "UNSUPPORTED_CAMERA_MOTION",
                "message": f"camera motion {effect} is not implemented",
                "requested_effect": effect,
                "applied_effect": "failed",
            })
    for event in timeline.get("overlay_plan") or []:
        effect = str(event.get("event_type") or "overlay")
        requested_effects.append(effect)
        if effect not in SUPPORTED_OVERLAY_EVENTS:
            records.append({
                "code": "UNSUPPORTED_OVERLAY_EVENT",
                "message": f"overlay event {effect} is not implemented",
                "requested_effect": effect,
                "applied_effect": "failed",
            })
    requested = ",".join(requested_effects) if requested_effects else "static_hold"
    return requested, records


def _render_dynamic_video(payload: dict[str, Any], audio_path: Path, output_path: Path) -> None:
    """Stream timeline-rendered RGB frames into FFmpeg without image files."""
    video = payload["video"]
    width = int(video["width"])
    height = int(video["height"])
    fps = int(video["fps"])
    duration = float(payload["render_duration_sec"])
    frame_count = max(1, int(math.ceil(duration * fps)))
    command = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        "-i", str(audio_path),
        "-map", "0:v:0", "-map", "1:a:0",
        "-af", f"adelay={int(round(float(payload['head_handle_sec']) * 1000))}|{int(round(float(payload['head_handle_sec']) * 1000))},apad",
        "-t", f"{duration:.6f}",
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart", str(output_path),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.stdin is not None
        for frame_index in range(frame_count):
            elapsed = frame_index / fps
            frame = _render_dynamic_frame(
                payload["historical_candles"],
                width,
                height,
                payload["visual_timeline"],
                payload.get("visual_facts") if isinstance(payload.get("visual_facts"), list) else [],
                elapsed,
            )
            process.stdin.write(frame)
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        return_code = process.wait(timeout=360)
    except Exception:
        process.kill()
        process.wait(timeout=10)
        raise
    if return_code != 0:
        raise RuntimeError(f"FFMPEG_RENDER_FAILED:{stderr[-2000:]}")


def _render_failure_details(exc: Exception, payload: dict[str, Any]) -> dict[str, Any]:
    """Add an actionable marker when the source audio cannot be fetched."""
    if isinstance(exc, urllib.error.HTTPError):
        status = int(exc.code)
        if status == 404:
            return {
                "code": "AUDIO_URL_NOT_FOUND",
                "message": f"audio_url returned HTTP 404: {payload.get('audio_url', '')}",
                "retryable": False,
                "audio_url": str(payload.get("audio_url") or ""),
            }
        return {
            "code": "AUDIO_FETCH_HTTP_ERROR",
            "message": f"audio_url returned HTTP {status}",
            "retryable": status >= 500,
            "audio_url": str(payload.get("audio_url") or ""),
        }
    message = str(exc)[:2000]
    if message.startswith("DYNAMIC_EFFECT_UNSUPPORTED:"):
        _, code, detail = (message.split(":", 2) + ["", ""])[:3]
        return {"code": code or "DYNAMIC_EFFECT_UNSUPPORTED", "message": detail or message, "retryable": False}
    return {"code": "SEGMENT_RENDER_FAILED", "message": message, "retryable": True}


def _render(job_id: str) -> None:
    work = Path(tempfile.mkdtemp(prefix=f"{job_id}-"))
    try:
        job = STORE.update(job_id, status="rendering", error=None)
        payload = job["payload"]
        audio_path = work / "audio.bin"
        output_path = MEDIA_DIR / f"{job_id}.mp4"
        requested, records = _effect_degradations(payload)
        if records:
            first = records[0]
            raise RuntimeError(f"DYNAMIC_EFFECT_UNSUPPORTED:{first['code']}:{first['message']}")
        _download_audio(str(payload["audio_url"]), audio_path)
        video = payload["video"]
        _render_dynamic_video(payload, audio_path, output_path)
        probe = _probe(
            output_path,
            float(payload["render_duration_sec"]),
            int(video["fps"]),
            int(video["width"]),
            int(video["height"]),
        )
        if not probe["probe_valid"]:
            raise RuntimeError("FFPROBE_CONTRACT_FAILED")
        result = {
            "video_url": f"{PUBLIC_BASE_URL}/media/{output_path.name}",
            "thumbnail_url": "",
            "base_duration_sec": float(payload["base_duration_sec"]),
            "head_handle_sec": float(payload["head_handle_sec"]),
            "tail_handle_sec": float(payload["tail_handle_sec"]),
            "render_duration_sec": float(payload["render_duration_sec"]),
            **probe,
            "kline_main_visual_present": True,
            "degraded": False,
            "degradation_code": "",
            "requested_effect": requested,
            "applied_effect": "dynamic_timeline",
            "planned_effects": requested.split(",") if requested else [],
            "applied_effects": ["dynamic_timeline"],
            "visual_timeline_valid": True,
            "degradation_records": [],
        }
        STORE.update(job_id, status="completed", result=result, error=None)
    except Exception as exc:
        STORE.update(
            job_id,
            status="failed",
            result=None,
            error=_render_failure_details(exc, payload),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _start_render_worker(job_id: str) -> bool:
    """Start a local worker once; a later poll can resume jobs after a restart."""
    with _ACTIVE_RENDER_LOCK:
        if job_id in _ACTIVE_RENDER_JOBS:
            return False
        _ACTIVE_RENDER_JOBS.add(job_id)

    def run() -> None:
        try:
            # Submissions return immediately, but the expensive renderer is
            # deliberately capped so a batch cannot exhaust Railway memory
            # by starting every segment at once. Jobs wait here in the queue.
            with _RENDER_SLOTS:
                _render(job_id)
        finally:
            with _ACTIVE_RENDER_LOCK:
                _ACTIVE_RENDER_JOBS.discard(job_id)

    threading.Thread(target=run, daemon=True).start()
    return True


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
    if str(job.get("status") or "") in {"queued", "rendering"}:
        _start_render_worker(str(job["job_id"]))
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


@router.post("/v1/tool-09/segments/submit")
def tool09_submit(payload: Tool09SegmentRequest) -> dict[str, Any]:
    """Submit one TOOL-09 segment and return immediately without waiting for MP4."""
    request = _tool09_render_request(payload)
    job, created = _create_or_reuse_segment_render_job(request)
    return {
        "schema_version": "tool09-submit-result-v1",
        "master_request_id": payload.master_request_id,
        "job_id": str(job["job_id"]),
        "request_id": str(job["request_id"]),
        "segment_id": str(payload.segment_item.get("segment_id") or ""),
        "order": int(payload.segment_item.get("order") or 0),
        "status": str(job["status"]),
        "done": str(job["status"]) in {"completed", "failed"},
        "created": created,
    }


@router.post("/v1/tool-09/render-batches")
def tool09_create_batch(payload: Tool09BatchRequest) -> dict[str, Any]:
    """Create or reuse one batch tracker for submitted TOOL-09 segment jobs."""
    if payload.market_input.get("schema_version") != "market-input-contract-v1":
        raise HTTPException(status_code=422, detail={"code": "MARKET_INPUT_VERSION_INVALID"})
    if payload.segment_media.get("schema_version") != "segment-media-contract-v1":
        raise HTTPException(status_code=422, detail={"code": "SEGMENT_MEDIA_VERSION_INVALID"})

    for job_id in payload.segment_job_ids:
        try:
            job = STORE.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail={"code": "SEGMENT_JOB_NOT_FOUND", "job_id": job_id}) from exc
        request = job.get("payload") if isinstance(job.get("payload"), dict) else {}
        if str(request.get("master_request_id") or "") != payload.master_request_id:
            raise HTTPException(status_code=422, detail={"code": "SEGMENT_JOB_MASTER_ID_MISMATCH", "job_id": job_id})

    batch, created = TOOL09_BATCH_STORE.create_or_get(
        _tool09_batch_request_id(payload),
        payload.model_dump(),
    )
    return {
        "schema_version": "tool09-batch-submit-result-v1",
        "master_request_id": payload.master_request_id,
        "batch_job_id": str(batch["job_id"]),
        "status": str(batch["status"]),
        "done": str(batch["status"]) in {"completed", "failed"},
        "created": created,
    }


def tool09_batch_status(batch_job_id: str) -> dict[str, Any]:
    """Return fast batch progress; only package the final contract after all jobs stop."""
    try:
        batch = TOOL09_BATCH_STORE.get(batch_job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "TOOL09_BATCH_NOT_FOUND"}) from exc
    payload = Tool09BatchRequest.model_validate(batch.get("payload") or {})
    cached_result = batch.get("result")
    if str(batch.get("status")) in {"completed", "failed"} and isinstance(cached_result, dict):
        return cached_result

    jobs: list[dict[str, Any]] = []
    completed_count = 0
    failed_count = 0
    running_count = 0
    for job_id in payload.segment_job_ids:
        try:
            job = STORE.get(job_id)
        except KeyError:
            job = {
                "job_id": job_id,
                "status": "failed",
                "payload": {"segment_id": "", "order": 0},
                "error": {"code": "SEGMENT_JOB_NOT_FOUND", "message": job_id, "retryable": False},
            }
        jobs.append(job)
        status = str(job.get("status") or "queued")
        if status == "completed":
            completed_count += 1
        elif status == "failed":
            failed_count += 1
        else:
            running_count += 1
            _start_render_worker(str(job["job_id"]))

    total_count = len(jobs)
    if running_count:
        TOOL09_BATCH_STORE.update(batch_job_id, status="rendering", error=None)
        return {
            "schema_version": "tool09-batch-status-v1",
            "master_request_id": payload.master_request_id,
            "batch_job_id": batch_job_id,
            "status": "rendering",
            "done": False,
            "total_count": total_count,
            "completed_count": completed_count,
            "failed_count": failed_count,
        }

    rendered_segments = [
        _tool09_rendered_from_job(payload.master_request_id, job)
        for job in jobs
    ]
    final = tool09_finalize(Tool09FinalizeRequest.model_validate({
        "schema_version": "tool09-collection-request-v1",
        "master_request_id": payload.master_request_id,
        "rendered_segments": rendered_segments,
        "market_input": payload.market_input,
        "segment_media": payload.segment_media,
    }))
    final_status = "completed" if final["segment_render_valid"] else "failed"
    result = {
        "master_request_id": payload.master_request_id,
        "batch_job_id": batch_job_id,
        "status": final_status,
        "done": True,
        "total_count": total_count,
        "completed_count": completed_count,
        "failed_count": failed_count,
        **final,
        "schema_version": "tool09-batch-status-v1",
    }
    TOOL09_BATCH_STORE.update(
        batch_job_id,
        status=final_status,
        result=result,
        error=None if final_status == "completed" else {"code": "TOOL09_BATCH_FAILED"},
    )
    return result


@router.get("/v1/tool-09/render-batches/{batch_job_id}")
def get_tool09_batch_status(batch_job_id: str) -> dict[str, Any]:
    return tool09_batch_status(batch_job_id)


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
        "planned_effects": job.get("planned_effects") if isinstance(job.get("planned_effects"), list) else [],
        "applied_effects": job.get("applied_effects") if isinstance(job.get("applied_effects"), list) else [],
        "visual_timeline_valid": bool(job.get("visual_timeline_valid")),
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
    orders = [int(item.get("order") or 0) for item in parsed]
    if len(orders) != len(set(orders)) or sorted(orders) != list(range(1, len(orders) + 1)):
        errors.append("RENDERED_SEGMENT_ORDER_INVALID")

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
