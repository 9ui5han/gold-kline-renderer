"""Backend-owned TTS profiles and pre-paid-call validation."""

from __future__ import annotations

import copy
import json
import os
import re
from typing import Any

import httpx


AI302_BASE_URL = "https://api.302.ai"
PROFILE_SCHEMA_VERSION = "tts-profile-catalog-v1"
PERFORMANCE_SCHEMA_VERSION = "tts-performance-v1"


class ProfileError(ValueError):
    """Raised before a paid TTS request when a profile contract is invalid."""


_DEFAULT_PROFILES: tuple[dict[str, Any], ...] = (
    {"profile_id": "mm_finance_male_01", "provider": "minimax", "voice_id": "English_Trustworthy_Man", "display_name": "MiniMax Trustworthy Man", "language": "en", "gender": "male", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "calm", "status": "documented", "source": "minimax-system-voice-list"},
    {"profile_id": "mm_finance_male_02", "provider": "minimax", "voice_id": "English_Diligent_Man", "display_name": "MiniMax Diligent Man", "language": "en", "gender": "male", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "calm", "status": "verified", "source": "minimax-system-voice-list"},
    {"profile_id": "mm_finance_male_03", "provider": "minimax", "voice_id": "English_Gentle-voiced_man", "display_name": "MiniMax Gentle-voiced Man", "language": "en", "gender": "male", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "calm", "status": "documented", "source": "minimax-system-voice-list"},
    {"profile_id": "mm_finance_female_01", "provider": "minimax", "voice_id": "English_Graceful_Lady", "display_name": "MiniMax Graceful Lady", "language": "en", "gender": "female", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "calm", "status": "documented", "source": "minimax-system-voice-list"},
    {"profile_id": "mm_cn_radio_host", "provider": "minimax", "voice_id": "Chinese (Mandarin)_Radio_Host", "display_name": "MiniMax 电台男主播", "language": "zh", "gender": "male", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "calm", "status": "verified", "source": "minimax-system-voice-list"},
    {"profile_id": "mm_cn_reliable_exec", "provider": "minimax", "voice_id": "Chinese (Mandarin)_Reliable_Executive", "display_name": "MiniMax 沉稳高管", "language": "zh", "gender": "male", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "calm", "status": "verified", "source": "minimax-system-voice-list"},
    {"profile_id": "el_finance_male_01", "provider": "elevenlabs", "voice_id": "JBFqnCBsd6RMkjVDRZzb", "display_name": "ElevenLabs documented male candidate", "language": "en", "gender": "male", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "natural", "status": "verified", "source": "302-elevenlabs-tts-example"},
    {"profile_id": "el_finance_female_01", "provider": "elevenlabs", "voice_id": "21m00Tcm4TlvDq8ikWAM", "display_name": "ElevenLabs Rachel", "language": "en", "gender": "female", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "natural", "status": "verified", "source": "302-elevenlabs-voices-example"},
    {"profile_id": "el_finance_female_02", "provider": "elevenlabs", "voice_id": "EXAVITQu4vr4xnSDxMaL", "display_name": "ElevenLabs documented female candidate", "language": "en", "gender": "female", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "natural", "status": "verified", "source": "302-elevenlabs-voices-example"},
    {"profile_id": "dx_official_30002", "provider": "dubbingx", "voice_id": "30002", "display_name": "DubbingX 智吾褚", "language": "zh", "gender": "male", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "auto", "status": "verified", "source": "dubbingx-voice-list-example"},
    {"profile_id": "dx_documented_30065", "provider": "dubbingx", "voice_id": "30065", "display_name": "DubbingX documented example 30065", "language": "zh", "gender": "unknown", "default_speed": 1.0, "speed_min": 0.90, "speed_max": 1.05, "default_emotion": "auto", "status": "verified", "source": "302-dubbingx-tts-example"},
)


def _validate_profile(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ProfileError("PROFILE_OBJECT_INVALID")
    profile = copy.deepcopy(item)
    profile_id = str(profile.get("profile_id") or "").strip()
    provider = str(profile.get("provider") or "").strip().lower()
    voice_id = str(profile.get("voice_id") or "").strip()
    status = str(profile.get("status") or "documented").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,49}", profile_id):
        raise ProfileError("PROFILE_ID_INVALID")
    if provider not in {"minimax", "dubbingx", "elevenlabs"}:
        raise ProfileError("PROFILE_PROVIDER_INVALID")
    if not voice_id:
        raise ProfileError("PROFILE_VOICE_ID_EMPTY")
    if status not in {"documented", "verified", "unavailable"}:
        raise ProfileError("PROFILE_STATUS_INVALID")
    profile.update(profile_id=profile_id, provider=provider, voice_id=voice_id, status=status)
    return profile


def build_profile_catalog(config_json: str | None = None) -> dict[str, dict[str, Any]]:
    catalog = {item["profile_id"]: _validate_profile(item) for item in _DEFAULT_PROFILES}
    raw = os.getenv("TTS_PROFILE_CATALOG_JSON", "").strip() if config_json is None else config_json
    if raw:
        try:
            configured = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProfileError("PROFILE_CATALOG_JSON_INVALID") from exc
        if not isinstance(configured, list):
            raise ProfileError("PROFILE_CATALOG_MUST_BE_ARRAY")
        for item in configured:
            profile = _validate_profile(item)
            catalog[profile["profile_id"]] = profile
    return catalog


def resolve_profile(profile_id: str, *, allow_documented: bool = False, catalog: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    profile = (catalog or build_profile_catalog()).get(str(profile_id or "").strip())
    if profile is None:
        raise ProfileError("PROFILE_NOT_FOUND")
    if profile["status"] == "unavailable":
        raise ProfileError("PROFILE_UNAVAILABLE")
    if profile["status"] != "verified" and not allow_documented:
        raise ProfileError("PROFILE_NOT_VERIFIED")
    return copy.deepcopy(profile)


def validate_performance_plan(source_text: str, plan: Any) -> dict[str, Any]:
    source = str(source_text or "").strip()
    if not source or not isinstance(plan, dict):
        raise ProfileError("PERFORMANCE_PLAN_INVALID")
    result = copy.deepcopy(plan)
    if str(result.get("text") or "") != source:
        raise ProfileError("PERFORMANCE_TEXT_MISMATCH")
    try:
        speed = float(result.get("speed", 1.0))
        pitch = float(result.get("pitch", 0.0))
        energy = float(result.get("energy", 0.7))
        pause_after_ms = int(result.get("pause_after_ms", 0))
    except (TypeError, ValueError) as exc:
        raise ProfileError("PERFORMANCE_VALUE_INVALID") from exc
    if not 0.90 <= speed <= 1.05:
        raise ProfileError("PERFORMANCE_SPEED_OUT_OF_RANGE")
    if not -2.0 <= pitch <= 2.0:
        raise ProfileError("PERFORMANCE_PITCH_OUT_OF_RANGE")
    if not 0.0 <= energy <= 1.0:
        raise ProfileError("PERFORMANCE_ENERGY_OUT_OF_RANGE")
    if not 0 <= pause_after_ms <= 650:
        raise ProfileError("PERFORMANCE_PAUSE_OUT_OF_RANGE")
    cues = result.get("cues", [])
    if not isinstance(cues, list) or len(cues) > 5:
        raise ProfileError("PERFORMANCE_CUES_INVALID")
    for cue in cues:
        if not isinstance(cue, dict):
            raise ProfileError("PERFORMANCE_CUE_INVALID")
        cue_text = str(cue.get("text") or "")
        if not cue_text or cue_text not in source:
            raise ProfileError("CUE_TEXT_NOT_FOUND")
        if cue.get("action") not in {"emphasize", "soften"}:
            raise ProfileError("CUE_ACTION_INVALID")
    result.update(schema_version=PERFORMANCE_SCHEMA_VERSION, text=source, speed=speed, pitch=pitch, energy=energy, pause_after_ms=pause_after_ms, cues=cues)
    return result


def compile_provider_settings(
    profile: dict[str, Any],
    performance_plan: dict[str, Any],
) -> dict[str, Any]:
    """Compile only controls shown by the current 302.AI provider examples."""
    plan = validate_performance_plan(
        str(performance_plan.get("text") or ""),
        performance_plan,
    )
    provider = profile["provider"]
    if provider == "minimax":
        emotion = plan.get("emotion")
        if emotion not in {"calm", "fluent"}:
            emotion = profile.get("default_emotion") or "calm"
        return {
            "voice_id": profile["voice_id"],
            "speed": plan["speed"],
            "vol": 1,
            "pitch": int(round(plan["pitch"])),
            "emotion": emotion,
        }
    if provider == "dubbingx":
        return {
            "voiceId": profile["voice_id"],
            "language": profile.get("language") or "zh",
            "emotion": profile.get("default_emotion") or "auto",
            "audioPitch": round(1.0 + (plan["pitch"] * 0.02), 2),
            "audioSpeed": plan["speed"],
        }
    if provider == "elevenlabs":
        return {"voice_id": profile["voice_id"]}
    raise ProfileError("PROFILE_PROVIDER_INVALID")


def _collect_voice_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"voice_id", "voiceId"} and isinstance(child, (str, int)):
                found.add(str(child))
            else:
                found.update(_collect_voice_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_voice_ids(child))
    return found


def check_profile_sources(api_key: str, *, client: httpx.Client | None = None) -> dict[str, Any]:
    key = str(api_key or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if not key:
        raise ProfileError("AI302_API_KEY_MISSING")
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json", "Content-Type": "application/json"}
    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=30.0)
    results: dict[str, Any] = {}
    requests = (
        ("elevenlabs", "GET", "/elevenlabs/voices", None),
        ("dubbingx", "POST", "/dubbingx/v1/getTTSTimbreList", {"pageIndex": 1, "pageSize": 100}),
    )
    try:
        for provider, method, path, body in requests:
            try:
                response = client.request(method, f"{AI302_BASE_URL}{path}", headers=headers, json=body)
                payload = response.json() if response.content else {}
                voice_ids = sorted(_collect_voice_ids(payload))
                results[provider] = {"status": "available" if response.is_success else "unavailable", "http_status": response.status_code, "voice_count": len(voice_ids), "voice_ids": voice_ids, "error_code": "" if response.is_success else f"HTTP_{response.status_code}"}
            except (httpx.HTTPError, ValueError) as exc:
                results[provider] = {"status": "unavailable", "http_status": 0, "voice_count": 0, "voice_ids": [], "error_code": type(exc).__name__}
    finally:
        if owns_client:
            client.close()
    minimax_count = sum(item["provider"] == "minimax" for item in build_profile_catalog().values())
    results["minimax"] = {"status": "documentation_only", "http_status": 0, "voice_count": minimax_count, "voice_ids": [], "error_code": "PAID_PREVIEW_REQUIRED"}
    return results
