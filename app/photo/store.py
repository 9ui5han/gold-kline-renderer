import json
import re
from pathlib import Path


PHOTO_JOB_PATTERN = re.compile(r"^photo-[A-Za-z0-9][A-Za-z0-9_-]{2,80}$")


class PhotoStore:
    """Keep photo artifacts in a namespace separate from video and macro data."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "photo-work"

    def job_dir(self, photo_job_id: str) -> Path:
        job_id = str(photo_job_id or "").strip()
        if not PHOTO_JOB_PATTERN.fullmatch(job_id):
            raise ValueError("PHOTO_JOB_ID_INVALID")
        target = self.root / job_id
        target.mkdir(parents=True, exist_ok=True)
        return target

    def save_job(self, photo_job_id: str, payload: dict) -> None:
        target = self.job_dir(photo_job_id) / "manifest.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(target)

    def get_job(self, photo_job_id: str) -> dict:
        target = self.job_dir(photo_job_id) / "manifest.json"
        if not target.is_file():
            raise KeyError("PHOTO_JOB_NOT_FOUND")
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("PHOTO_JOB_MANIFEST_INVALID") from exc
        if not isinstance(data, dict):
            raise ValueError("PHOTO_JOB_MANIFEST_INVALID")
        return data

    def save_context(self, photo_job_id: str, payload: dict) -> None:
        target = self.job_dir(photo_job_id) / "render-context.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(target)

    def get_context(self, photo_job_id: str) -> dict:
        target = self.job_dir(photo_job_id) / "render-context.json"
        if not target.is_file():
            raise KeyError("PHOTO_RENDER_CONTEXT_NOT_FOUND")
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("PHOTO_RENDER_CONTEXT_INVALID") from exc
        if not isinstance(data, dict):
            raise ValueError("PHOTO_RENDER_CONTEXT_INVALID")
        return data
