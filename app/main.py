import json
import html
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator

from .chart_renderer import render_tradingview_scene
from .macro_source_probe import probe_all_sources


logger = logging.getLogger("gold_kline_renderer")
DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp/gold-video"))
MEDIA_DIR = DATA_DIR / "media"
WORK_DIR = DATA_DIR / "work"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)
TOKEN = os.getenv("RENDER_SERVICE_TOKEN", "change-me")
AI302_API_KEY = os.getenv("AI302_API_KEY", "")
INDEXTTS2_SPEAKER_AUDIO_URL = os.getenv(
    "INDEXTTS2_SPEAKER_AUDIO_URL",
    "",
).strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_MB", "30")) * 1024 * 1024
QWEN3_TTS_MAX_INPUT_BYTES = int(
    os.getenv("QWEN3_TTS_MAX_INPUT_BYTES", "540")
)
FONT_PATHS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


class Candle(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    @field_validator("high")
    @classmethod
    def high_is_valid(cls, value: float, info):
        return value


class VideoOptions(BaseModel):
    width: int = Field(default=1080, ge=720, le=2160)
    height: int = Field(default=1920, ge=1280, le=3840)
    fps: int = Field(default=30, ge=24, le=60)
    format: Literal["mp4"] = "mp4"


class StyleOptions(BaseModel):
    theme: str = "dark_gold"
    # Dify uses the new direction names; the legacy analysis package still
    # stores its candle scenarios as base/bull/bear.
    scenario: Literal["base", "bull", "bear"] = "base"
    show_volume: bool = True
    show_support_resistance: bool = True
    show_observation_zones: bool = True
    show_subtitles: bool = True
    show_path_shadow: bool = False
    show_alternate_path: bool = True
    show_exact_forecast_prices: bool = False
    show_exact_forecast_times: bool = False

    @field_validator("scenario", mode="before")
    @classmethod
    def normalize_scenario(cls, value: Any) -> str:
        return {
            "sideways": "base",
            "up": "bull",
            "down": "bear",
        }.get(str(value or "").strip().lower(), str(value or "base"))


class RenderRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    symbol: str = "XAUUSD"
    timeframe: str
    data_as_of: str
    duration_target_sec: int = Field(default=90, ge=60, le=120)
    test_duration_sec: int = Field(default=10, ge=5, le=20)
    historical_candles: list[Candle] = Field(min_length=20, max_length=500)
    analysis_forecast: dict[str, Any]
    forecast_paths: dict[str, Any] = Field(default_factory=dict)
    narration: dict[str, Any]
    audio_url: str = ""
    video: VideoOptions = Field(default_factory=VideoOptions)
    style: StyleOptions = Field(default_factory=StyleOptions)

    @field_validator("historical_candles")
    @classmethod
    def validate_ohlc(cls, candles: list[Candle]):
        for index, candle in enumerate(candles):
            if candle.high < max(candle.open, candle.close, candle.low):
                raise ValueError(f"第{index}根K线high不合法")
            if candle.low > min(candle.open, candle.close, candle.high):
                raise ValueError(f"第{index}根K线low不合法")
        return candles

    @field_validator("audio_url")
    @classmethod
    def validate_audio_url(cls, value: str):
        if value == "":
            return value
        if not value.startswith(("https://", "http://")):
            raise ValueError("audio_url必须是HTTP(S)地址")
        return value


app = FastAPI(
    title="302 AI + Python Gold K-line Renderer",
    version="1.0.0",
    description="根据真实OHLCV、预测情景和302.AI语音生成TikTok竖屏MP4。",
)
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
JOBS: dict[str, dict[str, Any]] = {}
TTS_JOBS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()


def require_token(authorization: str = Header(default="")) -> None:
    if TOKEN == "change-me":
        raise HTTPException(503, "服务器尚未设置RENDER_SERVICE_TOKEN")
    if authorization != f"Bearer {TOKEN}":
        raise HTTPException(401, "TOKEN_INVALID")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def update_job(job_id: str, **changes: Any) -> None:
    with LOCK:
        JOBS[job_id].update(changes)
        JOBS[job_id]["updated_at"] = now_iso()


def update_tts_job(job_id: str, **changes: Any) -> None:
    with LOCK:
        TTS_JOBS[job_id].update(changes)
        TTS_JOBS[job_id]["updated_at"] = now_iso()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": now_iso()}


@app.get(
    "/v1/macro-events/source-health",
    dependencies=[Depends(require_token)],
)
def macro_event_source_health() -> dict[str, Any]:
    """Check official calendar reachability without interpreting direction."""
    return probe_all_sources()


class TTSProxyRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=5000)
    # 可选的朗读专用文本。Eleven v3 的情绪/停顿标签只放在这里，
    # 原始 text 继续用于 WhisperX 字幕对齐，避免控制标签进入字幕。
    speech_text: str | None = Field(default=None, max_length=7000)
    voice_type: str = "Kore"
    voice_id: str = Field(default="30065", pattern=r"^\d+$")
    speed_ratio: float = Field(default=1.0, ge=0.5, le=2.0)
    style_prompt: str = Field(default="", max_length=2000)
    emotion_mode: Literal["auto", "neutral"] = "auto"
    narration_json: dict[str, Any] | str | None = None
    tts_provider: Literal[
        "dubbingx",
        "openai",
        "elevenlabs",
        "minimax",
        "indextts2",
        "glm_tts",
        "qwen3_tts",
    ] = "dubbingx"
    openai_voice: Literal["alloy"] = "alloy"
    elevenlabs_voice_id: str = Field(
        default="JBFqnCBsd6RMkjVDRZzb",
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    minimax_voice_id: str = Field(
        default="audiobook_male_1",
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    glm_voice: Literal[
        "tongtong",
        "chuichui",
        "xiaochen",
        "jam",
        "kazi",
        "douji",
        "luodo",
    ] = "tongtong"
    qwen3_voice: Literal["Elias"] = "Elias"


def ai302_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {AI302_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def parse_narration_segments(payload: TTSProxyRequest) -> list[str]:
    """读取旁白定稿中的分段，并保证分段原文与完整旁白一致。"""
    raw_narration = payload.narration_json
    if isinstance(raw_narration, str):
        raw_narration = json.loads(raw_narration or "{}")

    if not isinstance(raw_narration, dict):
        return [payload.text]

    raw_segments = raw_narration.get("segments") or []
    if not isinstance(raw_segments, list) or not raw_segments:
        return [payload.text]

    ordered_segments = sorted(
        (item for item in raw_segments if isinstance(item, dict)),
        key=lambda item: int(item.get("order") or 0),
    )
    segments = [
        str(item.get("text") or "").strip()
        for item in ordered_segments
        if str(item.get("text") or "").strip()
    ]
    if not segments:
        return [payload.text]

    normalize = lambda value: re.sub(r"\s+", "", str(value or ""))
    if normalize("".join(segments)) != normalize(payload.text):
        raise ValueError("narration_json分段文字与完整旁白不一致")

    return segments


def parse_elevenlabs_segments(payload: TTSProxyRequest) -> list[dict[str, Any]]:
    """读取ElevenLabs分段文字和段后停顿，并校验完整文本一致。"""
    raw_narration = payload.narration_json
    if isinstance(raw_narration, str):
        raw_narration = json.loads(raw_narration or "{}")

    if not isinstance(raw_narration, dict):
        return [{"text": payload.text.strip(), "pause_after_ms": 0}]

    raw_segments = raw_narration.get("segments") or []
    if not isinstance(raw_segments, list) or not raw_segments:
        return [{"text": payload.text.strip(), "pause_after_ms": 0}]

    indexed_segments = list(enumerate(raw_segments, start=1))
    ordered_segments = sorted(
        (
            (index, item)
            for index, item in indexed_segments
            if isinstance(item, dict)
        ),
        key=lambda pair: int(pair[1].get("order") or pair[0]),
    )

    segments: list[dict[str, Any]] = []
    for index, item in ordered_segments:
        text = str(item.get("text") or item.get("spoken_text") or "").strip()
        if not text:
            continue
        pause_value = item.get("pause_after_ms", 0)
        if isinstance(pause_value, bool):
            raise ValueError(f"ElevenLabs分段{index}的pause_after_ms不是整数")
        try:
            pause_after_ms = int(pause_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"ElevenLabs分段{index}的pause_after_ms不是整数"
            ) from exc
        if not 0 <= pause_after_ms <= 650:
            raise ValueError(
                f"ElevenLabs分段{index}的pause_after_ms超出0至650毫秒范围"
            )
        segments.append(
            {
                "text": text,
                "pause_after_ms": pause_after_ms,
                "section": str(item.get("section") or "").strip().lower(),
            }
        )

    if not segments:
        return [{"text": payload.text.strip(), "pause_after_ms": 0}]

    normalize = lambda value: re.sub(r"\s+", " ", str(value or "")).strip()
    joined_text = " ".join(item["text"] for item in segments)
    if normalize(joined_text) != normalize(payload.text):
        raise ValueError("narration_json分段文字与完整旁白不一致")

    return segments


def build_elevenlabs_narrative_chunks(
    planned_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """把口播计划的短句合并成连续叙事块，避免每句话都重新起音。

    Dify 仍会保留并校验原来的细粒度分段、停顿和文字；这里仅决定
    ElevenLabs 实际要合成几次。若计划不符合预期的章节顺序，则退回原
    分段，避免擅自重排或漏读文字。
    """
    if len(planned_segments) < 2:
        return planned_segments

    section_to_chunk = {
        "opening": (0, "opening_context"),
        "context": (0, "opening_context"),
        "evidence": (1, "levels_confirmation"),
        "primary": (2, "primary_path"),
        "alternate": (3, "alternate_risk_closing"),
        "risk": (3, "alternate_risk_closing"),
        "closing": (3, "alternate_risk_closing"),
    }
    grouped: list[tuple[str, list[dict[str, Any]]]] = []
    last_chunk_index = -1

    for segment in planned_segments:
        section = str(segment.get("section") or "").strip().lower()
        target = section_to_chunk.get(section)
        if target is None:
            return planned_segments
        chunk_index, chunk_name = target
        if chunk_index < last_chunk_index:
            return planned_segments
        if chunk_index > last_chunk_index:
            grouped.append((chunk_name, []))
            last_chunk_index = chunk_index
        grouped[-1][1].append(segment)

    if not 2 <= len(grouped) <= 4:
        return planned_segments

    return [
        {
            "text": " ".join(item["text"] for item in chunk_segments),
            "pause_after_ms": int(chunk_segments[-1]["pause_after_ms"] or 0),
            "section": chunk_name,
        }
        for chunk_name, chunk_segments in grouped
    ]


def post_dubbingx(path: str, body: dict[str, Any], timeout: float = 60) -> dict[str, Any]:
    response = httpx.post(
        f"https://api.302.ai{path}",
        headers=ai302_headers(),
        json=body,
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_DUBBINGX_HTTP_STATUS",
                "upstream": upstream_error_summary(exc.response),
            },
        ) from exc

    result = response.json()
    if not isinstance(result, dict) or result.get("success") is not True:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_DUBBINGX_FAILED",
                "upstream": result,
            },
        )
    return result


def analyze_dubbingx_emotion(text: str) -> str:
    result = post_dubbingx(
        "/dubbingx/v2/analyzeEmotion",
        {"text": text},
    )
    emotion = str(result.get("data") or "").strip()
    if not emotion:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_DUBBINGX_EMOTION_EMPTY",
                "upstream": result,
            },
        )
    return emotion


def submit_dubbingx_tts(
    text: str,
    voice_id: str,
    emotion: str,
    speed_ratio: float,
) -> str:
    ssml = (
        f'<speak voiceId="{voice_id}" language="zh" '
        f'emotion="{html.escape(emotion, quote=True)}" '
        f'audioPitch="1.0" audioSpeed="{speed_ratio:.2f}">'
        f'{html.escape(text, quote=False)}</speak>'
    )
    result = post_dubbingx(
        "/dubbingx/v2/addTtsTask",
        {"text": ssml},
    )
    data = result.get("data") or {}
    task_id = str(data.get("taskId") or "").strip() if isinstance(data, dict) else ""
    if not task_id:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_DUBBINGX_TASK_ID_EMPTY",
                "upstream": result,
            },
        )
    return task_id


def wait_for_dubbingx_tts(task_id: str) -> str:
    max_checks = int(os.getenv("DUBBINGX_MAX_POLLS", "120"))
    poll_interval = float(os.getenv("DUBBINGX_POLL_INTERVAL_SEC", "2"))

    for _ in range(max_checks):
        result = post_dubbingx(
            f"/dubbingx/v1/getTtsTaskInfo/{task_id}",
            {},
        )
        data = result.get("data") or {}
        status = str(data.get("status") or "").strip().lower()

        if status == "completed":
            file_url = str(data.get("fileUrl") or "").strip()
            if not file_url.startswith(("https://", "http://")):
                raise HTTPException(
                    status_code=502,
                    detail={"error_code": "302_DUBBINGX_AUDIO_URL_EMPTY"},
                )
            return file_url

        if status in {"failed", "canceled"}:
            raise HTTPException(
                status_code=502,
                detail={
                    "error_code": "302_DUBBINGX_TASK_FAILED",
                    "upstream": result,
                },
            )

        if status not in {"ready", "generating"}:
            raise HTTPException(
                status_code=502,
                detail={
                    "error_code": "302_DUBBINGX_UNKNOWN_STATUS",
                    "upstream": result,
                },
            )
        time.sleep(poll_interval)

    raise HTTPException(
        status_code=504,
        detail={"error_code": "302_DUBBINGX_TIMEOUT"},
    )


def concatenate_audio(parts: list[Path], output_path: Path) -> None:
    if len(parts) == 1:
        shutil.copyfile(parts[0], output_path)
        return

    args = ["ffmpeg", "-y"]
    for part in parts:
        args.extend(["-i", str(part)])
    filter_inputs = "".join(f"[{index}:a]" for index in range(len(parts)))
    args.extend(
        [
            "-filter_complex",
            f"{filter_inputs}concat=n={len(parts)}:v=0:a=1[outa]",
            "-map",
            "[outa]",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )
    run_command(args)


def normalize_audio_to_wav(input_path: Path, output_path: Path) -> None:
    """把不同来源音频统一成ffmpeg concat可稳定处理的单声道WAV。"""
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )


def create_silence_wav(duration_ms: int, output_path: Path) -> None:
    """生成指定时长的无声片段，供段落之间插入。"""
    duration_sec = max(0.001, duration_ms / 1000)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{duration_sec:.3f}",
            "-ar",
            "44100",
            "-ac",
            "1",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
    )


def concatenate_audio_with_pauses(
    normalized_segments: list[Path],
    pauses_after_ms: list[int],
    output_path: Path,
    work_dir: Path,
) -> None:
    """插入段间静音并编码为MP3；最后一段停顿不追加到末尾。"""
    parts: list[Path] = []
    for index, segment_path in enumerate(normalized_segments):
        parts.append(segment_path)
        if index < len(normalized_segments) - 1:
            pause_ms = int(pauses_after_ms[index] or 0)
            if pause_ms > 0:
                silence_path = work_dir / f"silence-{index:02d}.wav"
                create_silence_wav(pause_ms, silence_path)
                parts.append(silence_path)

    combined_wav = work_dir / "combined.wav"
    concatenate_audio(parts, combined_wav)
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(combined_wav),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "128k",
            str(output_path),
        ]
    )


def split_text_by_utf8_limit(text: str, max_bytes: int) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []
    if len(normalized.encode("utf-8")) <= max_bytes:
        return [normalized]

    pieces = re.findall(r".+?[。！？；，,]|.+$", normalized, flags=re.S)
    chunks: list[str] = []
    current = ""

    def push_current() -> None:
        nonlocal current
        if current:
            chunks.append(current)
            current = ""

    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        if len(piece.encode("utf-8")) > max_bytes:
            push_current()
            buffer = ""
            for char in piece:
                candidate = buffer + char
                if len(candidate.encode("utf-8")) > max_bytes:
                    if buffer:
                        chunks.append(buffer)
                    buffer = char
                else:
                    buffer = candidate
            if buffer:
                chunks.append(buffer)
            continue

        candidate = current + piece
        if current and len(candidate.encode("utf-8")) > max_bytes:
            push_current()
            current = piece
        else:
            current = candidate

    push_current()
    return chunks


def generate_openai_tts(payload: TTSProxyRequest, output_path: Path) -> None:
    """按302.AI官方OpenAI Speech格式生成一条试听音频。"""
    response = httpx.post(
        "https://api.302.ai/v1/audio/speech",
        headers=ai302_headers(),
        json={
            "model": "gpt-4o-mini-tts",
            "input": payload.text,
            "voice": payload.openai_voice,
        },
        timeout=180,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_OPENAI_TTS_HTTP_STATUS",
                "upstream": upstream_error_summary(exc.response),
            },
        ) from exc

    content_type = response.headers.get("content-type", "").lower()
    if "audio" not in content_type:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_OPENAI_TTS_INVALID_RESPONSE",
                "content_type": content_type,
                "body": response.text[:1000],
            },
        )
    if not response.content or len(response.content) > MAX_AUDIO_BYTES:
        raise HTTPException(
            status_code=502,
            detail={"error_code": "302_OPENAI_TTS_INVALID_AUDIO_SIZE"},
        )
    output_path.write_bytes(response.content)


def generate_elevenlabs_tts(payload: TTSProxyRequest, output_path: Path) -> None:
    """按302.AI官方ElevenLabs格式生成一条试听音频。"""
    response = httpx.post(
        (
            "https://api.302.ai/elevenlabs/text-to-speech/"
            f"{payload.elevenlabs_voice_id}"
        ),
        params={
            "output_format": "mp3_44100_128",
            "response_format": "url",
        },
        headers=ai302_headers(),
        json={
            "text": (payload.speech_text or payload.text).strip(),
            "model_id": "eleven_v3",
        },
        timeout=180,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_ELEVENLABS_TTS_HTTP_STATUS",
                "upstream": upstream_error_summary(exc.response),
            },
        ) from exc

    result = response.json()
    audio_url = str(result.get("url") or "").strip()
    if not audio_url.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_ELEVENLABS_TTS_AUDIO_URL_EMPTY",
                "upstream": result,
            },
        )
    download_audio(audio_url, output_path)


def generate_elevenlabs_segmented_tts(
    payload: TTSProxyRequest,
    output_path: Path,
    request_work_dir: Path,
) -> None:
    """按连续叙事块调用302.AI ElevenLabs V3，并保留计划中的段间停顿。"""
    planned_segments = parse_elevenlabs_segments(payload)
    segments = build_elevenlabs_narrative_chunks(planned_segments)
    logger.info(
        "ELEVENLABS_NARRATIVE_CHUNKS request_id=%s planned_segments=%s tts_chunks=%s",
        payload.request_id,
        len(planned_segments),
        len(segments),
    )
    if len(segments) == 1:
        generate_elevenlabs_tts(payload, output_path)
        return

    normalized_paths: list[Path] = []
    pauses_after_ms: list[int] = []
    for index, segment in enumerate(segments):
        segment_text = segment["text"]
        segment_mp3 = request_work_dir / f"eleven-segment-{index:02d}.mp3"
        segment_wav = request_work_dir / f"eleven-segment-{index:02d}.wav"
        segment_payload = payload.model_copy(
            update={
                "text": segment_text,
                "speech_text": segment_text,
                "narration_json": None,
            }
        )
        generate_elevenlabs_tts(segment_payload, segment_mp3)
        normalize_audio_to_wav(segment_mp3, segment_wav)
        normalized_paths.append(segment_wav)
        pauses_after_ms.append(int(segment["pause_after_ms"] or 0))
        logger.info(
            "ELEVENLABS_SEGMENT_COMPLETED request_id=%s segment=%s/%s "
            "pause_after_ms=%s",
            payload.request_id,
            index + 1,
            len(segments),
            pauses_after_ms[-1],
        )

    concatenate_audio_with_pauses(
        normalized_paths,
        pauses_after_ms,
        output_path,
        request_work_dir,
    )


def generate_minimax_tts(payload: TTSProxyRequest, output_path: Path) -> None:
    """按302.AI官方MiniMax Speech 2.8 HD格式生成试听音频。"""
    response = httpx.post(
        "https://api.302.ai/minimaxi/v1/t2a_v2",
        headers=ai302_headers(),
        json={
            "model": "speech-2.8-hd",
            "text": payload.text,
            "stream": False,
            "voice_setting": {
                "voice_id": payload.minimax_voice_id,
                "speed": payload.speed_ratio,
                "vol": 1,
                "pitch": 0,
            },
            "text_normalization": True,
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 2,
            },
            "subtitle_enable": False,
            "output_format": "url",
        },
        timeout=180,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_MINIMAX_TTS_HTTP_STATUS",
                "upstream": upstream_error_summary(exc.response),
            },
        ) from exc

    result = response.json()
    base_response = result.get("base_resp") or {}
    if int(base_response.get("status_code") or 0) != 0:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_MINIMAX_TTS_FAILED",
                "upstream": result,
            },
        )
    data = result.get("data") or {}
    audio_url = str(data.get("audio") or "").strip()
    if not audio_url.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_MINIMAX_TTS_AUDIO_URL_EMPTY",
                "upstream": result,
            },
        )
    download_audio(audio_url, output_path)


def generate_indextts2_tts(payload: TTSProxyRequest, output_path: Path) -> None:
    """按302.AI官方 IndexTTS-2 异步接口生成语音。"""
    if not INDEXTTS2_SPEAKER_AUDIO_URL.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "INDEXTTS2_SPEAKER_AUDIO_URL_MISSING",
                "error_message": (
                    "Railway尚未设置有效的INDEXTTS2_SPEAKER_AUDIO_URL"
                ),
            },
        )

    try:
        response = httpx.post(
            "https://api.302.ai/302/index_tts2/task",
            headers=ai302_headers(),
            json={
                "text": payload.text,
                "speaker_audio_url": INDEXTTS2_SPEAKER_AUDIO_URL,
            },
            timeout=60,
        )
        response.raise_for_status()
        result = response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_INDEXTTS2_CREATE_HTTP_STATUS",
                "upstream": upstream_error_summary(exc.response),
            },
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_INDEXTTS2_CREATE_FAILED",
                "error_message": str(exc),
            },
        ) from exc

    task_id = str(result.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_INDEXTTS2_TASK_ID_EMPTY",
                "upstream": result,
            },
        )

    max_checks = int(os.getenv("INDEXTTS2_MAX_POLLS", "150"))
    poll_interval = float(os.getenv("INDEXTTS2_POLL_INTERVAL_SEC", "2"))

    for _ in range(max_checks):
        try:
            response = httpx.get(
                "https://api.302.ai/302/index_tts2/task",
                params={"task_id": task_id},
                headers=ai302_headers(),
                timeout=30,
            )
            response.raise_for_status()
            task_result = response.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error_code": "302_INDEXTTS2_QUERY_HTTP_STATUS",
                    "upstream": upstream_error_summary(exc.response),
                },
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail={
                    "error_code": "302_INDEXTTS2_QUERY_FAILED",
                    "error_message": str(exc),
                },
            ) from exc

        state = str(task_result.get("state") or "").strip().upper()
        if state == "SUCCESS":
            audio_url = str(task_result.get("audio_url") or "").strip()
            if not audio_url.startswith(("https://", "http://")):
                raise HTTPException(
                    status_code=502,
                    detail={
                        "error_code": "302_INDEXTTS2_AUDIO_URL_EMPTY",
                        "upstream": task_result,
                    },
                )
            download_audio(audio_url, output_path)
            return

        if any(word in state for word in ("FAIL", "ERROR", "CANCEL")):
            raise HTTPException(
                status_code=502,
                detail={
                    "error_code": "302_INDEXTTS2_TASK_FAILED",
                    "upstream": task_result,
                },
            )

        time.sleep(poll_interval)

    raise HTTPException(
        status_code=504,
        detail={
            "error_code": "302_INDEXTTS2_TIMEOUT",
            "task_id": task_id,
        },
    )


def generate_glm_tts(payload: TTSProxyRequest, output_path: Path) -> None:
    """按302.AI官方 GLM-TTS URL返回格式生成 WAV。"""
    if len(payload.text) > 1024:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "GLM_TTS_TEXT_TOO_LONG",
                "error_message": "GLM-TTS单次输入不能超过1024字符",
            },
        )

    response = httpx.post(
        "https://api.302.ai/bigmodel/api/paas/v4/audio/speech",
        params={"output_format": "url"},
        headers=ai302_headers(),
        json={
            "model": "glm-tts",
            "input": payload.text,
            "voice": payload.glm_voice,
            "response_format": "wav",
            "speed": payload.speed_ratio,
            "volume": 1.0,
        },
        timeout=180,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_GLM_TTS_HTTP_STATUS",
                "upstream": upstream_error_summary(exc.response),
            },
        ) from exc

    try:
        result = response.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_GLM_TTS_INVALID_JSON",
                "error_message": str(exc),
            },
        ) from exc

    audio_url = str(result.get("url") or "").strip()
    if not audio_url.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_GLM_TTS_AUDIO_URL_EMPTY",
                "upstream": result,
            },
        )
    download_audio(audio_url, output_path)


def generate_qwen3_tts_segment(
    text: str,
    voice: str,
    output_path: Path,
) -> None:
    """按302.AI官方 Qwen3-TTS-Flash 示例生成单段语音。"""
    response = httpx.post(
        "https://api.302.ai/aliyun/api/v1/services/aigc/"
        "multimodal-generation/generation",
        headers=ai302_headers(),
        json={
            "model": "qwen3-tts-flash-2025-09-18",
            "input": {
                "text": text,
                "voice": voice,
            },
        },
        timeout=180,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_QWEN3_TTS_HTTP_STATUS",
                "upstream": upstream_error_summary(exc.response),
            },
        ) from exc

    try:
        result = response.json()
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_QWEN3_TTS_INVALID_JSON",
                "error_message": str(exc),
            },
        ) from exc

    audio_url = str(
        ((result.get("output") or {}).get("audio") or {}).get("url") or ""
    ).strip()
    if not audio_url.startswith(("https://", "http://")):
        raise HTTPException(
            status_code=502,
            detail={
                "error_code": "302_QWEN3_TTS_AUDIO_URL_EMPTY",
                "upstream": result,
            },
        )
    download_audio(audio_url, output_path)


def generate_qwen3_tts(payload: TTSProxyRequest, output_path: Path) -> None:
    """将长旁白切成 Qwen3 可接受的小段，逐段生成后合并。"""
    chunks = split_text_by_utf8_limit(
        payload.text,
        QWEN3_TTS_MAX_INPUT_BYTES,
    )
    if not chunks:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "QWEN3_TTS_TEXT_EMPTY"},
        )

    if len(chunks) == 1:
        generate_qwen3_tts_segment(
            chunks[0],
            payload.qwen3_voice,
            output_path,
        )
        return

    segment_dir = WORK_DIR / f"qwen3-{payload.request_id}-{uuid.uuid4()}"
    segment_dir.mkdir(parents=True, exist_ok=True)
    segment_paths: list[Path] = []
    try:
        for index, chunk in enumerate(chunks):
            segment_path = segment_dir / f"segment-{index:02d}.wav"
            generate_qwen3_tts_segment(
                chunk,
                payload.qwen3_voice,
                segment_path,
            )
            segment_paths.append(segment_path)
            logger.info(
                "QWEN3_TTS_SEGMENT_COMPLETED request_id=%s segment=%s/%s bytes=%s",
                payload.request_id,
                index + 1,
                len(chunks),
                len(chunk.encode("utf-8")),
            )
        concatenate_audio(segment_paths, output_path)
    finally:
        shutil.rmtree(segment_dir, ignore_errors=True)


def upstream_error_summary(response: httpx.Response) -> dict[str, Any]:
    try:
        body: Any = response.json()
    except Exception:
        body = response.text[:1000]

    return {
        "status_code": response.status_code,
        "body": body,
    }


SUBTITLE_MAX_CHARS = 58
SUBTITLE_MAX_WORDS = 13


def _subtitle_word(value: str) -> str:
    """把原文或WhisperX单词规整为可比较的英文/数字单元。"""
    return "".join(
        re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", str(value or "").lower())
    )


def _subtitle_chunks(original_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """按完整短句和可读长度切分原旁白，且不拆开数字或小数。"""
    tokens = re.findall(
        r"\d+(?:\.\d+)*|[A-Za-z]+(?:['’][A-Za-z]+)?|[\u4e00-\u9fff]+|\s+|[^\s]",
        original_text,
    )
    chunks: list[dict[str, Any]] = []
    source_words: list[str] = []
    current = ""
    current_start = 0
    current_words = 0
    join_next_without_space = False

    def is_word(token: str) -> bool:
        return bool(
            re.fullmatch(
                r"\d+(?:\.\d+)*|[A-Za-z]+(?:['’][A-Za-z]+)?|[\u4e00-\u9fff]+",
                token,
            )
        )

    def flush() -> None:
        nonlocal current, current_start, current_words
        text = current.strip()
        if text and current_words:
            chunks.append(
                {
                    "text": text,
                    "word_start": current_start,
                    "word_end": len(source_words) - 1,
                }
            )
        current = ""
        current_start = len(source_words)
        current_words = 0

    for raw_token in tokens:
        if raw_token.isspace():
            continue
        token = raw_token.strip()
        word = is_word(token)
        sentence_end = token in ("。", "！", "？", "；", ".", "!", "?", ";")
        comma_end = token in ("，", "、", ",", ":", "：")
        connector = token in ("-", "–", "—", "/")

        projected_length = len(current.rstrip()) + len(token) + (1 if current else 0)
        if (
            word
            and current_words
            and (
                projected_length > SUBTITLE_MAX_CHARS
                or current_words >= SUBTITLE_MAX_WORDS
            )
        ):
            flush()
            join_next_without_space = False

        if word:
            if current and not join_next_without_space:
                current += " "
            current += token
            source_words.append(token)
            current_words += 1
            join_next_without_space = False
        elif connector:
            current = current.rstrip() + token
            join_next_without_space = True
        else:
            current = current.rstrip() + token
            join_next_without_space = False

        if sentence_end and (
            len(current) >= 28 or current_words >= 7
        ):
            flush()
        elif comma_end and (
            len(current) >= 38 or current_words >= 9
        ):
            flush()

    flush()
    return chunks, source_words


def _whisper_word_timings(
    alignment_result: dict[str, Any],
) -> list[dict[str, Any]]:
    timings: list[dict[str, Any]] = []
    for segment in alignment_result.get("segments") or []:
        for word in segment.get("words") or []:
            start = word.get("start")
            end = word.get("end")
            text = _subtitle_word(word.get("word") or word.get("text") or "")
            if start is None or end is None or not text:
                continue
            try:
                start_sec = float(start)
                end_sec = float(end)
            except (TypeError, ValueError):
                continue
            if end_sec > start_sec:
                timings.append(
                    {"text": text, "start_sec": start_sec, "end_sec": end_sec}
                )
    return timings


def _match_source_words_to_alignment(
    source_words: list[str],
    timing_words: list[dict[str, Any]],
) -> dict[int, int]:
    """顺序匹配原文与识别词；数字识别差异会自动由邻近词插值处理。"""
    matches: dict[int, int] = {}
    cursor = 0
    for source_index, source_word in enumerate(source_words):
        normalized = _subtitle_word(source_word)
        if not normalized:
            continue
        match_index = None
        for candidate in range(cursor, min(cursor + 12, len(timing_words))):
            if timing_words[candidate]["text"] == normalized:
                match_index = candidate
                break
        if match_index is not None:
            matches[source_index] = match_index
            cursor = match_index + 1
    return matches


def _source_word_time(
    source_index: int,
    use_end: bool,
    source_word_count: int,
    matches: dict[int, int],
    timing_words: list[dict[str, Any]],
    speech_start: float,
    speech_end: float,
) -> float:
    """优先使用真实时间戳；未识别词仅在相邻锚点之间插值。"""
    timing_key = "end_sec" if use_end else "start_sec"
    direct = matches.get(source_index)
    if direct is not None:
        return float(timing_words[direct][timing_key])

    left = max((index for index in matches if index < source_index), default=None)
    right = min((index for index in matches if index > source_index), default=None)
    if left is not None and right is not None:
        left_time = float(timing_words[matches[left]]["end_sec"])
        right_time = float(timing_words[matches[right]]["start_sec"])
        ratio = (source_index - left) / max(right - left, 1)
        return left_time + (right_time - left_time) * ratio

    ratio = source_index / max(source_word_count - 1, 1)
    return speech_start + (speech_end - speech_start) * ratio


def build_subtitle_cues(
    alignment_result: dict[str, Any],
    audio_duration_sec: float,
    original_text: str,
) -> list[dict[str, Any]]:
    """
    WhisperX只负责提供真实语速和时间，字幕文字始终使用原始旁白。
    这样可以避免日期、K线数量和价格数字被语音识别遗漏。
    """
    original_text = re.sub(r"\s+", " ", str(original_text or "")).strip()

    if not original_text:
        raise RuntimeError("原始旁白为空")

    chunks, source_words = _subtitle_chunks(original_text)
    if not chunks or not source_words:
        raise RuntimeError("没有生成有效字幕文本")

    timing_words = _whisper_word_timings(alignment_result)
    if timing_words:
        speech_start = max(
            0.0,
            min(item["start_sec"] for item in timing_words),
        )
        speech_end = min(
            float(audio_duration_sec),
            max(item["end_sec"] for item in timing_words),
        )
    else:
        speech_start = 0.0
        speech_end = float(audio_duration_sec)

    if speech_end <= speech_start:
        speech_start = 0.0
        speech_end = float(audio_duration_sec)

    matches = _match_source_words_to_alignment(source_words, timing_words)
    cue_starts = [
        _source_word_time(
            chunk["word_start"],
            False,
            len(source_words),
            matches,
            timing_words,
            speech_start,
            speech_end,
        )
        for chunk in chunks
    ]
    cues: list[dict[str, Any]] = []
    previous_start = speech_start
    for index, chunk in enumerate(chunks):
        cue_start = max(previous_start, cue_starts[index])
        if index < len(chunks) - 1:
            # 字幕延续到下一句真正开口，停顿期间不闪空白。
            cue_end = max(cue_start + 0.2, cue_starts[index + 1])
        else:
            cue_end = speech_end
        cue_end = min(cue_end, float(audio_duration_sec))
        if cue_end <= cue_start:
            cue_end = min(float(audio_duration_sec), cue_start + 0.2)

        cues.append(
            {
                "start_sec": round(cue_start, 3),
                "end_sec": round(cue_end, 3),
                "text": chunk["text"],
            }
        )
        previous_start = cue_start

    return cues


def align_audio_with_whisperx(
    audio_path: Path,
    audio_duration_sec: float,
    original_text: str,
) -> list[dict[str, Any]]:
    """
    上传已经生成的音频，让302.AI WhisperX返回真实语音时间戳。
    """
    try:
        with audio_path.open("rb") as audio_file:
            response = httpx.post(
                "https://api.302.ai/302/whisperx",
                headers={
                    "Authorization": f"Bearer {AI302_API_KEY}",
                    "Accept": "application/json",
                },
                files={
                    "audio_input": (
                        audio_path.name,
                        audio_file,
                        "audio/wav" if audio_path.suffix == ".wav" else "audio/mpeg",
                    )
                },
                data={
                    "language": "zh",
                    "processing_type": "align",
                    "translate": "false",
                    "output": "text",
                },
                timeout=300,
            )

        response.raise_for_status()
        result = response.json()

    except Exception as exc:
        raise RuntimeError(
            f"WHISPERX_REQUEST_FAILED: {exc}"
        ) from exc

    if result.get("error"):
        raise RuntimeError(
            f"WHISPERX_ALIGNMENT_FAILED: {result['error']}"
        )

    subtitle_cues = build_subtitle_cues(
        result,
        audio_duration_sec,
        original_text,
    )

    if not subtitle_cues:
        raise RuntimeError(
            "WHISPERX_NO_SUBTITLE_CUES: 没有识别到有效字幕时间"
        )

    return subtitle_cues
    
@app.post("/v1/tts", dependencies=[Depends(require_token)])
def create_tts_audio(payload: TTSProxyRequest) -> dict[str, Any]:
    if not AI302_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="服务器尚未设置AI302_API_KEY",
        )

    audio_format = (
        "mp3"
        if payload.tts_provider in {"openai", "elevenlabs", "minimax"}
        else "wav"
    )
    audio_name = f"tts-{uuid.uuid4()}.{audio_format}"
    audio_path = MEDIA_DIR / audio_name
    request_work_dir = WORK_DIR / f"tts-{payload.request_id}-{uuid.uuid4()}"
    request_work_dir.mkdir(parents=True, exist_ok=True)

    try:
        if payload.tts_provider == "openai":
            generate_openai_tts(payload, audio_path)
        elif payload.tts_provider == "elevenlabs":
            generate_elevenlabs_segmented_tts(
                payload,
                audio_path,
                request_work_dir,
            )
        elif payload.tts_provider == "minimax":
            generate_minimax_tts(payload, audio_path)
        elif payload.tts_provider == "indextts2":
            generate_indextts2_tts(payload, audio_path)
        elif payload.tts_provider == "glm_tts":
            generate_glm_tts(payload, audio_path)
        elif payload.tts_provider == "qwen3_tts":
            generate_qwen3_tts(payload, audio_path)
        else:
            segments = parse_narration_segments(payload)
            segment_paths: list[Path] = []
            submitted_tasks: list[tuple[str, str]] = []

            for segment_text in segments:
                emotion = (
                    analyze_dubbingx_emotion(segment_text)
                    if payload.emotion_mode == "auto"
                    else "常规-日常说话-3"
                )
                task_id = submit_dubbingx_tts(
                    text=segment_text,
                    voice_id=payload.voice_id,
                    emotion=emotion,
                    speed_ratio=payload.speed_ratio,
                )
                submitted_tasks.append((task_id, emotion))

            for index, (task_id, emotion) in enumerate(submitted_tasks):
                upstream_audio_url = wait_for_dubbingx_tts(task_id)
                segment_path = request_work_dir / f"segment-{index:02d}.wav"
                download_audio(upstream_audio_url, segment_path)
                segment_paths.append(segment_path)
                logger.info(
                    "DUBBINGX_SEGMENT_COMPLETED request_id=%s segment=%s emotion=%s",
                    payload.request_id,
                    index + 1,
                    emotion,
                )

            concatenate_audio(segment_paths, audio_path)
    except Exception as exc:
        logger.exception(
            "302_TTS_GENERATION_FAILED request_id=%s provider=%s",
            payload.request_id,
            payload.tts_provider,
        )
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=502,
            detail=f"302_TTS_GENERATION_FAILED: {exc}",
        ) from exc
    finally:
        shutil.rmtree(request_work_dir, ignore_errors=True)

    duration_sec = round(
        probe_duration(audio_path),
        3,
    )

    try:
        subtitle_cues = align_audio_with_whisperx(
            audio_path,
            duration_sec,
            payload.text,
        )
    except Exception as exc:
        logger.exception(
            "WHISPERX_ALIGNMENT_FAILED request_id=%s voice_id=%s audio=%s",
            payload.request_id,
            payload.voice_id,
            audio_name,
        )
        raise HTTPException(
            status_code=502,
            detail=str(exc),
        ) from exc

    return {
        "status": "completed",
        "audio_url": f"{PUBLIC_BASE_URL}/media/{audio_name}",
        "duration_sec": duration_sec,
        "subtitle_cues": subtitle_cues,
        "subtitle_count": len(subtitle_cues),
        "alignment_method": (
            "openai_tts_whisperx_bounds"
            if payload.tts_provider == "openai"
            else (
                "elevenlabs_v3_whisperx_bounds"
                if payload.tts_provider == "elevenlabs"
                else (
                    "minimax_speech_2_8_hd_whisperx_bounds"
                    if payload.tts_provider == "minimax"
                    else (
                        "indextts2_whisperx_bounds"
                        if payload.tts_provider == "indextts2"
                        else (
                            "glm_tts_whisperx_bounds"
                            if payload.tts_provider == "glm_tts"
                            else (
                                "qwen3_tts_whisperx_bounds"
                                if payload.tts_provider == "qwen3_tts"
                                else "dubbingx_emotion_segments_whisperx_bounds"
                            )
                        )
                    )
                )
            )
        ),
        "format": audio_format,
        "error_code": "",
        "error_message": "",
    }


def run_tts_job(job_id: str, payload: dict[str, Any]) -> None:
    try:
        update_tts_job(job_id, status="processing", progress=10)
        result = create_tts_audio(TTSProxyRequest.model_validate(payload))
        update_tts_job(
            job_id,
            status="completed",
            progress=100,
            **{key: value for key, value in result.items() if key != "status"},
        )
    except HTTPException as exc:
        detail = exc.detail
        error_code = "TTS_HTTP_ERROR"
        if isinstance(detail, dict):
            error_code = str(detail.get("error_code") or error_code)
            error_message = json.dumps(detail, ensure_ascii=False)
        else:
            error_message = str(detail)
        update_tts_job(
            job_id,
            status="failed",
            progress=100,
            error_code=error_code,
            error_message=error_message,
        )
    except Exception as exc:
        logger.exception("TTS_JOB_FAILED job_id=%s", job_id)
        update_tts_job(
            job_id,
            status="failed",
            progress=100,
            error_code=type(exc).__name__.upper(),
            error_message=str(exc),
        )


@app.post("/v1/tts-jobs", status_code=202, dependencies=[Depends(require_token)])
def create_tts_job(payload: TTSProxyRequest) -> dict[str, Any]:
    if not AI302_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="服务器尚未设置AI302_API_KEY",
        )

    job_id = str(uuid.uuid4())
    status_url = f"{PUBLIC_BASE_URL}/v1/tts-jobs/{job_id}"
    job = {
        "job_id": job_id,
        "request_id": payload.request_id,
        "status": "queued",
        "progress": 0,
        "status_url": status_url,
        "audio_url": "",
        "duration_sec": 0,
        "subtitle_cues": [],
        "subtitle_count": 0,
        "alignment_method": "",
        "format": "",
        "error_code": "",
        "error_message": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with LOCK:
        TTS_JOBS[job_id] = job

    threading.Thread(
        target=run_tts_job,
        args=(job_id, payload.model_dump()),
        daemon=True,
    ).start()
    return job


@app.get("/v1/tts-jobs/{job_id}", dependencies=[Depends(require_token)])
def get_tts_job(job_id: str) -> dict[str, Any]:
    with LOCK:
        job = TTS_JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "TTS_JOB_NOT_FOUND")
        return dict(job)

@app.post("/v1/render-jobs", status_code=202, dependencies=[Depends(require_token)])
def create_render_job(payload: RenderRequest) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    status_url = f"{PUBLIC_BASE_URL}/v1/render-jobs/{job_id}"
    job = {
        "job_id": job_id,
        "request_id": payload.request_id,
        "status": "queued",
        "progress": 0,
        "status_url": status_url,
        "video_url": "",
        "thumbnail_url": "",
        "duration_sec": 0,
        "error_code": "",
        "error_message": "",
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }
    with LOCK:
        JOBS[job_id] = job
    threading.Thread(
        target=render_job,
        args=(job_id, payload.model_dump()),
        daemon=True,
    ).start()
    return job


@app.get("/v1/render-jobs/{job_id}", dependencies=[Depends(require_token)])
def get_render_job(job_id: str) -> dict[str, Any]:
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "JOB_NOT_FOUND")
        return dict(job)


@app.post("/v1/test-video", dependencies=[Depends(require_token)])
def create_single_test_video(payload: RenderRequest) -> dict[str, Any]:
    """同步生成一条短测试视频；不创建任务、不轮询、不拼接视频片段。"""
    try:
        return render_single_test_video(payload.model_dump())
    except Exception as exc:
        print(f"TEST_VIDEO_ERROR: {type(exc).__name__}: {exc}", flush=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error_code": type(exc).__name__.upper(),
                "error_message": str(exc),
            },
        ) from exc


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = list(FONT_PATHS)
    if bold:
        candidates.insert(0, "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def text_lines(draw: ImageDraw.ImageDraw, text: str, face, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in str(text):
        trial = current + char
        if current and draw.textbbox((0, 0), trial, font=face)[2] > max_width:
            lines.append(current)
            current = char
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def price_label(value: float) -> str:
    return f"{value:,.2f}"


def find_scenario(analysis: dict[str, Any], name: str) -> dict[str, Any]:
    for scenario in analysis.get("scenarios") or []:
        if scenario.get("name") == name:
            return scenario
    scenarios = analysis.get("scenarios") or []
    return scenarios[0] if scenarios else {"candles": [], "probability": 0}


def draw_dashed_line(draw, xy, fill, width=3, dash=12):
    x1, y1, x2, y2 = xy
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= 0:
        return
    for start in range(0, int(length), dash * 2):
        end = min(start + dash, length)
        sx = x1 + (x2 - x1) * start / length
        sy = y1 + (y2 - y1) * start / length
        ex = x1 + (x2 - x1) * end / length
        ey = y1 + (y2 - y1) * end / length
        draw.line((sx, sy, ex, ey), fill=fill, width=width)


def render_scene(
    path: Path,
    payload: dict[str, Any],
    scene_index: int,
    total_scenes: int,
    current_time_sec: float | None = None,
) -> None:
    width = int(payload["video"]["width"])
    height = int(payload["video"]["height"])
    image = Image.new("RGB", (width, height), "#05070d")
    draw = ImageDraw.Draw(image)
    analysis = payload["analysis_forecast"]
    history = payload["historical_candles"]
    scenario = find_scenario(analysis, payload["style"]["scenario"])
    forecast_all = scenario.get("candles") or []

    minimum_history = min(20, len(history))
    history_count = min(
        len(history),
        minimum_history
        + round((len(history) - minimum_history) * min(scene_index, 6) / 6),
    )
    visible_history = history[-max(history_count, 1):]
    forecast_count = 0 if scene_index < 7 else min(6, (scene_index - 6) * 2)
    visible_forecast = forecast_all[:forecast_count]
    candles = visible_history + visible_forecast

    title_font = font(54, True)
    meta_font = font(29)
    label_font = font(25)
    small_font = font(22)
    subtitle_font = font(35, True)
    draw.text((60, 55), f"{payload['symbol']} 黄金K线分析", font=title_font, fill="#f4d06f")
    draw.text(
        (60, 125),
        f"周期 {payload['timeframe']}  ·  数据截止 {payload['data_as_of']}",
        font=meta_font,
        fill="#aab3c5",
    )
    trend = str(analysis.get("trend") or "mixed")
    probability = float(scenario.get("probability") or 0) * 100
    draw.rounded_rectangle((60, 180, 1020, 265), 22, fill="#111827")
    draw.text(
        (86, 200),
        f"历史走势：{trend}    基准情景概率：{probability:.0f}%",
        font=meta_font,
        fill="#ffffff",
    )

    chart = (70, 315, 1010, 1240)
    left, top, right, bottom = chart
    volume_top = 1070
    price_bottom = 1025
    draw.rounded_rectangle(chart, 18, fill="#0b1020", outline="#283249", width=2)

    all_prices = [
        float(c[key])
        for c in candles
        for key in ("high", "low")
    ]
    for zone_key in ("potential_buy_zones", "potential_sell_zones"):
        for zone in analysis.get(zone_key) or []:
            all_prices.extend([float(zone["low"]), float(zone["high"])])
    if not all_prices:
        all_prices = [0, 1]
    pmin, pmax = min(all_prices), max(all_prices)
    padding = max((pmax - pmin) * 0.08, 0.01)
    pmin -= padding
    pmax += padding

    def py(value: float) -> float:
        return price_bottom - (float(value) - pmin) / (pmax - pmin) * (price_bottom - top - 30)

    for grid_i in range(6):
        y = top + 30 + grid_i * (price_bottom - top - 30) / 5
        value = pmax - grid_i * (pmax - pmin) / 5
        draw.line((left + 10, y, right - 10, y), fill="#202a3b", width=1)
        draw.text((right - 145, y - 28), price_label(value), font=small_font, fill="#77839a")

    if payload["style"]["show_observation_zones"]:
        for key, color, name in (
            ("potential_buy_zones", "#153f35", "潜在买入观察区"),
            ("potential_sell_zones", "#4a2430", "潜在卖出观察区"),
        ):
            for zone in analysis.get(key) or []:
                y1, y2 = py(zone["high"]), py(zone["low"])
                draw.rectangle((left + 10, y1, right - 10, y2), fill=color)
                draw.text((left + 20, y1 + 4), name, font=small_font, fill="#e8eef8")

    if payload["style"]["show_support_resistance"]:
        for levels, color, prefix in (
            (analysis.get("support_levels") or [], "#39c6a3", "支撑"),
            (analysis.get("resistance_levels") or [], "#ff6b81", "压力"),
        ):
            for level in levels:
                y = py(level)
                draw_dashed_line(draw, (left + 10, y, right - 10, y), color, 2, 10)
                draw.text(
                    (left + 20, y - 25),
                    f"{prefix} {price_label(level)}",
                    font=small_font,
                    fill=color,
                )

    count = max(len(candles), 1)
    slot = (right - left - 45) / count
    body_width = max(4, min(18, int(slot * 0.55)))
    volumes = [float(c.get("volume") or 0) for c in candles]
    vmax = max(volumes or [1]) or 1
    for index, candle in enumerate(candles):
        x = left + 24 + slot * (index + 0.5)
        is_forecast = index >= len(visible_history)
        rising = float(candle["close"]) >= float(candle["open"])
        color = "#33d69f" if rising else "#ff5d73"
        if is_forecast:
            color = "#64a8ff"
        draw.line(
            (x, py(candle["high"]), x, py(candle["low"])),
            fill=color,
            width=3 if not is_forecast else 2,
        )
        y_open, y_close = py(candle["open"]), py(candle["close"])
        y1, y2 = min(y_open, y_close), max(y_open, y_close)
        if y2 - y1 < 3:
            y2 = y1 + 3
        if is_forecast:
            draw.rectangle(
                (x - body_width / 2, y1, x + body_width / 2, y2),
                outline=color,
                width=3,
            )
        else:
            draw.rectangle(
                (x - body_width / 2, y1, x + body_width / 2, y2),
                fill=color,
            )
        volume = float(candle.get("volume") or 0)
        vh = (bottom - volume_top - 25) * volume / vmax
        draw.rectangle(
            (x - body_width / 2, bottom - 18 - vh, x + body_width / 2, bottom - 18),
            fill="#35506b" if is_forecast else color,
        )

    draw.text((80, 1258), "绿色/红色：历史K线    蓝色空心：模型预测K线", font=label_font, fill="#b9c3d4")
    summary = str(analysis.get("market_summary") or "")
    summary_lines = text_lines(draw, summary, label_font, 900)[:3]
    draw.rounded_rectangle((60, 1310, 1020, 1510), 18, fill="#101827")
    draw.text((85, 1330), "模型分析", font=meta_font, fill="#f4d06f")
    for line_no, line in enumerate(summary_lines):
        draw.text((85, 1380 + line_no * 36), line, font=label_font, fill="#e7edf7")

    subtitle_cues = payload["narration"].get("subtitle_cues") or []

    if current_time_sec is None:
        timeline_duration = float(
            payload.get("render_duration_sec")
            or payload.get("duration_target_sec")
            or payload.get("test_duration_sec")
            or 10
        )
        current_time = (
            scene_index
            / max(total_scenes - 1, 1)
            * timeline_duration
        )
    else:
        current_time = float(current_time_sec)

    subtitle = ""

    for cue in subtitle_cues:
        start_sec = float(cue.get("start_sec") or 0)
        end_sec = float(cue.get("end_sec") or 0)

        if start_sec <= current_time < end_sec:
            subtitle = str(cue.get("text") or "")
            break

    if not subtitle:
        segments = sorted(
            payload["narration"].get("segments") or [],
            key=lambda item: item.get("order", 0),
        )

        if segments:
            segment = segments[
                min(scene_index, len(segments) - 1)
            ]
            subtitle = str(segment.get("text") or "")
        else:
            subtitle = str(
                payload["narration"].get("full_text") or ""
            )

    subtitle_lines = text_lines(draw, subtitle, subtitle_font, 900)[:5]
    panel_top = 1540
    draw.rounded_rectangle((60, panel_top, 1020, 1810), 24, fill="#111827", outline="#293449")
    for line_no, line in enumerate(subtitle_lines):
        bbox = draw.textbbox((0, 0), line, font=subtitle_font)
        x = (width - (bbox[2] - bbox[0])) / 2
        draw.text((x, panel_top + 30 + line_no * 48), line, font=subtitle_font, fill="#ffffff")

    draw.text(
        (60, 1840),
        "AI辅助分析 · 预测可能完全错误 · 仅供研究教育 · 不构成投资建议",
        font=small_font,
        fill="#9ba7ba",
    )
    image.save(path, "PNG")


def run_command(args: list[str]) -> None:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr[-4000:] or "FFmpeg执行失败")


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"无法读取音频时长: {result.stderr[-1000:]}")
    return float(result.stdout.strip())


def download_audio(url: str, target: Path) -> None:
    downloaded = 0
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type:
            raise RuntimeError("audio_url返回了HTML，不是音频")
        with target.open("wb") as output:
            for chunk in response.iter_bytes():
                downloaded += len(chunk)
                if downloaded > MAX_AUDIO_BYTES:
                    raise RuntimeError("音频文件超过大小限制")
                output.write(chunk)


def build_scene_intervals(
    payload: dict[str, Any],
    duration: float,
) -> list[tuple[float, float]]:
    """
    按字幕真实起止时间生成画面区间。
    每条字幕对应一个画面区间，避免固定10张画面造成字幕切换滞后。
    """
    duration = max(0.1, float(duration))
    raw_cues = payload.get("narration", {}).get("subtitle_cues") or []
    cues: list[tuple[float, float]] = []

    for cue in raw_cues:
        start = max(0.0, float(cue.get("start_sec") or 0))
        end = min(duration, float(cue.get("end_sec") or 0))

        if end > start:
            cues.append((start, end))

    cues.sort(key=lambda item: item[0])

    if not cues:
        scene_count = 10
        seconds_per_scene = duration / scene_count
        return [
            (
                index * seconds_per_scene,
                (index + 1) * seconds_per_scene,
            )
            for index in range(scene_count)
        ]

    intervals: list[tuple[float, float]] = []
    cursor = 0.0

    for start, end in cues:
        start = max(cursor, start)

        if start > cursor:
            intervals.append((cursor, start))

        if end > start:
            intervals.append((start, end))
            cursor = end

    if cursor < duration:
        intervals.append((cursor, duration))

    # The TradingView theme animates candles against narration. Subdivide long
    # subtitle intervals so the renderer receives a new visual state every
    # fifth-second instead of holding one still image for an entire sentence.
    if payload.get("style", {}).get("theme") == "light_tradingview":
        animated_intervals: list[tuple[float, float]] = []
        step_seconds = 0.2

        for start, end in intervals:
            frame_start = start

            while frame_start < end:
                frame_end = min(end, frame_start + step_seconds)
                animated_intervals.append((frame_start, frame_end))
                frame_start = frame_end

        return animated_intervals

    return intervals


def render_job(job_id: str, payload: dict[str, Any]) -> None:
    job_dir = WORK_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        update_job(job_id, status="rendering", progress=5)
        audio_path = job_dir / "narration.mp3"
        download_audio(payload["audio_url"], audio_path)
        duration = probe_duration(audio_path)
        if duration < 20 or duration > 150:
            raise RuntimeError(f"音频时长{duration:.1f}秒，不在20到150秒安全范围")
        payload["render_duration_sec"] = duration
        update_job(job_id, progress=15)

        scene_intervals = build_scene_intervals(payload, duration)
        scene_count = len(scene_intervals)
        scene_renderer = (
            render_tradingview_scene
            if payload["style"].get("theme") == "light_tradingview"
            else render_scene
        )
        scene_paths = []
        for index, (start_sec, end_sec) in enumerate(scene_intervals):
            scene_path = job_dir / f"scene-{index:02}.png"
            scene_renderer(
                scene_path,
                payload,
                index,
                scene_count,
                current_time_sec=(start_sec + end_sec) / 2,
            )
            scene_paths.append(scene_path)
            update_job(job_id, progress=15 + int((index + 1) / scene_count * 50))

        thumbnail_name = f"{job_id}-thumbnail.png"
        thumbnail_path = MEDIA_DIR / thumbnail_name
        thumbnail_path.write_bytes(scene_paths[-1].read_bytes())

        concat_path = job_dir / "scenes.txt"
        rows = []
        for scene_path, (start_sec, end_sec) in zip(
            scene_paths,
            scene_intervals,
        ):
            rows.append(f"file '{scene_path.resolve()}'")
            rows.append(
                f"duration {max(0.001, end_sec - start_sec):.6f}"
            )
        rows.append(f"file '{scene_paths[-1].resolve()}'")
        concat_path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        output_name = f"{job_id}-tiktok.mp4"
        output_path = MEDIA_DIR / output_name
        update_job(job_id, progress=70)
        run_command(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-i",
                str(audio_path),
                "-t",
                f"{duration:.3f}",
                "-vf",
                f"fps={payload['video']['fps']},format=yuv420p",
                "-c:v",
                "libx264",
                "-threads",
                "2",
                "-preset",
                "ultrafast",
                "-crf",
                "24",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-ar:a",
                "48000",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        actual_duration = round(probe_duration(output_path), 3)
        update_job(
            job_id,
            status="completed",
            progress=100,
            video_url=f"{PUBLIC_BASE_URL}/media/{output_name}",
            thumbnail_url=f"{PUBLIC_BASE_URL}/media/{thumbnail_name}",
            duration_sec=actual_duration,
        )
    except Exception as exc:
        update_job(
            job_id,
            status="failed",
            progress=100,
            error_code=type(exc).__name__.upper(),
            error_message=str(exc),
        )


def render_single_test_video(payload: dict[str, Any]) -> dict[str, Any]:
    """
    生成一个连续 MP4：
    Pillow 逐帧画图 -> FFmpeg 直接编码帧序列 -> 加入语音前若干秒。
    这里没有生成多个视频片段，也没有 concat。
    """
    render_id = str(uuid.uuid4())
    work_dir = WORK_DIR / f"test-{render_id}"
    work_dir.mkdir(parents=True, exist_ok=True)
    audio_url = str(payload.get("audio_url") or "")
    audio_path = work_dir / "narration.mp3"
    duration = float(payload.get("test_duration_sec") or 10)
    if audio_url:
        download_audio(audio_url, audio_path)
        audio_duration = probe_duration(audio_path)
        duration = min(duration, audio_duration)
        if duration < 5:
            raise RuntimeError(f"语音只有{audio_duration:.1f}秒，测试视频至少需要5秒")
    payload["render_duration_sec"] = duration

    scene_intervals = build_scene_intervals(payload, duration)
    frame_count = len(scene_intervals)
    scene_renderer = (
        render_tradingview_scene
        if payload["style"].get("theme") == "light_tradingview"
        else render_scene
    )
    for index, (start_sec, end_sec) in enumerate(scene_intervals):
        scene_renderer(
            work_dir / f"frame-{index:03}.png",
            payload,
            index,
            frame_count,
            current_time_sec=(start_sec + end_sec) / 2,
        )

    thumbnail_name = f"test-{render_id}-thumbnail.png"
    thumbnail_path = MEDIA_DIR / thumbnail_name
    thumbnail_path.write_bytes((work_dir / f"frame-{frame_count - 1:03}.png").read_bytes())
    output_name = f"test-{render_id}.mp4"
    output_path = MEDIA_DIR / output_name
    concat_path = work_dir / "scenes.txt"
    rows = []
    for index, (start_sec, end_sec) in enumerate(scene_intervals):
        frame_path = work_dir / f"frame-{index:03}.png"
        rows.append(f"file '{frame_path.resolve()}'")
        rows.append(
            f"duration {max(0.001, end_sec - start_sec):.6f}"
        )
    rows.append(
        f"file '{(work_dir / f'frame-{frame_count - 1:03}.png').resolve()}'"
    )
    concat_path.write_text(
        "\n".join(rows) + "\n",
        encoding="utf-8",
    )

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
    ]
    if audio_url:
        command.extend(["-i", str(audio_path)])
    command.extend(
        [
            "-t",
            f"{duration:.3f}",
            "-vf",
            f"fps={payload['video']['fps']},format=yuv420p",
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-preset",
            "ultrafast",
            "-crf",
            "24",
        ]
    )
    if audio_url:
        command.extend(
            [
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-filter:a",
                "loudnorm=I=-16:LRA=11:TP=-1.5",
            ]
        )
    command.extend(["-movflags", "+faststart", str(output_path)])
    run_command(command)
    actual_duration = round(probe_duration(output_path), 3)
    return {
        "status": "completed",
        "test_mode": True,
        "has_audio": bool(audio_url),
        "render_id": render_id,
        "video_url": f"{PUBLIC_BASE_URL}/media/{output_name}",
        "thumbnail_url": f"{PUBLIC_BASE_URL}/media/{thumbnail_name}",
        "duration_sec": actual_duration,
        "error_code": "",
        "error_message": "",
    }
