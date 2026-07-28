import json
import math
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator


DATA_DIR = Path(os.getenv("DATA_DIR", "/tmp/gold-video"))
MEDIA_DIR = DATA_DIR / "media"
WORK_DIR = DATA_DIR / "work"
MEDIA_DIR.mkdir(parents=True, exist_ok=True)
WORK_DIR.mkdir(parents=True, exist_ok=True)
TOKEN = os.getenv("RENDER_SERVICE_TOKEN", "change-me")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
MAX_AUDIO_BYTES = int(os.getenv("MAX_AUDIO_MB", "30")) * 1024 * 1024
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
    scenario: Literal["base", "bull", "bear"] = "base"
    show_volume: bool = True
    show_support_resistance: bool = True
    show_observation_zones: bool = True
    show_subtitles: bool = True


class RenderRequest(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    symbol: str = "XAUUSD"
    timeframe: str
    data_as_of: str
    duration_target_sec: int = Field(default=90, ge=60, le=120)
    test_duration_sec: int = Field(default=10, ge=5, le=20)
    historical_candles: list[Candle] = Field(min_length=20, max_length=500)
    analysis_forecast: dict[str, Any]
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "time": now_iso()}


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

if payload.get("audio_url"):
    timeline_duration = float(payload.get("duration_target_sec") or 90)
else:
    timeline_duration = float(payload.get("test_duration_sec") or 10)

current_time = (
    scene_index
    / max(total_scenes - 1, 1)
    * timeline_duration
)

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
        key=lambda item: item.get("order", 0)
    )

    if segments:
        segment = segments[min(scene_index, len(segments) - 1)]
        subtitle = str(segment.get("text") or "")
    else:
        subtitle = str(payload["narration"].get("full_text") or "")
        
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
        update_job(job_id, progress=15)

        scene_count = 10
        scene_paths = []
        for index in range(scene_count):
            scene_path = job_dir / f"scene-{index:02}.png"
            render_scene(scene_path, payload, index, scene_count)
            scene_paths.append(scene_path)
            update_job(job_id, progress=15 + int((index + 1) / scene_count * 50))

        thumbnail_name = f"{job_id}-thumbnail.png"
        thumbnail_path = MEDIA_DIR / thumbnail_name
        thumbnail_path.write_bytes(scene_paths[-1].read_bytes())

        seconds_per_scene = duration / scene_count
        concat_path = job_dir / "scenes.txt"
        rows = []
        for scene_path in scene_paths:
            rows.append(f"file '{scene_path.resolve()}'")
            rows.append(f"duration {seconds_per_scene:.6f}")
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
                "-vf",
                f"fps={payload['video']['fps']},format=yuv420p",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "21",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-filter:a",
                "loudnorm=I=-16:LRA=11:TP=-1.5",
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

    # 每秒一个新的K线画面，FFmpeg直接把帧序列编码成一条视频。
    frame_count = max(5, math.ceil(duration))
    for index in range(frame_count):
        render_scene(
            work_dir / f"frame-{index:03}.png",
            payload,
            round(index / max(frame_count - 1, 1) * 9),
            10,
        )

    thumbnail_name = f"test-{render_id}-thumbnail.png"
    thumbnail_path = MEDIA_DIR / thumbnail_name
    thumbnail_path.write_bytes((work_dir / f"frame-{frame_count - 1:03}.png").read_bytes())
    output_name = f"test-{render_id}.mp4"
    output_path = MEDIA_DIR / output_name
    input_fps = frame_count / duration
    command = [
        "ffmpeg",
        "-y",
        "-framerate",
        f"{input_fps:.6f}",
        "-i",
        str(work_dir / "frame-%03d.png"),
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
