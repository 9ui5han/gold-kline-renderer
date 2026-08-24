from __future__ import annotations

import os
import json
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.job_store import IdempotencyConflict, JobStore


def now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


DATA_DIR = Path(
    os.getenv("DATA_DIR", "/tmp/gold-video")
).resolve()

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "http://127.0.0.1:8000",
).rstrip("/")

DATA_DIR.mkdir(parents=True, exist_ok=True)

JOB_STORE = JobStore(DATA_DIR / "compose_jobs")

router = APIRouter()


TRANSITION_MAP = {
    "fade": "fade",
    "slide_left": "slideleft",
    "slide_right": "slideright",
    "zoom_blur": "zoomin",
    "cross_zoom": "circleopen",
    "light_zoom": "fadefast",
    "whip_left": "slideleft",
    "whip_right": "slideright",
    "flash": "fadefast",
    "blur_zoom": "zoomin",
}


class TransitionOut(BaseModel):
    type: str = "fade"
    duration_ms: int = Field(
        default=250,
        ge=0,
        le=500,
    )


class ComposeSegment(BaseModel):
    segment_id: str = Field(min_length=1, max_length=60)
    order: int = Field(ge=1)
    video_url: str = Field(pattern=r"^https?://")
    base_duration_sec: float = Field(gt=0, le=300)
    head_handle_sec: float = Field(default=0.0, ge=0, le=3)
    tail_handle_sec: float = Field(default=0.0, ge=0, le=3)
    actual_render_duration_sec: float = Field(gt=0, le=306)
    probe_valid: bool
    kline_main_visual_present: bool
    degraded: bool = False
    degradation_code: str = ""
    transition_out: TransitionOut


class ComposeVideoConfig(BaseModel):
    width: int = Field(default=1080, ge=320, le=3840)
    height: int = Field(default=1920, ge=320, le=3840)
    fps: int = Field(default=30, ge=24, le=60)
    format: str = Field(default="mp4", pattern=r"^mp4$")


class ComposeRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=120)
    segments: list[ComposeSegment] = Field(min_length=1, max_length=100)
    expected_final_duration_sec: float = Field(gt=0, le=3600)
    narration_timeline_sec: float = Field(gt=0, le=3600)
    duration_tolerance_sec: float = Field(default=10.0, ge=0, le=60)
    fallback_policy: dict[str, Any]
    video: ComposeVideoConfig


class FinalComposeStartRequest(BaseModel):
    """The compact string contract emitted by TOOL-10's T10-02 node."""

    market_input_v1_json: str = Field(min_length=2)
    rendered_v1_json: str = Field(min_length=2)
    master_request_id: str = Field(min_length=1, max_length=100)

    @field_validator("master_request_id")
    @classmethod
    def validate_master_request_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("MASTER_REQUEST_ID_EMPTY")
        return normalized


class FinalComposeStepRequest(BaseModel):
    """Opaque polling state returned by ``/start`` and ``/step``."""

    job_id: str = Field(min_length=1, max_length=100)
    master_request_id: str = Field(min_length=1, max_length=100)

    @field_validator("master_request_id")
    @classmethod
    def validate_master_request_id(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("MASTER_REQUEST_ID_EMPTY")
        return normalized


def _probe_duration(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]

    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
    )

    return float(result.stdout.strip())


def _probe_media(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_streams",
            "-show_format", "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    audio_streams = [
        x for x in streams if x.get("codec_type") == "audio"
    ]
    audio_durations = []
    for stream in audio_streams:
        try:
            audio_durations.append(float(stream.get("duration") or 0.0))
        except (TypeError, ValueError):
            audio_durations.append(0.0)
    return {
        "duration_sec": float((data.get("format") or {}).get("duration") or 0),
        "video_stream_valid": any(x.get("codec_type") == "video" for x in streams),
        "audio_stream_valid": bool(audio_streams),
        "audio_duration_sec": max(audio_durations, default=0.0),
    }


def _download(
    url: str,
    destination: Path,
) -> None:
    with httpx.stream(
        "GET",
        url,
        timeout=120.0,
        follow_redirects=True,
    ) as response:
        response.raise_for_status()

        with destination.open("wb") as f:
            for chunk in response.iter_bytes():
                f.write(chunk)


def _audio_trim_bounds(
    head_handle_sec: float,
    base_duration_sec: float,
) -> tuple[float, float]:
    head = max(0.0, float(head_handle_sec))
    return head, head + max(0.0, float(base_duration_sec))


def _build_ffmpeg_command(
    files: list[Path],
    transitions: list[dict[str, Any]],
    output_path: Path,
    base_durations: list[float],
    segments: list[ComposeSegment],
    trim_video_to_base: bool = False,
) -> list[str]:
    durations = [
        _probe_duration(path)
        for path in files
    ]

    cmd = ["ffmpeg", "-y"]

    for path in files:
        cmd.extend(["-i", str(path)])

    filters: list[str] = []

    # 为xfade统一timebase。
    for index in range(len(files)):
        trim_v = (
            f"trim=duration={base_durations[index]:.3f},"
            if trim_video_to_base else ""
        )
        # 每段旁白只保留base区间，并始终使用concat线性拼接；
        # 视频手柄只服务xfade，绝不让相邻两句旁白acrossfade。
        # 从head handle之后开始取完整base，防止尾部旁白被等长截断。
        head, end = _audio_trim_bounds(
            segments[index].head_handle_sec,
            base_durations[index],
        )
        trim_a = f"atrim=start={head:.3f}:end={end:.3f},"
        filters.append(
            f"[{index}:v]"
            f"{trim_v}"
            "settb=AVTB,"
            "setpts=PTS-STARTPTS"
            f"[v{index}]"
        )
        filters.append(
            f"[{index}:a]"
            f"{trim_a}"
            "asetpts=PTS-STARTPTS"
            f"[a{index}]"
        )

    current_v = "v0"
    current_a = "a0"

    cumulative_duration = durations[0]

    for index in range(1, len(files)):
        transition = transitions[index - 1]

        transition_type = str(
            transition.get("type") or "fade"
        )

        ffmpeg_transition = TRANSITION_MAP.get(transition_type)
        if transition_type != "hard_cut" and not ffmpeg_transition:
            raise ValueError(f"TRANSITION_UNSUPPORTED:{transition_type}")

        transition_sec = (
            int(
                transition.get(
                    "duration_ms",
                    350,
                )
            )
            / 1000.0
        )

        if transition_type == "hard_cut":
            transition_sec = 0.0
        else:
            # 普通转场需满足固定范围和相邻基础切片25%上限。
            transition_sec = max(
                0.15,
                min(
                    transition_sec,
                    0.50,
                    durations[index - 1] * 0.25,
                    durations[index] * 0.25,
                ),
            )

        offset = (
            cumulative_duration
            - transition_sec
        )

        next_v = f"vx{index}"
        next_a = f"ax{index}"

        if transition_type == "hard_cut":
            filters.append(
                f"[{current_v}][v{index}]"
                f"concat=n=2:v=1:a=0[{next_v}]"
            )
        else:
            filters.append(
                f"[{current_v}][v{index}]"
                f"xfade="
                f"transition={ffmpeg_transition}:"
                f"duration={transition_sec:.3f}:"
                f"offset={offset:.3f}"
                f"[{next_v}]"
            )


        # 无论视频使用何种转场，旁白都按base时间轴顺序拼接。
        filters.append(
            f"[{current_a}][a{index}]"
            f"concat=n=2:v=0:a=1[{next_a}]"
        )

        current_v = next_v
        current_a = next_a

        cumulative_duration = (
            cumulative_duration
            + durations[index]
            - transition_sec
        )

    filter_complex = ";".join(filters)

    cmd.extend([
        "-filter_complex",
        filter_complex,
        "-map",
        f"[{current_v}]",
        "-map",
        f"[{current_a}]",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ])

    return cmd


def _transition_ledger(segments, transitions):
    ledger = []
    for index, transition in enumerate(transitions):
        requested_ms = int(transition.get("duration_ms") or 0)
        transition_type = str(transition.get("type") or "fade")
        if transition_type == "hard_cut":
            actual_sec = 0.0
        else:
            actual_sec = max(
                0.15,
                min(
                    requested_ms / 1000.0,
                    0.50,
                    float(segments[index].actual_render_duration_sec) * 0.25,
                    float(segments[index + 1].actual_render_duration_sec) * 0.25,
                ),
            )
        ledger.append({
            "from_segment_id": segments[index].segment_id,
            "to_segment_id": segments[index + 1].segment_id,
            "type": transition_type,
            "duration_ms": int(round(actual_sec * 1000)),
            "actual_overlap_sec": round(actual_sec, 3),
        })
    return ledger


def run_compose_job(job_id: str, payload: dict[str, Any]) -> None:
    try:
        JOB_STORE.update(job_id, status="processing", result=None, error=None)
        request = ComposeRequest.model_validate(payload)
        segments = sorted(request.segments, key=lambda item: item.order)
        if not segments:
            raise ValueError("SEGMENTS_EMPTY")

        rebuild_ids = []
        for segment in segments:
            if not segment.probe_valid or not segment.video_url.startswith(("http://", "https://")):
                rebuild_ids.append(segment.segment_id)
        if rebuild_ids:
            JOB_STORE.update(job_id, status="failed", result={
                "rebuild_segment_ids": sorted(set(rebuild_ids)),
                "degradation_records": [],
            }, error={"code": "SEGMENT_REBUILD_REQUIRED", "message": "segment file missing or corrupt"})
            return

        requested_preview = [item.transition_out.model_dump() for item in segments[:-1]]
        requested_ledger_preview = _transition_ledger(segments, requested_preview)
        for index, boundary in enumerate(requested_ledger_preview):
            available = float(segments[index].tail_handle_sec) + float(segments[index + 1].head_handle_sec)
            if abs(available - float(boundary["actual_overlap_sec"])) > 0.002:
                raise ValueError("BOUNDARY_HANDLE_MISMATCH")
        for segment in segments:
            planned = float(segment.base_duration_sec) + float(segment.head_handle_sec) + float(segment.tail_handle_sec)
            if abs(planned - float(segment.actual_render_duration_sec)) > (1.0 / request.video.fps + 0.001):
                raise ValueError(f"{segment.segment_id}:RENDER_HANDLE_MISMATCH")
        requested_expected = sum(float(item.actual_render_duration_sec) for item in segments) - sum(float(item["actual_overlap_sec"]) for item in requested_ledger_preview)
        if abs(requested_expected - float(request.expected_final_duration_sec)) > 0.05:
            raise ValueError("EXPECTED_FINAL_DURATION_MISMATCH")

        work_dir = DATA_DIR / "compose" / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for index, segment in enumerate(segments, start=1):
            path = work_dir / f"{index:02d}_{segment.segment_id}.mp4"
            try:
                _download(segment.video_url, path)
                probe = _probe_media(path)
                if not probe["video_stream_valid"] or not probe["audio_stream_valid"]:
                    raise ValueError("undecodable")
            except Exception:
                rebuild_ids.append(segment.segment_id)
                continue
            files.append(path)
        if rebuild_ids:
            JOB_STORE.update(job_id, status="failed", result={
                "rebuild_segment_ids": sorted(set(rebuild_ids)),
                "degradation_records": [],
            }, error={"code": "SEGMENT_REBUILD_REQUIRED", "message": "segment file missing or corrupt"})
            return

        requested = [item.transition_out.model_dump() for item in segments[:-1]]
        candidates = [
            requested,
            [{"type": "hard_cut", "duration_ms": 0} if item.get("type") == "hard_cut" else {"type": "fade", "duration_ms": item.get("duration_ms", 250)} for item in requested],
            [{"type": "hard_cut", "duration_ms": 0} for _ in requested],
        ]
        requested_ledger = _transition_ledger(segments, requested)
        output_dir = DATA_DIR / "composed"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{job_id}.mp4"
        applied_ledger = []
        attempt_name = ""
        last_error = ""

        for name, transitions in zip(("requested", "fade", "hard_cut"), candidates):
            attempt_ledger = _transition_ledger(segments, transitions)
            actual_overlap = sum(item["actual_overlap_sec"] for item in attempt_ledger)
            attempt_expected = sum(float(item.actual_render_duration_sec) for item in segments) - actual_overlap
            if name == "hard_cut":
                # hard_cut没有overlap；去掉所有视频手柄后再concat，使成片回到base时间轴。
                attempt_expected = sum(float(item.base_duration_sec) for item in segments)
            try:
                command = _build_ffmpeg_command(
                    files,
                    transitions,
                    output_path,
                    [item.base_duration_sec for item in segments],
                    segments,
                    trim_video_to_base=(name == "hard_cut"),
                )
                subprocess.run(command, check=True, capture_output=True, text=True)
                actual_probe = _probe_media(output_path)
                if abs(float(actual_probe["duration_sec"]) - attempt_expected) > 0.15:
                    raise ValueError("ATTEMPT_DURATION_MISMATCH")
                applied_ledger = attempt_ledger
                attempt_name = name
                break
            except Exception as exc:
                last_error = str(exc)[-2000:]
        else:
            raise ValueError("COMPOSE_ALL_FALLBACKS_FAILED:" + last_error)

        probe = _probe_media(output_path)
        duration_sec = float(probe["duration_sec"])
        if not probe["video_stream_valid"] or not probe["audio_stream_valid"]:
            raise ValueError("FINAL_MEDIA_UNDECODABLE")
        if abs(duration_sec - request.narration_timeline_sec) > 0.15:
            raise ValueError("FINAL_NARRATION_DURATION_MISMATCH")
        frame_tolerance = 1.0 / request.video.fps + 0.001
        expected_narration_tail_sec = round(sum(float(item.base_duration_sec) for item in segments), 3)
        actual_narration_tail_sec = round(float(probe["audio_duration_sec"]), 3)
        narration_tail_diff_sec = round(actual_narration_tail_sec - expected_narration_tail_sec, 3)
        narration_complete = actual_narration_tail_sec > 0 and abs(narration_tail_diff_sec) <= frame_tolerance
        if not narration_complete:
            raise ValueError("NARRATION_TRUNCATED")

        actual_overlap = round(sum(item["actual_overlap_sec"] for item in applied_ledger), 3)
        records = [] if attempt_name == "requested" else [{
            "degradation_code": "TRANSITION_RENDER_FAILED",
            "requested_effect": "requested",
            "applied_effect": attempt_name,
        }]
        relative = output_path.relative_to(DATA_DIR).as_posix()
        result = {
            "video_url": f"{PUBLIC_BASE_URL}/media/{relative}",
            "duration_sec": duration_sec,
            "narration_timeline_sec": request.narration_timeline_sec,
            "probe_valid": True,
            "video_stream_valid": probe["video_stream_valid"],
            "audio_stream_valid": probe["audio_stream_valid"],
            "audio_duration_sec": probe["audio_duration_sec"],
            "narration_complete": True,
            "expected_narration_tail_sec": expected_narration_tail_sec,
            "actual_narration_tail_sec": actual_narration_tail_sec,
            "narration_tail_diff_sec": narration_tail_diff_sec,
            "kline_main_visual_present": any(item.kline_main_visual_present for item in segments),
            "requested_transitions": requested_ledger,
            "applied_transitions": applied_ledger,
            "actual_overlap_sec": actual_overlap,
            "degradation_records": records,
            "rebuild_segment_ids": [],
        }
        JOB_STORE.update(job_id, status="completed", result=result, error=None)
    except Exception as exc:
        JOB_STORE.update(job_id, status="failed", result=None, error={
            "code": "COMPOSE_FAILED",
            "message": str(exc),
        })


def _public_job(job: dict[str, Any]) -> dict[str, Any]:
    response = {
        "job_id": str(job["job_id"]),
        "request_id": str(job["request_id"]),
        "status": str(job["status"]),
        "status_url": f"{PUBLIC_BASE_URL}/v1/compose-jobs/{job['job_id']}",
        "created_at": str(job["created_at"]),
        "updated_at": str(job["updated_at"]),
    }
    if isinstance(job.get("result"), dict):
        response.update(job["result"])
    if isinstance(job.get("error"), dict):
        response["error"] = job["error"]
    return response


def _load_contract(raw: str, version: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or ""))
    except json.JSONDecodeError as exc:
        raise HTTPException(422, f"{label}_JSON_INVALID") from exc
    if not isinstance(value, dict):
        raise HTTPException(422, f"{label}_NOT_OBJECT")
    if value.get("schema_version") != version:
        raise HTTPException(422, f"{label}_VERSION_INVALID")
    return value


def _compose_request_from_tool10(payload: FinalComposeStartRequest) -> ComposeRequest:
    market = _load_contract(
        payload.market_input_v1_json,
        "market-input-contract-v1",
        "MARKET_INPUT",
    )
    rendered = _load_contract(
        payload.rendered_v1_json,
        "rendered-segments-contract-v1",
        "RENDERED_SEGMENTS",
    )
    if str(rendered.get("master_request_id") or "").strip() != payload.master_request_id:
        raise HTTPException(409, "MASTER_REQUEST_ID_MISMATCH")
    if rendered.get("segment_render_valid") is not True:
        raise HTTPException(422, "SEGMENT_RENDER_NOT_VALID")
    errors = rendered.get("segment_render_errors")
    if not isinstance(errors, list) or errors:
        raise HTTPException(422, "SEGMENT_RENDER_ERRORS_NOT_EMPTY")
    items = rendered.get("rendered_segments")
    if not isinstance(items, list) or not items:
        raise HTTPException(422, "RENDERED_SEGMENTS_EMPTY")

    segments: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise HTTPException(422, f"RENDERED_SEGMENT_NOT_OBJECT:{index}")
        video = item.get("video") if isinstance(item.get("video"), dict) else {}
        transition = item.get("transition_out")
        if not isinstance(transition, dict):
            transition = {"type": "hard_cut", "duration_ms": 0}
        base = float(item.get("base_duration_sec") or item.get("duration_sec") or 0)
        head = float(item.get("head_handle_sec") or 0)
        tail = float(item.get("tail_handle_sec") or 0)
        actual = float(
            item.get("actual_render_duration_sec")
            or item.get("render_duration_sec")
            or video.get("duration_sec")
            or 0
        )
        segments.append({
            "segment_id": str(item.get("segment_id") or ""),
            "order": int(item.get("order") or index + 1),
            "video_url": str(video.get("url") or item.get("video_url") or ""),
            "base_duration_sec": base,
            "head_handle_sec": head,
            "tail_handle_sec": tail,
            "actual_render_duration_sec": actual,
            "probe_valid": item.get("probe_valid") is True,
            "kline_main_visual_present": item.get("kline_main_visual_present") is True,
            "degraded": item.get("degraded") is True,
            "degradation_code": str(item.get("degradation_code") or ""),
            "transition_out": transition,
        })

    ordered = sorted(segments, key=lambda value: value["order"])
    # The final segment has no following boundary, so its transition is ignored.
    requested = [value["transition_out"] for value in ordered[:-1]]
    typed_segments = [ComposeSegment.model_validate(value) for value in ordered]
    ledger = _transition_ledger(typed_segments, requested)
    expected = sum(value["actual_render_duration_sec"] for value in ordered) - sum(
        float(value["actual_overlap_sec"]) for value in ledger
    )
    narration_timeline = sum(value["base_duration_sec"] for value in ordered)
    video_value = rendered.get("video")
    if not isinstance(video_value, dict):
        job_config = market.get("job_config")
        if not isinstance(job_config, dict):
            job_config = {}
        video_value = job_config.get("video")
        if not isinstance(video_value, dict):
            video_value = market.get("video") if isinstance(market.get("video"), dict) else {}
    return ComposeRequest.model_validate({
        "request_id": f"{payload.master_request_id}-final-compose",
        "segments": ordered,
        "expected_final_duration_sec": round(expected, 3),
        "narration_timeline_sec": round(narration_timeline, 3),
        "duration_tolerance_sec": 10.0,
        "fallback_policy": {"transition_failure": ["fade", "hard_cut"]},
        "video": video_value or {},
    })


def _final_result(job: dict[str, Any], master_request_id: str) -> dict[str, Any]:
    status = str(job.get("status") or "")
    result = job.get("result") if isinstance(job.get("result"), dict) else {}
    error = job.get("error") if isinstance(job.get("error"), dict) else {}
    valid = status == "completed"
    error_code = str(error.get("code") or "FINAL_COMPOSE_FAILED")
    errors = [] if valid else [error_code]
    duration = float(result.get("duration_sec") or 0)
    target = float(result.get("narration_timeline_sec") or 0)
    difference = round(duration - target, 3)
    contract = {
        "schema_version": "final-result-contract-v1",
        "master_request_id": master_request_id,
        "final_valid": valid,
        "final_errors": errors,
        "final_video_url": str(result.get("video_url") or ""),
        "final_duration_sec": duration,
    }
    return {
        "schema_version": "final-compose-result-v1",
        "master_request_id": master_request_id,
        "final_result_v1_json": json.dumps(contract, ensure_ascii=False, separators=(",", ":")),
        "final_valid": valid,
        "final_errors_json": json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
        "final_video_url": str(result.get("video_url") or ""),
        "final_duration_sec": duration,
        "target_duration_sec": target,
        "duration_diff_sec": difference,
        "duration_in_preferred": valid and abs(difference) <= 3.0,
        "duration_in_hard": valid and abs(difference) <= 10.0,
        "final_degraded": bool(result.get("degradation_records")),
        "degradation_records": result.get("degradation_records") or [],
        "rebuild_segment_ids": result.get("rebuild_segment_ids") or [],
        "requested_transitions": result.get("requested_transitions") or [],
        "applied_transitions": result.get("applied_transitions") or [],
        "actual_overlap_sec": float(result.get("actual_overlap_sec") or 0),
    }


def _poll_request(job_id: str, master_request_id: str) -> str:
    return json.dumps(
        {"job_id": job_id, "master_request_id": master_request_id},
        ensure_ascii=False,
        separators=(",", ":"),
    )


@router.post("/v1/final-compose-jobs/start", status_code=202)
def start_final_compose_job(payload: FinalComposeStartRequest) -> dict[str, Any]:
    request = _compose_request_from_tool10(payload)
    job = create_compose_job(request)
    job_id = str(job["job_id"])
    current = JOB_STORE.get(job_id)
    done = str(current.get("status")) in {"completed", "failed"}
    result_json = "{}"
    if done:
        result_json = json.dumps(
            _final_result(current, payload.master_request_id),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return {
        "schema_version": "final-compose-start-v1",
        "master_request_id": payload.master_request_id,
        "request_json": _poll_request(job_id, payload.master_request_id),
        "done": done,
        "result_json": result_json,
    }


@router.post("/v1/final-compose-jobs/step")
def step_final_compose_job(payload: FinalComposeStepRequest) -> dict[str, Any]:
    try:
        job = JOB_STORE.get(payload.job_id)
    except KeyError as exc:
        raise HTTPException(404, "COMPOSE_JOB_NOT_FOUND") from exc
    expected_request_id = f"{payload.master_request_id}-final-compose"
    if str(job.get("request_id") or "") != expected_request_id:
        raise HTTPException(409, "MASTER_REQUEST_ID_MISMATCH")
    status = str(job.get("status") or "")
    done = status in {"completed", "failed"}
    action = "pass" if status == "completed" else "fail" if status == "failed" else "continue"
    result_json = "{}"
    if done:
        result_json = json.dumps(
            _final_result(job, payload.master_request_id),
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return {
        "schema_version": "final-compose-step-v1",
        "master_request_id": payload.master_request_id,
        "action": action,
        "done": done,
        "next_request_json": _poll_request(payload.job_id, payload.master_request_id),
        "result_json": result_json,
    }


@router.post(
    "/v1/compose-jobs",
    status_code=202,
)
def create_compose_job(
    payload: ComposeRequest,
) -> dict[str, Any]:
    payload_dict = payload.model_dump()
    try:
        stored, created = JOB_STORE.create_or_get(payload.request_id, payload_dict)
    except IdempotencyConflict as exc:
        raise HTTPException(409, "IDEMPOTENCY_CONFLICT") from exc
    job_id = str(stored["job_id"])

    if not created:
        return _public_job(stored)

    threading.Thread(
        target=run_compose_job,
        args=(
            job_id,
            payload.model_dump(),
        ),
        daemon=True,
    ).start()

    return _public_job(JOB_STORE.get(job_id))


@router.get(
    "/v1/compose-jobs/{job_id}",
)
def get_compose_job(
    job_id: str,
) -> dict[str, Any]:
    try:
        job = JOB_STORE.get(job_id)
    except KeyError:
        raise HTTPException(
            404,
            "COMPOSE_JOB_NOT_FOUND",
        )
    return _public_job(job)
