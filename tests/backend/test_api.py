from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from supplier_comparison.backend.api import create_app
from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendService


REQUIREMENT = {
    "manufacturer": "QQ Demo Components",
    "manufacturer_part_number": "QW-MCU9-DEMO",
    "package": "QFN-32",
    "revision": "R1",
    "condition": "NEW",
    "allow_substitutes": False,
    "base_unit": "piece",
    "required_quantity": 1000,
    "quantity_unit": "piece",
    "budget_amount": "8000.00",
    "currency": "SGD",
    "includes_shipping": True,
    "tax_mode": "EXCLUDED",
    "other_fees_required": False,
    "planned_order_date": "2026-09-14",
    "delivery_deadline": "2026-09-19",
    "delivery_location": "Singapore",
    "ranking_preference": "LOWEST_CONFIRMED_TOTAL_COST",
}


@pytest.fixture
def client(tmp_path: Path) -> tuple[TestClient, BackendService]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    service = BackendService(sessions, tmp_path / "quotes", actor_id="local-test-user")
    return TestClient(create_app(service, readiness_check=lambda: True)), service


def test_create_upload_and_read_task_without_exposing_storage_path(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    created = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-1"},
        json={"requirement": REQUIREMENT, "scenario_id": "MCU-DEMO-001"},
    )
    assert created.status_code == 201
    task = created.json()

    uploaded = http.post(
        f"/api/v1/tasks/{task['task_id']}/quotes",
        headers={"Idempotency-Key": "upload-1"},
        data={
            "expected_task_revision": "1",
            "supplier_id": "SUP-022",
            "is_synthetic": "true",
        },
        files={"file": ("supplier-a.csv", b"quote data", "text/csv")},
    )

    assert uploaded.status_code == 201
    assert uploaded.json()["task_revision"] == 2
    assert "storage_path" not in uploaded.json()
    loaded = http.get(f"/api/v1/tasks/{task['task_id']}")
    assert loaded.status_code == 200
    assert loaded.json()["scenario_id"] == "MCU-DEMO-001"
    assert loaded.json()["requirement"]["budget_amount"] == "8000.00"


def test_stale_mutation_returns_stable_error_envelope(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-1"},
        json={"requirement": REQUIREMENT},
    ).json()
    service.upload_quote(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-022",
        original_filename="supplier-a.csv",
        media_type="text/csv",
        content=b"quote data",
        idempotency_key="upload-direct",
    )

    response = http.post(
        f"/api/v1/tasks/{task['task_id']}/runs",
        headers={"Idempotency-Key": "run-1", "X-Request-ID": "request-123"},
        json={"expected_task_revision": 1},
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "task_revision_conflict",
            "message": "Task revision has changed.",
            "details": {"expected": 1, "actual": 2},
            "request_id": "request-123",
        }
    }


def test_issue_answer_endpoint_uses_server_side_actor(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-1"},
        json={"requirement": REQUIREMENT},
    ).json()
    run = service.start_run(
        task["task_id"], expected_task_revision=1, idempotency_key="run-direct"
    )
    issue = service.open_issue(
        task_id=task["task_id"],
        graph_run_id=run["graph_run_id"],
        task_revision=1,
        issue_type="CONFIRM_MISSING",
        quote_id="quote-b",
        field_name="shipping_fee_status",
        question="Confirm missing shipping.",
        answer_schema={"answer_type": "CONFIRM_MISSING"},
    )

    answered = http.post(
        f"/api/v1/tasks/{task['task_id']}/issues/{issue['issue_id']}/answers",
        headers={"Idempotency-Key": "answer-1"},
        json={
            "expected_task_revision": 1,
            "answer": {"answer_type": "CONFIRM_MISSING"},
            "actor_id": "attacker-controlled",
        },
    )

    assert answered.status_code == 202
    issues = http.get(f"/api/v1/tasks/{task['task_id']}/issues").json()
    assert issues[0]["answered_by"] == "local-test-user"


def test_health_endpoints_separate_liveness_and_readiness(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    assert http.get("/health/live").json() == {"status": "alive"}
    assert http.get("/health/ready").json() == {"status": "ready"}


def test_upload_rejects_content_larger_than_parser_limit(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    task = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-limit"},
        json={"requirement": REQUIREMENT},
    ).json()

    response = http.post(
        f"/api/v1/tasks/{task['task_id']}/quotes",
        headers={"Idempotency-Key": "upload-limit"},
        data={"expected_task_revision": "1", "supplier_id": "SUP-022"},
        files={"file": ("too-large.pdf", b"x" * (5 * 1024 * 1024 + 1), "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "file_too_large"
    assert http.get(f"/api/v1/tasks/{task['task_id']}").json()["task_revision"] == 1


def test_unexpected_error_uses_envelope_without_leaking_exception(
    client: tuple[TestClient, BackendService],
) -> None:
    _http, service = client

    def broken_readiness() -> bool:
        raise RuntimeError("database password must not leak")

    http = TestClient(
        create_app(service, readiness_check=broken_readiness),
        raise_server_exceptions=False,
    )
    response = http.get("/health/ready", headers={"X-Request-ID": "request-500"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "An unexpected server error occurred.",
            "details": {},
            "request_id": "request-500",
        }
    }
