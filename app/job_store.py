from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class IdempotencyConflict(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class JobStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._request_index: dict[str, str] = {}
        for path in self.root.glob("*.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
                request_id = str(job.get("request_id") or "")
                job_id = str(job.get("job_id") or "")
                if request_id and job_id:
                    self._request_index[request_id] = job_id
            except (OSError, ValueError, TypeError):
                continue

    def _path(self, job_id: str) -> Path:
        return self.root / f"{job_id}.json"

    def _write(self, job: dict[str, Any]) -> None:
        path = self._path(str(job["job_id"]))
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(job, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def create_or_get(
        self,
        request_id: str,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        payload_hash = _fingerprint(payload)
        with self._lock:
            existing_id = self._request_index.get(request_id)
            if existing_id:
                existing = self.get(existing_id)
                if existing.get("payload_hash") != payload_hash:
                    raise IdempotencyConflict(request_id)
                return existing, False
            now = utc_now()
            job = {
                "job_id": "srj_" + uuid.uuid4().hex,
                "request_id": request_id,
                "payload_hash": payload_hash,
                "payload": deepcopy(payload),
                "status": "queued",
                "result": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            self._request_index[request_id] = str(job["job_id"])
            self._write(job)
            return deepcopy(job), True

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            path = self._path(job_id)
            if not path.is_file():
                raise KeyError(job_id)
            return json.loads(path.read_text(encoding="utf-8"))

    def update(self, job_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            job = self.get(job_id)
            job.update(deepcopy(changes))
            job["updated_at"] = utc_now()
            self._write(job)
            return deepcopy(job)
