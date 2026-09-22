"""Shared-volume heartbeat for the single background worker."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HEARTBEAT_FILENAME = ".worker-heartbeat.json"


def write_worker_heartbeat(storage_root: str | Path) -> dict[str, Any]:
    root = Path(storage_root)
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "ready",
        "pid": os.getpid(),
        "heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    target = root / HEARTBEAT_FILENAME
    temporary = root / f"{HEARTBEAT_FILENAME}.tmp-{os.getpid()}"
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(target)
    return payload


def worker_heartbeat_status(
    storage_root: str | Path, *, stale_after_seconds: float
) -> dict[str, Any]:
    target = Path(storage_root) / HEARTBEAT_FILENAME
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        heartbeat = datetime.fromisoformat(str(payload["heartbeat_at"]))
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=timezone.utc)
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"status": "unavailable", "heartbeat_at": None, "age_seconds": None}
    age = max(0.0, (datetime.now(timezone.utc) - heartbeat).total_seconds())
    return {
        "status": "ready" if age <= stale_after_seconds else "stale",
        "heartbeat_at": heartbeat.isoformat(),
        "age_seconds": round(age, 3),
    }
