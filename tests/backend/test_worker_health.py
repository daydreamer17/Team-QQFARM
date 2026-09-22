from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from supplier_comparison.backend.worker_health import (
    HEARTBEAT_FILENAME,
    worker_heartbeat_status,
    write_worker_heartbeat,
)


def test_worker_heartbeat_reports_ready_and_missing(tmp_path) -> None:
    assert worker_heartbeat_status(tmp_path, stale_after_seconds=20)["status"] == "unavailable"

    written = write_worker_heartbeat(tmp_path)
    status = worker_heartbeat_status(tmp_path, stale_after_seconds=20)

    assert written["status"] == "ready"
    assert status["status"] == "ready"
    assert status["age_seconds"] is not None


def test_worker_heartbeat_reports_stale(tmp_path) -> None:
    old = datetime.now(timezone.utc) - timedelta(minutes=5)
    (tmp_path / HEARTBEAT_FILENAME).write_text(
        json.dumps({"status": "ready", "pid": 1, "heartbeat_at": old.isoformat()}),
        encoding="utf-8",
    )

    status = worker_heartbeat_status(tmp_path, stale_after_seconds=20)

    assert status["status"] == "stale"
    assert status["age_seconds"] >= 299
