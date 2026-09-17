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
    assert loaded.json()["quotes"] == [
        {
            "quote_id": uploaded.json()["quote_id"],
            "quote_version": 1,
            "supplier_id": "SUP-022",
            "document_id": uploaded.json()["document_id"],
            "document_version": 1,
            "original_filename": "supplier-a.csv",
        }
    ]

    history = http.get(f"/api/v1/tasks/{task['task_id']}/quotes")
    assert history.status_code == 200
    assert history.json()["task_revision"] == 2
    assert history.json()["items"][0]["supplier_id"] == "SUP-022"
    assert history.json()["items"][0]["versions"] == [
        {
            "quote_version": 1,
            "document_id": uploaded.json()["document_id"],
            "document_version": 1,
            "original_filename": "supplier-a.csv",
            "media_type": "text/csv",
            "size_bytes": len(b"quote data"),
            "document_sha256": uploaded.json()["document_sha256"],
            "is_synthetic": True,
            "is_current": True,
            "created_at": history.json()["items"][0]["versions"][0]["created_at"],
        }
    ]
    assert "storage_path" not in str(history.json())


def test_list_tasks_returns_safe_recent_summaries(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    created = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-list-1"},
        json={"requirement": REQUIREMENT, "scenario_id": "LIST-DEMO-001"},
    ).json()

    response = http.get("/api/v1/tasks", params={"limit": 10})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "task_id": created["task_id"],
                "scenario_id": "LIST-DEMO-001",
                "task_revision": 1,
                "status": "DRAFT",
                "current_result_id": None,
                "manufacturer": "QQ Demo Components",
                "manufacturer_part_number": "QW-MCU9-DEMO",
                "planned_order_date": "2026-09-14",
                "created_at": response.json()["items"][0]["created_at"],
                "updated_at": response.json()["items"][0]["updated_at"],
            }
        ]
    }


def test_list_tasks_rejects_oversized_limit(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    assert http.get("/api/v1/tasks", params={"limit": 51}).status_code == 422


def test_create_task_freezes_policy_set_and_index_binding(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    policy_binding = {
        "policy_set_version": "2026.09.1",
        "policy_index_version": "pidx-api-test",
        "category": "Electronics",
        "region": "SG",
    }

    created = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-with-policy"},
        json={"requirement": REQUIREMENT, "policy_binding": policy_binding},
    )

    assert created.status_code == 201
    assert created.json()["policy_binding"] == policy_binding
    loaded = http.get(f"/api/v1/tasks/{created.json()['task_id']}")
    assert loaded.status_code == 200
    assert loaded.json()["policy_binding"] == policy_binding


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


def test_failed_resume_job_can_be_requeued_from_same_checkpoint(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-retry"},
        json={"requirement": REQUIREMENT},
    ).json()
    run = service.start_run(
        task["task_id"], expected_task_revision=1, idempotency_key="run-retry"
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
    resume = service.answer_issue(
        task["task_id"],
        issue["issue_id"],
        expected_task_revision=1,
        answer={"answer_type": "CONFIRM_MISSING"},
        idempotency_key="answer-retry",
    )
    service.claim_job(resume["job_id"])
    service.fail_job(
        resume["job_id"], code="workflow_failed", message="Workflow execution failed."
    )

    retried = http.post(
        f"/api/v1/tasks/{task['task_id']}/jobs/{resume['job_id']}/retries",
        headers={"Idempotency-Key": "retry-resume"},
        json={"expected_task_revision": 2},
    )

    assert retried.status_code == 202
    assert retried.json()["job_id"] == resume["job_id"]
    assert retried.json()["job_type"] == "RESUME"
    assert retried.json()["job_status"] == "PENDING"
    loaded = http.get(f"/api/v1/tasks/{task['task_id']}").json()
    assert loaded["status"] == "QUEUED"
    assert loaded["current_job"]["error_code"] is None


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
