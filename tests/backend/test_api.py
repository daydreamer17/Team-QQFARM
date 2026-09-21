from __future__ import annotations

from decimal import Decimal
from io import BytesIO
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from supplier_comparison.backend.api import create_app
from supplier_comparison.backend.models import Base, Document, Job, Task, WorkflowArtifact
from supplier_comparison.backend.service import BackendService
from supplier_comparison.backend.service import BackendError
from supplier_comparison.backend.service import content_hash
from supplier_comparison.backend.workflow import DraftReviewRunner, WorkflowRunner
from langgraph.checkpoint.memory import InMemorySaver
from tests.backend.test_workflow import CanonicalCsvProcessor, DICTIONARY_PATH, _requirement


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
    assert loaded.json()["progress"] == {
        "requirement_completed": True,
        "quote_review_completed": False,
        "decision_completed": False,
        "summary_completed": False,
    }
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


def test_quote_replacement_upload_and_deactivation_endpoints(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    task = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-replacement-api"},
        json={"requirement": REQUIREMENT, "scenario_id": "REPLACEMENT-API"},
    ).json()
    uploaded = http.post(
        f"/api/v1/tasks/{task['task_id']}/quotes",
        headers={"Idempotency-Key": "upload-replacement-api-v1"},
        data={
            "expected_task_revision": "1",
            "supplier_id": "SUP-022",
            "is_synthetic": "true",
        },
        files={"file": ("supplier-v1.csv", b"version one", "text/csv")},
    ).json()

    replacement = http.post(
        f"/api/v1/tasks/{task['task_id']}/quotes/{uploaded['quote_id']}/revisions",
        headers={"Idempotency-Key": "upload-replacement-api-v2"},
        json={"expected_task_revision": 2},
    )

    assert replacement.status_code == 202
    draft = replacement.json()
    assert draft["replacement_quote_id"] == uploaded["quote_id"]
    discarded = http.post(
        f"/api/v1/tasks/{task['task_id']}/quote-drafts/{draft['quote_draft_id']}/discard",
        headers={"Idempotency-Key": "discard-replacement-api-v2"},
        json={"expected_draft_revision": draft["draft_revision"]},
    )
    assert discarded.status_code == 200

    deactivated = http.post(
        f"/api/v1/tasks/{task['task_id']}/quotes/{uploaded['quote_id']}/deactivate",
        headers={"Idempotency-Key": "deactivate-replacement-api-v1"},
        json={"expected_task_revision": 2},
    )

    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False
    history = http.get(f"/api/v1/tasks/{task['task_id']}/quotes").json()
    assert history["items"][0]["active"] is False
    assert len(history["items"][0]["versions"]) == 1

    duplicate = http.post(
        f"/api/v1/tasks/{task['task_id']}/quotes",
        headers={"Idempotency-Key": "duplicate-disabled-quote-api"},
        data={
            "expected_task_revision": str(deactivated.json()["task_revision"]),
            "supplier_id": "SUP-022",
            "is_synthetic": "true",
        },
        files={"file": ("supplier-v1.csv", b"version one", "text/csv")},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_quote_uploaded"
    assert duplicate.json()["error"]["details"]["quote_id"] == uploaded["quote_id"]
    assert duplicate.json()["error"]["details"]["quote_active"] is False

    reactivated = http.post(
        f"/api/v1/tasks/{task['task_id']}/quotes/{uploaded['quote_id']}/reactivate",
        headers={"Idempotency-Key": "reactivate-replacement-api-v1"},
        json={"expected_task_revision": deactivated.json()["task_revision"]},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["active"] is True
    assert reactivated.json()["task_revision"] == deactivated.json()["task_revision"] + 1
    history = http.get(f"/api/v1/tasks/{task['task_id']}/quotes").json()
    assert history["items"][0]["active"] is True


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
        ],
        "total": 1,
        "limit": 10,
        "offset": 0,
        "status_counts": {"DRAFT": 1},
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


def test_quote_field_schema_is_backend_owned_and_complete(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client

    response = http.get("/api/v1/quote-field-schema")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "quote-review-schema/1.0.0"
    assert payload["dictionary_version"] == "1.2.0"
    assert len(payload["dictionary_sha256"]) == 64
    assert len(payload["fields"]) == 30
    assert {item["required_level"] for item in payload["fields"]} == {
        "关键",
        "条件关键",
        "可选",
    }
    assert sum(item["required_level"] == "关键" for item in payload["fields"]) == 16
    assert sum(item["required_level"] == "条件关键" for item in payload["fields"]) == 10
    assert sum(item["required_level"] == "可选" for item in payload["fields"]) == 4
    assert "supplier_id" not in {item["field_name"] for item in payload["fields"]}
    shipping = next(item for item in payload["fields"] if item["field_name"] == "shipping_fee_status")
    assert "UNKNOWN" in shipping["allowed_values"]
    assert {item["group_id"] for item in payload["relation_groups"]} >= {
        "shipping_fee",
        "price_basis",
        "relative_delivery",
    }


def test_quote_draft_mutations_define_json_request_bodies(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client

    schema_response = http.get("/openapi.json")

    assert schema_response.status_code == 200
    paths = schema_response.json()["paths"]
    operations = (
        (
            "/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/review",
            "put",
            "QuoteDraftReviewRequest",
        ),
        (
            "/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/corrections",
            "put",
            "QuoteDraftCorrectionRequest",
        ),
        (
            "/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/submit",
            "post",
            "SubmitQuoteDraftRequest",
        ),
        (
            "/api/v1/tasks/{task_id}/quote-drafts/{draft_id}/discard",
            "post",
            "DiscardQuoteDraftRequest",
        ),
    )
    for path, method, schema_name in operations:
        body_schema = paths[path][method]["requestBody"]["content"][
            "application/json"
        ]["schema"]
        assert body_schema == {"$ref": f"#/components/schemas/{schema_name}"}


def test_put_quote_draft_review_accepts_exactly_all_30_field_actions(
    client: tuple[TestClient, BackendService],
    tmp_path: Path,
) -> None:
    http, service = client
    task = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-full-field-review"},
        json={"requirement": REQUIREMENT, "scenario_id": "MCU-DEMO-001"},
    ).json()
    uploaded = http.post(
        f"/api/v1/tasks/{task['task_id']}/quote-drafts",
        headers={"Idempotency-Key": "upload-full-field-review"},
        data={
            "expected_task_revision": "1",
            "supplier_id": "SUP-022",
            "is_synthetic": "true",
        },
        files={"file": ("supplier-a.csv", b"synthetic quote", "text/csv")},
    )
    assert uploaded.status_code == 202
    draft = uploaded.json()
    processed = DraftReviewRunner(
        service,
        processor=CanonicalCsvProcessor(tmp_path),
        dictionary_path=DICTIONARY_PATH,
    ).run_job(draft["job"]["job_id"])
    assert processed["status"] == "REVIEW_REQUIRED"

    current_response = http.get(
        f"/api/v1/tasks/{task['task_id']}/quote-drafts/{draft['quote_draft_id']}"
    )
    assert current_response.status_code == 200
    current = current_response.json()
    assert len(current["fields"]) == 30
    actions = [
        {
            "action": (
                "CONFIRM_VALUE"
                if field["validation_status"] in {"EXTRACTED", "VERIFIED"}
                else "CONFIRM_MISSING"
                if field["validation_status"] == "MISSING"
                else "CONFIRM_CONFLICT"
            ),
            "field_name": field["field_name"],
            "expected_field_id": field["field_id"],
            "expected_field_version": field["field_version"],
        }
        for field in current["fields"]
    ]

    review_url = (
        f"/api/v1/tasks/{task['task_id']}"
        f"/quote-drafts/{draft['quote_draft_id']}/review"
    )
    review_request = {
        "expected_draft_revision": current["draft_revision"],
        "schema_version": current["schema_version"],
        "actions": actions,
    }
    review_headers = {"Idempotency-Key": "review-all-fields"}
    reviewed = http.put(
        review_url,
        headers=review_headers,
        json=review_request,
    )

    assert reviewed.status_code == 200
    payload = reviewed.json()
    assert payload["status"] == "READY_TO_SUBMIT"
    assert payload["human_review_complete"] is True
    assert payload["submission_ready"] is True
    assert payload["review_progress"]["reviewed"] == 30
    assert payload["unconfirmed_fields"] == []

    replayed = http.put(
        review_url,
        headers=review_headers,
        json=review_request,
    )
    assert replayed.status_code == 200
    assert replayed.json() == payload

    different_request = {
        **review_request,
        "actions": [dict(action) for action in actions],
    }
    different_request["actions"][0]["reason"] = "A different human review request."
    conflicting = http.put(
        review_url,
        headers=review_headers,
        json=different_request,
    )
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "idempotency_key_reused"
    assert service.get_quote_draft(
        task["task_id"], draft["quote_draft_id"]
    )["draft_revision"] == payload["draft_revision"]


def test_quote_draft_endpoints_hide_drafts_across_tasks_and_owners(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = service.create_task(
        _requirement(), idempotency_key="create-private-draft"
    )
    other_task = service.create_task(
        _requirement(), idempotency_key="create-other-private-task"
    )
    draft = service.upload_quote_draft_stream(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-PRIVATE",
        original_filename="private.csv",
        media_type="text/csv",
        stream=BytesIO(b"supplier_name,unit_price\nPrivate Supplier,10.00\n"),
        idempotency_key="upload-private-draft",
    )
    draft_id = draft["quote_draft_id"]
    review_body = {
        "expected_draft_revision": draft["draft_revision"],
        "schema_version": service.quote_field_schema()["schema_version"],
        "actions": [
            {
                "action": "CONFIRM_VALUE",
                "field_name": "supplier_name",
                "expected_field_id": "field-not-disclosed",
                "expected_field_version": 1,
            }
        ],
    }

    wrong_task_base = (
        f"/api/v1/tasks/{other_task['task_id']}/quote-drafts/{draft_id}"
    )
    assert http.get(wrong_task_base).status_code == 404
    assert http.get(f"{wrong_task_base}/content").status_code == 404
    wrong_task_review = http.put(
        f"{wrong_task_base}/review",
        headers={"Idempotency-Key": "wrong-task-review"},
        json=review_body,
    )
    assert wrong_task_review.status_code == 404
    assert wrong_task_review.json()["error"]["code"] == "quote_draft_not_found"

    outsider = BackendService(
        service.session_factory,
        service.storage_root,
        actor_id="quote-draft-outsider",
    )
    outsider_http = TestClient(create_app(outsider, readiness_check=lambda: True))
    owned_base = f"/api/v1/tasks/{task['task_id']}/quote-drafts/{draft_id}"
    assert outsider_http.get(owned_base).status_code == 404
    assert outsider_http.get(f"{owned_base}/content").status_code == 404
    outsider_review = outsider_http.put(
        f"{owned_base}/review",
        headers={"Idempotency-Key": "outsider-review"},
        json=review_body,
    )
    assert outsider_review.status_code == 404
    assert outsider_review.json()["error"]["code"] == "quote_draft_not_found"


def test_requirement_draft_is_grounded_and_bound_to_created_task(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    uploaded = http.post(
        "/api/v1/requirement-drafts",
        headers={"Idempotency-Key": "requirement-upload-1"},
        files={"file": ("requirement.txt", b"Manufacturer: QQ Demo Components\nPart: QW-MCU9-DEMO\nQuantity: 1000 pieces", "text/plain")},
    )
    assert uploaded.status_code == 202
    draft = uploaded.json()
    context = service.requirement_draft_job_context(draft["job"]["job_id"])
    source = {
        "source_id": "requirement:line:1", "kind": "TEXT_LINE",
        "page_number": None, "line_number": 1, "raw_text": "Manufacturer: QQ Demo Components",
    }
    service.complete_requirement_draft_job(
        context["job_id"],
        parsed={"schema_version": "requirement-parsed/1.0.0", "sources": [source]},
        candidates={"schema_version": "requirement-candidates/1.0.0", "candidates": [{
            "field_name": "manufacturer", "raw_value": "QQ Demo Components",
            "normalized_value": "QQ Demo Components", "validation_status": "EXTRACTED",
            "origin": "DOCUMENT", "source_refs": [{"source_id": source["source_id"], "quoted_text": source["raw_text"]}],
        }]},
        calls_used=1,
    )
    ready = http.get(f"/api/v1/requirement-drafts/{draft['requirement_draft_id']}").json()
    assert ready["status"] == "READY"
    created = http.post(
        "/api/v1/tasks",
        headers={"Idempotency-Key": "create-from-requirement-draft"},
        json={
            "requirement": REQUIREMENT,
            "requirement_draft_id": ready["requirement_draft_id"],
            "expected_requirement_draft_revision": ready["draft_revision"],
        },
    )
    assert created.status_code == 201
    used = http.get(f"/api/v1/requirement-drafts/{ready['requirement_draft_id']}").json()
    assert used["status"] == "USED"
    assert used["submitted_task_id"] == created.json()["task_id"]


def test_requirement_update_and_soft_abandon_are_versioned(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    task = http.post(
        "/api/v1/tasks", headers={"Idempotency-Key": "create-edit"}, json={"requirement": REQUIREMENT}
    ).json()
    changed = dict(REQUIREMENT, budget_amount="9000.00")
    updated = http.put(
        f"/api/v1/tasks/{task['task_id']}/requirement",
        headers={"Idempotency-Key": "edit-requirement"},
        json={"expected_task_revision": 1, "requirement": changed},
    )
    assert updated.status_code == 202
    assert updated.json()["task_revision"] == 2
    assert updated.json()["status"] == "DRAFT"
    assert http.get(f"/api/v1/tasks/{task['task_id']}").json()["requirement"]["budget_amount"] == "9000.00"

    abandoned = http.post(
        f"/api/v1/tasks/{task['task_id']}/abandon",
        headers={"Idempotency-Key": "abandon-task"},
        json={"expected_task_revision": 2, "reason": "采购需求已经取消"},
    )
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "ABANDONED"
    blocked = http.post(
        f"/api/v1/tasks/{task['task_id']}/runs",
        headers={"Idempotency-Key": "run-abandoned"},
        json={"expected_task_revision": 3},
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "task_abandoned"
    audit = http.get(f"/api/v1/tasks/{task['task_id']}/revisions").json()
    assert [item["change_type"] for item in audit["revisions"]] == [
        "TASK_CREATED", "REQUIREMENT_UPDATED", "TASK_ABANDONED"
    ]
    assert audit["revisions"][-1]["details"]["reason"] == "采购需求已经取消"


def test_requirement_update_with_quote_queues_full_recalculation(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = service.create_task(_requirement(), idempotency_key="create-edit-with-quote")
    quote = service.upload_quote(
        task["task_id"], expected_task_revision=1, supplier_id="SUP-EDIT",
        original_filename="quote.csv", media_type="text/csv", content=b"quote",
        idempotency_key="upload-edit-quote",
    )
    changed = dict(REQUIREMENT, delivery_deadline="2026-09-20")
    response = http.put(
        f"/api/v1/tasks/{task['task_id']}/requirement",
        headers={"Idempotency-Key": "edit-with-quote"},
        json={"expected_task_revision": quote["task_revision"], "requirement": changed},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"
    assert response.json()["graph_run_id"]
    assert response.json()["job_id"]
    loaded = http.get(f"/api/v1/tasks/{task['task_id']}").json()
    assert loaded["current_job"]["job_type"] == "START"


def test_task_directory_filters_and_paginates_on_server(
    client: tuple[TestClient, BackendService],
) -> None:
    http, _service = client
    for key, scenario in (("directory-a", "ALPHA-001"), ("directory-b", "BETA-002")):
        created = http.post(
            "/api/v1/tasks", headers={"Idempotency-Key": key},
            json={"requirement": REQUIREMENT, "scenario_id": scenario},
        )
        assert created.status_code == 201
    response = http.get("/api/v1/tasks", params={
        "query": "alpha", "status": "DRAFT", "sort": "created_desc", "limit": 1, "offset": 0,
    })
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["scenario_id"] == "ALPHA-001"
    assert response.json()["status_counts"] == {"DRAFT": 2}


def test_document_content_stream_is_scoped_and_audited(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = service.create_task(_requirement(), idempotency_key="create-file")
    quote = service.upload_quote(
        task["task_id"], expected_task_revision=1, supplier_id="SUP-FILE",
        original_filename="quote.csv", media_type="text/csv", content=b"supplier,price\nSUP-FILE,10\n",
        idempotency_key="upload-file",
    )
    response = http.get(
        f"/api/v1/tasks/{task['task_id']}/documents/{quote['document_id']}/content",
        params={"disposition": "inline"},
        headers={"X-Request-ID": "file-request-1"},
    )
    assert response.status_code == 200
    assert response.content == b"supplier,price\nSUP-FILE,10\n"
    assert response.headers["etag"] == f'"{quote["document_sha256"]}"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")
    download = http.get(
        f"/api/v1/tasks/{task['task_id']}/documents/{quote['document_id']}/content",
        params={"disposition": "attachment"},
        headers={"X-Request-ID": "file-request-2"},
    )
    assert download.status_code == 200
    assert download.headers["content-disposition"].startswith("attachment;")
    accesses = http.get(f"/api/v1/tasks/{task['task_id']}/revisions").json()["document_accesses"]
    assert {item["action"] for item in accesses[:2]} == {"PREVIEW", "DOWNLOAD"}
    with service.session_factory.begin() as session:
        session.get(Document, quote["document_id"]).storage_path = "/etc/hosts"
    escaped = http.get(
        f"/api/v1/tasks/{task['task_id']}/documents/{quote['document_id']}/content"
    )
    assert escaped.status_code == 404
    assert escaped.json()["error"]["code"] == "document_content_not_found"


def test_quote_draft_content_can_be_previewed_before_submission(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = service.create_task(_requirement(), idempotency_key="create-draft-preview")
    source = b"supplier_name,unit_price\nSynthetic Supplier,10.00\n"
    draft = service.upload_quote_draft_stream(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-DRAFT",
        original_filename="draft.csv",
        media_type="text/csv",
        stream=BytesIO(source),
        idempotency_key="upload-draft-preview",
    )

    response = http.get(
        f"/api/v1/tasks/{task['task_id']}/quote-drafts/{draft['quote_draft_id']}/content",
        params={"disposition": "inline"},
    )

    assert response.status_code == 200
    assert response.content == source
    assert response.headers["etag"] == f'"{draft["document_sha256"]}"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")


def test_summary_is_bound_to_current_result_and_worker_output(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = service.create_task(_requirement(), idempotency_key="create-summary")
    result_id = "artifact_result_summary"
    payload = {
        "disposition": "FINAL", "evaluated_at": "2026-09-18T00:00:00+00:00",
        "rule_version": "rules/1", "supplier_results": [], "pending_quote_ids": [],
        "comparison_reasons": [], "recommended_quote_ids": [],
        "final_recommendation_allowed": False,
    }
    with service.session_factory.begin() as session:
        row = session.get(Task, task["task_id"])
        row.current_result_id = result_id
        row.status = "COMPLETED"
        session.add(WorkflowArtifact(
            artifact_id=result_id, task_id=task["task_id"], task_revision=1,
            artifact_type="COMPARISON_RESULT", payload=payload,
            content_sha256=content_hash(payload), schema_version="comparison/1",
        ))
    created = http.post(
        f"/api/v1/tasks/{task['task_id']}/summaries",
        headers={"Idempotency-Key": "summary-create"},
        json={"expected_task_revision": 1, "result_id": result_id},
    )
    assert created.status_code == 202
    report = created.json()
    context = service.summary_job_context(report["job"]["job_id"])
    reference_id = next(iter(context["facts"]["references"]))
    completed = service.complete_summary_job(
        report["job"]["job_id"],
        narrative={
            "title": "采购摘要", "overview": "当前结果不允许正式推荐。",
            "sections": [{"heading": "结论", "text": "没有可发布的正式推荐。", "reference_ids": [reference_id]}],
            "disclaimer": "本摘要不是采购审批。",
        },
        calls_used=1,
    )
    assert completed["status"] == "SUCCEEDED"
    task_after_summary = http.get(f"/api/v1/tasks/{task['task_id']}").json()
    assert task_after_summary["summary_completed"] is True
    assert task_after_summary["progress"]["decision_completed"] is True
    assert task_after_summary["progress"]["summary_completed"] is True
    loaded = http.get(f"/api/v1/tasks/{task['task_id']}/summaries/{report['summary_id']}")
    assert loaded.status_code == 200
    assert loaded.json()["narrative"]["title"] == "采购摘要"
    replacement = service.create_summary(
        task["task_id"],
        expected_task_revision=1,
        result_id=result_id,
        idempotency_key="summary-create-new-prompt",
        provider="fixed",
        model_id="fixed-model",
        environment="TEST",
        prompt_version="summary/2",
    )
    assert replacement["summary_id"] != report["summary_id"]
    superseded = http.get(
        f"/api/v1/tasks/{task['task_id']}/summaries/{report['summary_id']}"
    ).json()
    assert superseded["status"] == "STALE"
    assert superseded["is_current"] is False
    changed = _requirement().model_copy(update={"budget_amount": Decimal("8100.00")})
    service.update_requirement(
        task["task_id"], changed, expected_task_revision=1,
        idempotency_key="summary-stale-after-requirement-change",
    )
    stale = http.get(f"/api/v1/tasks/{task['task_id']}/summaries/{report['summary_id']}").json()
    assert stale["status"] == "STALE"
    assert stale["is_current"] is False
    assert stale["narrative"]["title"] == "采购摘要"
    assert stale["facts"]["requirement"]["budget_amount"] == "8000.00"
    assert stale["facts"]["task_revision"] == 1
    assert http.get(f"/api/v1/tasks/{task['task_id']}").json()["summary_completed"] is False


def test_summary_failure_retries_share_a_bounded_call_budget(
    client: tuple[TestClient, BackendService],
) -> None:
    http, service = client
    task = service.create_task(_requirement(), idempotency_key="create-summary-retry")
    result_id = "artifact_result_summary_retry"
    payload = {
        "disposition": "DRAFT", "supplier_results": [], "pending_quote_ids": [],
        "comparison_reasons": [], "recommended_quote_ids": [],
        "final_recommendation_allowed": False,
    }
    with service.session_factory.begin() as session:
        row = session.get(Task, task["task_id"])
        row.current_result_id = result_id
        row.status = "COMPLETED"
        session.add(WorkflowArtifact(
            artifact_id=result_id, task_id=task["task_id"], task_revision=1,
            artifact_type="COMPARISON_RESULT", payload=payload,
            content_sha256=content_hash(payload), schema_version="comparison/1",
        ))
    created = http.post(
        f"/api/v1/tasks/{task['task_id']}/summaries",
        headers={"Idempotency-Key": "summary-retry-create"},
        json={"expected_task_revision": 1, "result_id": result_id},
    ).json()
    service.summary_job_context(created["job"]["job_id"])
    service.fail_summary_job(
        created["job"]["job_id"], code="model_transport_error",
        message="safe failure", calls_used=2,
    )
    retry = http.post(
        f"/api/v1/tasks/{task['task_id']}/summaries/{created['summary_id']}/retries",
        headers={"Idempotency-Key": "summary-retry-1"},
        json={"expected_task_revision": 1},
    )
    assert retry.status_code == 202
    service.summary_job_context(retry.json()["job"]["job_id"])
    service.fail_summary_job(
        retry.json()["job"]["job_id"], code="model_transport_error",
        message="safe failure", calls_used=4,
    )
    exhausted = http.post(
        f"/api/v1/tasks/{task['task_id']}/summaries/{created['summary_id']}/retries",
        headers={"Idempotency-Key": "summary-retry-2"},
        json={"expected_task_revision": 1},
    )
    assert exhausted.status_code == 409
    assert exhausted.json()["error"]["code"] == "summary_not_retryable"


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


@pytest.fixture
def batch_review(client, tmp_path):
    http, service = client
    task = service.create_task(_requirement(), idempotency_key="batch-create")
    revision = task["task_revision"]
    for supplier in ("SUP-023", "SUP-024"):
        upload = service.upload_quote(
            task["task_id"], expected_task_revision=revision, supplier_id=supplier,
            original_filename=f"{supplier}.csv", media_type="text/csv", content=supplier.encode(),
            idempotency_key=f"batch-upload-{supplier}", is_synthetic=True,
        )
        revision = upload["task_revision"]
    started = service.start_run(task["task_id"], expected_task_revision=revision, idempotency_key="batch-run")
    runner = WorkflowRunner(
        service, processor=CanonicalCsvProcessor(tmp_path, row_overrides={
            "SUP-023": {"shipping_fee_status": "FREE", "shipping_fee_amount": "10.00"},
            "SUP-024": {"shipping_fee_status": "FREE", "shipping_fee_amount": "500.00"},
        }), checkpointer=InMemorySaver(), dictionary_path=DICTIONARY_PATH,
        evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(BackendError) as failed:
        runner.run_job(started["job_id"])
    assert failed.value.code == "review_required"
    review = http.get(f"/api/v1/tasks/{task['task_id']}/review").json()
    corrections = []
    for quote in review["quotes"]:
        fields = {f["field_name"]: f for f in quote["fields"]}
        for name, value in (("shipping_fee_status", "KNOWN_AMOUNT"),
                            ("shipping_fee_amount", "200.00" if quote["supplier_id"] == "SUP-023" else "500.00")):
            corrections.append({
                "quote_id": quote["quote_id"], "field_name": name,
                "expected_field_version": fields[name]["field_version"],
                "raw_value": value, "normalized_value": value,
                "unit": "SGD" if name.endswith("amount") else None,
                "reason": "User confirmed with supplier",
            })
    body = {"expected_task_revision": revision, "corrections": corrections}
    return http, service, task, runner, review, body


def test_batch_review_lists_all_quotes_and_current_problems(batch_review):
    http, _service, task, _runner, review, _body = batch_review
    assert review["task_revision"] == 3
    assert not review["review_pending"]
    assert {p["quote_id"] for p in review["problems"]} == {q["quote_id"] for q in review["quotes"]}
    assert review["blocking_problem_count"] >= 2
    assert all(p["field_version"] and p["original_filename"] for p in review["problems"])
    assert all(q["evidence_sources"] and all(s["raw_text"] for s in q["evidence_sources"]) for q in review["quotes"])
    assert "storage_path" not in str(review)


def test_comparison_limitation_does_not_offer_rewriting_a_valid_quote_field(batch_review):
    http, service, task, _runner, review, _body = batch_review
    import copy
    from sqlalchemy import select

    quote_id = review["quotes"][0]["quote_id"]
    with service.session_factory.begin() as session:
        impact = session.scalar(
            select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task["task_id"],
                WorkflowArtifact.artifact_type == "DECISION_IMPACT_RESULT",
            )
        )
        payload = copy.deepcopy(impact.payload)
        row = next(
            item
            for item in payload["comparison"]["supplier_results"]
            if item["quote_id"] == quote_id
        )
        row["pending_reasons"].append(
            {
                "code": "TAX_CONVERSION_REQUIRED",
                "fields": ["tax_mode"],
                "message": "Tax conversion data is required.",
            }
        )
        impact.payload = payload

    current = http.get(f"/api/v1/tasks/{task['task_id']}/review").json()
    problem = next(
        item
        for item in current["problems"]
        if item["field_name"] == "tax_mode"
        and "TAX_CONVERSION_REQUIRED" in item["codes"]
    )
    assert problem["needs_resolution"] is True
    assert problem["resolution"] == "ADDITIONAL_INFORMATION_REQUIRED"


def test_batch_corrections_reaudit_once_and_recompute_without_reextraction(batch_review):
    http, service, task, runner, _review, body = batch_review
    url = f"/api/v1/tasks/{task['task_id']}/fields/corrections"
    headers = {"Idempotency-Key": "batch-correct"}
    response = http.post(url, json=body, headers=headers)
    assert response.status_code == 202, response.text
    updated = response.json()
    assert updated["task_revision"] == body["expected_task_revision"] + 1
    assert http.post(url, json=body, headers=headers).json() == updated
    queued_review = http.get(f"/api/v1/tasks/{task['task_id']}/review").json()
    assert queued_review["review_pending"]
    assert queued_review["problems"] == []  # Old findings are not represented as current.
    assert service.get_task(task["task_id"])["current_result_id"] is None
    assert runner.run_job(updated["job_id"])["status"] == "SUCCEEDED"
    assert len(runner.processor.calls) == 2
    final = http.get(f"/api/v1/tasks/{task['task_id']}/review").json()
    assert not final["review_pending"]
    assert final["blocking_problem_count"] == 0
    assert all(q["review_status"] == "READY_FOR_DOWNSTREAM" for q in final["quotes"])
    result = service.list_results(task["task_id"])[0]["result"]
    assert sorted(s["total_cost"] for s in result["supplier_results"]) == ["7000.00", "7100.00"]
    from sqlalchemy import select
    from supplier_comparison.backend.models import WorkflowArtifact, TaskRevision, Job
    with service.session_factory() as session:
        events = session.scalars(select(WorkflowArtifact).where(
            WorkflowArtifact.graph_run_id == updated["graph_run_id"],
            WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
        )).all()
        assert len(events) == 4
        assert {event.payload["reviewer_id"] for event in events} == {service.actor_id}
        assert len(session.scalars(select(TaskRevision).where(
            TaskRevision.task_id == task["task_id"], TaskRevision.change_type == "FIELDS_CORRECTED_BATCH"
        )).all()) == 1
        assert len(session.scalars(select(Job).where(Job.graph_run_id == updated["graph_run_id"])).all()) == 1


def test_batch_correction_uses_current_execution_instead_of_newer_unreferenced_batch(batch_review):
    http, service, task, _runner, review, body = batch_review
    import copy
    from sqlalchemy import select
    from supplier_comparison.backend.models import DocumentExecution

    quote = review["quotes"][0]
    with service.session_factory.begin() as session:
        task_row = session.get(Task, task["task_id"])
        execution = session.scalar(
            select(DocumentExecution)
            .join(Document, Document.document_id == DocumentExecution.document_id)
            .where(
                DocumentExecution.graph_run_id == task_row.current_graph_run_id,
                Document.quote_id == quote["quote_id"],
                Document.quote_version == quote["quote_version"],
            )
        )
        current = session.get(WorkflowArtifact, execution.batch_artifact_id)
        stale_payload = copy.deepcopy(current.payload)
        stale_payload["parsed_input"]["context"]["quote_version"] += 1
        session.add(WorkflowArtifact(
            artifact_id="artifact_unreferenced_stale_batch",
            task_id=task["task_id"],
            task_revision=current.task_revision + 100,
            artifact_type="EXTRACTION_BATCH",
            schema_version=current.schema_version,
            quote_id=quote["quote_id"],
            document_id=current.document_id,
            graph_run_id=current.graph_run_id,
            payload=stale_payload,
            content_sha256=content_hash(stale_payload),
        ))

    response = http.post(
        f"/api/v1/tasks/{task['task_id']}/fields/corrections",
        headers={"Idempotency-Key": "current-execution-batch"},
        json=body,
    )
    assert response.status_code == 202, response.text


@pytest.mark.parametrize("case", ["stale_task", "stale_fields", "duplicates", "invalid_fields", "foreign_quote", "wrong_scalar"])
def test_batch_correction_rejects_invalid_submission_atomically(batch_review, case):
    http, service, task, _runner, _review, body = batch_review
    import copy
    bad = copy.deepcopy(body)
    if case == "stale_task":
        bad["expected_task_revision"] -= 1
    elif case == "stale_fields":
        for item in bad["corrections"]:
            item["expected_field_version"] += 1
    elif case == "duplicates":
        bad["corrections"].append(bad["corrections"][0])
    elif case == "invalid_fields":
        bad["corrections"][1]["field_name"] = "not_a_field_1"
        bad["corrections"][3]["field_name"] = "not_a_field_2"
        for item in bad["corrections"]:
            item["expected_field_version"] = 1
    elif case == "foreign_quote":
        bad["corrections"][-1]["quote_id"] = "foreign-quote"
    else:
        bad["corrections"][1]["normalized_value"] = 200.0
    response = http.post(f"/api/v1/tasks/{task['task_id']}/fields/corrections",
                         headers={"Idempotency-Key": "invalid-batch"}, json=bad)
    assert response.status_code in (409, 422, 404), response.text
    if case in ("stale_fields", "invalid_fields"):
        assert len(response.json()["error"]["details"]["errors"]) >= 2
    assert service.get_task(task["task_id"])["task_revision"] == body["expected_task_revision"]
    assert http.get(f"/api/v1/tasks/{task['task_id']}/review").json()["problems"] == _review["problems"]


def test_batch_idempotency_key_cannot_represent_different_corrections(batch_review):
    http, _service, task, _runner, _review, body = batch_review
    url = f"/api/v1/tasks/{task['task_id']}/fields/corrections"
    headers = {"Idempotency-Key": "batch-idempotent"}
    assert http.post(url, json=body, headers=headers).status_code == 202
    body["corrections"][0]["reason"] = "Different request"
    conflict = http.post(url, json=body, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"


def test_batch_review_and_corrections_enforce_task_owner(batch_review, tmp_path):
    _http, service, task, _runner, _review, body = batch_review
    outsider = BackendService(service.session_factory, tmp_path / "other", actor_id="outsider")
    http = TestClient(create_app(outsider, readiness_check=lambda: True))
    assert http.get(f"/api/v1/tasks/{task['task_id']}/review").status_code == 404
    assert http.post(f"/api/v1/tasks/{task['task_id']}/fields/corrections",
                     headers={"Idempotency-Key": "outsider"}, json=body).status_code == 404


def test_second_batch_keeps_previous_human_audit_and_invalidates_old_result(batch_review):
    http, service, task, runner, _review, body = batch_review
    url = f"/api/v1/tasks/{task['task_id']}/fields/corrections"
    first = http.post(url, json=body, headers={"Idempotency-Key": "first-batch"}).json()
    runner.run_job(first["job_id"])
    historical = service.list_results(task["task_id"])[0]
    review = http.get(f"/api/v1/tasks/{task['task_id']}/review").json()
    quote = next(q for q in review["quotes"] if q["supplier_id"] == "SUP-023")
    field = next(f for f in quote["fields"] if f["field_name"] == "shipping_fee_amount")
    second_body = {"expected_task_revision": review["task_revision"], "corrections": [{
        "quote_id": quote["quote_id"], "field_name": field["field_name"],
        "expected_field_version": field["field_version"], "raw_value": "300.00",
        "normalized_value": "300.00", "unit": "SGD", "reason": "Supplier updated shipping amount",
    }]}
    second = http.post(url, json=second_body, headers={"Idempotency-Key": "second-batch"})
    assert second.status_code == 202, second.text
    assert service.get_task(task["task_id"])["current_result_id"] is None
    assert not service.get_result(task["task_id"], historical["result_id"])["is_current"]
    assert runner.run_job(second.json()["job_id"])["status"] == "SUCCEEDED"
    result = service.list_results(task["task_id"])[0]["result"]
    assert sorted(s["total_cost"] for s in result["supplier_results"]) == ["7100.00", "7100.00"]
    assert len(result["recommended_quote_ids"]) == 2
    assert len(runner.processor.calls) == 2
    assert not service.list_review_problems(task["task_id"])["review_pending"]

    # Historical evidence must follow the frozen snapshot, never the newest batch.
    frozen = http.get(
        f"/api/v1/tasks/{task['task_id']}/quotes/{quote['quote_id']}/fields",
        params={"result_id": historical["result_id"]},
    )
    assert frozen.status_code == 200
    shipping = next(f for f in frozen.json()["fields"] if f["field_name"] == "shipping_fee_amount")
    assert shipping["normalized_value"] == "200.00"
    stored = service.get_result(task["task_id"], historical["result_id"])
    assert stored["input_snapshot"]["requirement"] == service.get_task(task["task_id"])["requirement"]
    assert stored["snapshot_id"]
    assert http.get(
        f"/api/v1/tasks/{task['task_id']}/quotes/{quote['quote_id']}/fields",
        params={"result_id": "missing-result"},
    ).status_code == 404


def test_batch_submission_does_not_bypass_semantic_review(batch_review):
    http, service, task, runner, _review, body = batch_review
    body["corrections"][0]["normalized_value"] = "PAID"
    response = http.post(f"/api/v1/tasks/{task['task_id']}/fields/corrections",
                         headers={"Idempotency-Key": "bad-enum"}, json=body)
    assert response.status_code == 202
    with pytest.raises(BackendError):
        runner.run_job(response.json()["job_id"])
    review = service.list_review_problems(task["task_id"])
    assert any("NORMALIZED_ENUM_INVALID" in p["codes"] for p in review["problems"])
    assert review["blocking_problem_count"] > 0
    assert service.get_task(task["task_id"])["current_result_id"] is None


@pytest.mark.parametrize("count", [0, 101])
def test_batch_size_contract_is_enforced(batch_review, count):
    http, service, task, _runner, _review, body = batch_review
    body["corrections"] = [body["corrections"][0]] * count
    response = http.post(f"/api/v1/tasks/{task['task_id']}/fields/corrections",
                         headers={"Idempotency-Key": "size"}, json=body)
    assert response.status_code == 422
    assert service.get_task(task["task_id"])["task_revision"] == body["expected_task_revision"]


def test_batch_correction_rejects_old_quote_version(batch_review):
    http, service, task, _runner, review, body = batch_review
    from supplier_comparison.backend.models import Quote
    with service.session_factory.begin() as session:
        session.get(Quote, review["quotes"][0]["quote_id"]).current_version += 1
    response = http.post(f"/api/v1/tasks/{task['task_id']}/fields/corrections",
                         headers={"Idempotency-Key": "stale-quote"}, json=body)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "extraction_batch_stale"


def test_investigation_endpoint_empty_task_and_owner_checks(client, tmp_path):
    http, service = client
    task = http.post("/api/v1/tasks", json={"requirement": REQUIREMENT},
                     headers={"Idempotency-Key": "investigation-task"}).json()
    assert http.get(f"/api/v1/tasks/{task['task_id']}/investigations").json() == []
    outsider = BackendService(service.session_factory, tmp_path / "other", actor_id="outsider")
    other_http = TestClient(create_app(outsider, readiness_check=lambda: True))
    assert other_http.get(f"/api/v1/tasks/{task['task_id']}/investigations").status_code == 404


def test_selection_analysis_and_authorized_simulation_are_read_only(batch_review):
    http, service, task, runner, _review, body = batch_review
    updated = http.post(f"/api/v1/tasks/{task['task_id']}/fields/corrections", json=body,
                        headers={'Idempotency-Key': 'gap-ready'}).json()
    runner.run_job(updated['job_id'])
    before = service.get_task(task['task_id'])
    url = f"/api/v1/tasks/{task['task_id']}"
    response = http.get(url + '/selection-gaps', params={'expected_task_revision': 4})
    assert response.status_code == 200, response.text
    report = response.json()
    assert len(report['gaps']) == 2 and len(report['clarification_drafts']) == 2
    assert sorted(r['confirmed_total_cost'] for r in report['gaps']) == ['7000.00', '7100.00']
    state = runner.graph.get_state({'configurable': {'thread_id': updated['graph_run_id']}}).values
    proof = service.artifact_payload(state['decision_impact_artifact_id'])
    assert report['input_sha256'] == proof['input_sha256']
    trial = http.post(url + '/requirement-simulations', json={
        'expected_task_revision': 4, 'confirm_hypothetical': True,
        'changes': {'budget_amount': '6500.00'}})
    assert trial.status_code == 200, trial.text
    result = trial.json()['result']
    assert result['hypothetical'] and not result['formal_recommendation_allowed']
    assert not result['comparison']['final_recommendation_allowed']
    assert service.get_task(task['task_id']) == before
    exclusion = http.post(url + '/requirement-simulations', json={
        'expected_task_revision': 4, 'confirm_hypothetical': True,
        'changes': {'excluded_supplier_ids': ['SUP-023']}})
    assert exclusion.status_code == 200, exclusion.text
    excluded_result = exclusion.json()['result']
    assert excluded_result['changes']['excluded_supplier_ids'] == ['SUP-023']
    assert len(excluded_result['excluded_quote_ids']) == 1
    assert [row['quote_id'] for row in excluded_result['comparison']['supplier_results']] == [
        next(q['quote_id'] for q in _review['quotes'] if q['supplier_id'] == 'SUP-024')
    ]
    unknown_supplier = http.post(url + '/requirement-simulations', json={
        'expected_task_revision': 4, 'confirm_hypothetical': True,
        'changes': {'excluded_supplier_ids': ['SUP-NOT-IN-TASK']}})
    assert unknown_supplier.status_code == 422
    assert unknown_supplier.json()['error']['code'] == 'simulation_change_invalid'
    assert service.get_task(task['task_id']) == before
    invalid = http.post(url + '/requirement-simulations', json={
        'expected_task_revision': 4, 'confirm_hypothetical': True,
        'changes': {'delivery_deadline': '2026-09-13'}})
    assert invalid.status_code == 422 and invalid.json()['error']['code'] == 'simulation_change_invalid'
    assert service.get_task(task['task_id']) == before
    assert http.get(url + '/selection-gaps', params={'expected_task_revision': 3}).status_code == 409


@pytest.mark.parametrize('authorization', [False, 'true', 1, None])
def test_simulation_cannot_fake_user_authorization(client, authorization):
    http, service = client
    task = service.create_task(_requirement(), idempotency_key='trial-auth')
    response = http.post(f"/api/v1/tasks/{task['task_id']}/requirement-simulations", json={
        'expected_task_revision': 1, 'confirm_hypothetical': authorization,
        'changes': {'budget_amount': '9000'}})
    assert response.status_code == 422
    assert service.get_task(task['task_id'])['task_revision'] == 1


def test_decision_scenario_persists_delta_becomes_stale_and_applies(batch_review, tmp_path):
    http, service, task, runner, review, body = batch_review
    updated = http.post(
        f"/api/v1/tasks/{task['task_id']}/fields/corrections",
        json=body,
        headers={'Idempotency-Key': 'scenario-ready'},
    ).json()
    runner.run_job(updated['job_id'])
    task_id = task['task_id']
    url = f"/api/v1/tasks/{task_id}/decision-scenarios"
    payload = {
        'expected_task_revision': 4,
        'confirm_hypothetical': True,
        'changes': {'excluded_supplier_ids': ['SUP-023']},
    }
    first = http.post(url, json=payload, headers={'Idempotency-Key': 'scenario-1'})
    assert first.status_code == 201, first.text
    scenario = first.json()
    assert scenario['status'] == 'READY' and scenario['is_current']
    assert scenario['baseline']['recommended_quote_ids']
    assert scenario['simulated']['comparison']['recommended_quote_ids']
    assert scenario['delta']['recommendation_changed']
    assert any(row['excluded'] for row in scenario['delta']['supplier_deltas'])
    assert http.post(url, json=payload, headers={'Idempotency-Key': 'scenario-1'}).json() == scenario
    outsider_service = BackendService(
        service.session_factory, tmp_path / 'scenario-outsider', actor_id='scenario-outsider'
    )
    outsider = TestClient(create_app(outsider_service, readiness_check=lambda: True))
    assert outsider.get(url + '/' + scenario['decision_scenario_id']).status_code == 404

    second = http.post(url, json={
        'expected_task_revision': 4,
        'confirm_hypothetical': True,
        'changes': {'ranking_mode': 'FASTEST_CONFIRMED_DELIVERY'},
    }, headers={'Idempotency-Key': 'scenario-2'})
    assert second.status_code == 201, second.text
    assert len(http.get(url).json()['items']) == 2
    assert http.get(url + '/' + scenario['decision_scenario_id']).json() == scenario

    applied = http.post(
        url + '/' + scenario['decision_scenario_id'] + '/apply',
        json={'expected_task_revision': 4},
        headers={'Idempotency-Key': 'apply-scenario-1'},
    )
    assert applied.status_code == 202, applied.text
    application = applied.json()
    assert application['task_revision'] == 5
    assert application['changed_requirement_fields'] == []
    assert application['changed_decision_preference_fields'] == ['excluded_supplier_ids']
    assert application['job_status'] == 'PENDING'
    repeated_apply = http.post(
        url + '/' + scenario['decision_scenario_id'] + '/apply',
        json={'expected_task_revision': 4},
        headers={'Idempotency-Key': 'apply-scenario-1'},
    )
    assert repeated_apply.json() == application
    current = service.get_task(task_id)
    assert current['decision_profile']['preferences']['excluded_supplier_ids'] == ['SUP-023']
    assert current['current_result_id'] is None
    queued_context = service.workflow_context(application['graph_run_id'])
    assert queued_context['decision_profile']['preferences']['excluded_supplier_ids'] == ['SUP-023']
    assert http.get(url + '/' + scenario['decision_scenario_id']).json()['status'] == 'APPLIED'
    assert http.get(url + '/' + second.json()['decision_scenario_id']).json()['status'] == 'STALE'
    stale_apply = http.post(
        url + '/' + second.json()['decision_scenario_id'] + '/apply',
        json={'expected_task_revision': 5},
        headers={'Idempotency-Key': 'apply-stale-scenario'},
    )
    assert stale_apply.status_code == 409
    assert stale_apply.json()['error']['code'] == 'decision_scenario_stale'
    audit = http.get(f"/api/v1/tasks/{task_id}/revisions").json()
    assert audit['revisions'][-1]['change_type'] == 'DECISION_SCENARIO_APPLIED'


def test_decision_scenario_requires_current_result_and_explicit_confirmation(client):
    http, service = client
    task = service.create_task(_requirement(), idempotency_key='scenario-no-result')
    url = f"/api/v1/tasks/{task['task_id']}/decision-scenarios"
    no_result = http.post(url, json={
        'expected_task_revision': 1,
        'confirm_hypothetical': True,
        'changes': {'budget_amount': '9000.00'},
    }, headers={'Idempotency-Key': 'scenario-no-result-create'})
    assert no_result.status_code == 409
    assert no_result.json()['error']['code'] == 'scenario_result_required'
    unconfirmed = http.post(url, json={
        'expected_task_revision': 1,
        'confirm_hypothetical': False,
        'changes': {'budget_amount': '9000.00'},
    }, headers={'Idempotency-Key': 'scenario-unconfirmed'})
    assert unconfirmed.status_code == 422


def test_decision_scenario_apply_updates_hard_requirement_and_profile_atomically(batch_review):
    http, service, task, runner, _review, body = batch_review
    updated = http.post(
        f"/api/v1/tasks/{task['task_id']}/fields/corrections",
        json=body,
        headers={'Idempotency-Key': 'scenario-combined-ready'},
    ).json()
    runner.run_job(updated['job_id'])
    task_id = task['task_id']
    created = http.post(
        f"/api/v1/tasks/{task_id}/decision-scenarios",
        json={
            'expected_task_revision': 4,
            'confirm_hypothetical': True,
            'changes': {
                'budget_amount': '7500.00',
                'ranking_mode': 'FASTEST_CONFIRMED_DELIVERY',
            },
        },
        headers={'Idempotency-Key': 'scenario-combined'},
    )
    assert created.status_code == 201, created.text
    applied = http.post(
        f"/api/v1/tasks/{task_id}/decision-scenarios/{created.json()['decision_scenario_id']}/apply",
        json={'expected_task_revision': 4},
        headers={'Idempotency-Key': 'scenario-combined-apply'},
    )
    assert applied.status_code == 202, applied.text
    assert applied.json()['task_revision'] == 5
    assert applied.json()['changed_requirement_fields'] == ['budget_amount']
    assert applied.json()['changed_decision_preference_fields'] == ['ranking_mode']
    current = service.get_task(task_id)
    assert current['requirement']['budget_amount'] == '7500.00'
    assert current['decision_profile']['preferences']['ranking_mode'] == 'FASTEST_CONFIRMED_DELIVERY'
    revisions = http.get(f"/api/v1/tasks/{task_id}/revisions").json()['revisions']
    assert sum(item['revision'] == 5 for item in revisions) == 1


def test_natural_language_intent_requires_confirmation_before_creating_scenario(batch_review):
    _http, service, task, runner, _review, body = batch_review
    updated = service.correct_fields(
        task_id=task['task_id'], corrections=body['corrections'],
        expected_task_revision=body['expected_task_revision'],
        idempotency_key='intent-ready',
    )
    runner.run_job(updated['job_id'])
    calls = []

    def fixed_parser(message, context):
        calls.append((message, context))
        return {
            'ranking_mode': 'LOWEST_COST_THEN_FASTEST_DELIVERY',
            'excluded_supplier_ids': ['SUP-024'],
            'cost_tolerance_amount': '300.00',
        }, 1

    http = TestClient(create_app(
        service,
        readiness_check=lambda: True,
        decision_intent_parser=fixed_parser,
        decision_intent_provider='fixed-test',
        decision_intent_model_id='fixed-intent-v1',
    ))
    url = f"/api/v1/tasks/{task['task_id']}/decision-intents"
    payload = {
        'expected_task_revision': 4,
        'message': '总价贵 300 新币以内都可以，优先更快的，但排除 SUP-024。',
    }
    parsed = http.post(url, json=payload, headers={'Idempotency-Key': 'parse-intent-1'})
    assert parsed.status_code == 201, parsed.text
    intent = parsed.json()
    assert intent['status'] == 'READY' and intent['is_current']
    assert intent['parsed_changes'] == {
        'ranking_mode': 'LOWEST_COST_THEN_FASTEST_DELIVERY',
        'excluded_supplier_ids': ['SUP-024'],
        'cost_tolerance_amount': '300.00',
    }
    assert '不会直接修改正式需求' in intent['confirmation_text']
    assert intent['attempts'] == 1
    assert calls[0][1]['available_supplier_ids'] == ['SUP-023', 'SUP-024']
    assert service.list_decision_scenarios(task['task_id'])['items'] == []
    assert http.post(url, json=payload, headers={'Idempotency-Key': 'parse-intent-1'}).json() == intent
    assert len(calls) == 1

    rejected = http.post(
        url + '/' + intent['decision_intent_id'] + '/confirm',
        json={'expected_task_revision': 4, 'confirm': False},
        headers={'Idempotency-Key': 'confirm-intent-rejected'},
    )
    assert rejected.status_code == 422
    confirmed = http.post(
        url + '/' + intent['decision_intent_id'] + '/confirm',
        json={'expected_task_revision': 4, 'confirm': True},
        headers={'Idempotency-Key': 'confirm-intent-1'},
    )
    assert confirmed.status_code == 201, confirmed.text
    result = confirmed.json()
    assert result['status'] == 'CONFIRMED'
    assert result['scenario']['status'] == 'READY'
    assert result['scenario']['changes'] == intent['parsed_changes']
    assert http.post(
        url + '/' + intent['decision_intent_id'] + '/confirm',
        json={'expected_task_revision': 4, 'confirm': True},
        headers={'Idempotency-Key': 'confirm-intent-1'},
    ).json() == result
    assert http.get(url + '/' + intent['decision_intent_id']).json()['status'] == 'CONFIRMED'


def test_decision_intent_failure_is_audited_and_idempotently_replayed(batch_review):
    _http, service, task, runner, _review, body = batch_review
    updated = service.correct_fields(
        task_id=task['task_id'], corrections=body['corrections'],
        expected_task_revision=body['expected_task_revision'],
        idempotency_key='intent-failure-ready',
    )
    runner.run_job(updated['job_id'])
    calls = []

    def invalid_parser(message, context):
        calls.append((message, context))
        return {'excluded_supplier_ids': ['SUP-NOT-IN-TASK']}, 2

    http = TestClient(create_app(
        service, readiness_check=lambda: True, decision_intent_parser=invalid_parser,
    ))
    url = f"/api/v1/tasks/{task['task_id']}/decision-intents"
    payload = {'expected_task_revision': 4, 'message': '排除一个不存在的供应商'}
    first = http.post(url, json=payload, headers={'Idempotency-Key': 'invalid-intent'})
    assert first.status_code == 422
    assert first.json()['error']['code'] == 'decision_intent_model_output_invalid'
    repeated = http.post(url, json=payload, headers={'Idempotency-Key': 'invalid-intent'})
    assert repeated.status_code == 422
    assert repeated.json()['error']['code'] == 'decision_intent_model_output_invalid'
    assert len(calls) == 1
    audit = http.get(url).json()['items']
    assert len(audit) == 1
    assert audit[0]['status'] == 'FAILED' and audit[0]['attempts'] == 2
    assert audit[0]['error_code'] == 'decision_intent_model_output_invalid'


def test_ready_decision_intent_becomes_stale_when_task_inputs_change(batch_review):
    _http, service, task, runner, _review, body = batch_review
    updated = service.correct_fields(
        task_id=task['task_id'], corrections=body['corrections'],
        expected_task_revision=body['expected_task_revision'],
        idempotency_key='intent-stale-ready',
    )
    runner.run_job(updated['job_id'])
    http = TestClient(create_app(
        service,
        readiness_check=lambda: True,
        decision_intent_parser=lambda _message, _context: (
            {'ranking_mode': 'FASTEST_CONFIRMED_DELIVERY'}, 1
        ),
    ))
    url = f"/api/v1/tasks/{task['task_id']}/decision-intents"
    parsed = http.post(
        url,
        json={'expected_task_revision': 4, 'message': '改成到货最快优先'},
        headers={'Idempotency-Key': 'parse-stale-intent'},
    ).json()
    requirement = service.get_task(task['task_id'])['requirement']
    requirement['budget_amount'] = '8100.00'
    changed = http.put(
        f"/api/v1/tasks/{task['task_id']}/requirement",
        json={'expected_task_revision': 4, 'requirement': requirement},
        headers={'Idempotency-Key': 'change-after-intent'},
    )
    assert changed.status_code == 202, changed.text
    assert http.get(url + '/' + parsed['decision_intent_id']).json()['status'] == 'STALE'
    confirmation = http.post(
        url + '/' + parsed['decision_intent_id'] + '/confirm',
        json={'expected_task_revision': 5, 'confirm': True},
        headers={'Idempotency-Key': 'confirm-stale-intent'},
    )
    assert confirmation.status_code == 409
    assert confirmation.json()['error']['code'] == 'decision_intent_stale'


def test_async_multi_turn_conversation_streams_validated_events_and_proposes_intent(batch_review):
    http, service, task, runner, _review, body = batch_review
    updated = service.correct_fields(
        task_id=task['task_id'], corrections=body['corrections'],
        expected_task_revision=body['expected_task_revision'],
        idempotency_key='conversation-ready',
    )
    runner.run_job(updated['job_id'])
    base = f"/api/v1/tasks/{task['task_id']}/decision-conversations"
    created = http.post(
        base,
        json={'expected_task_revision': 4, 'title': '成本与交期讨论'},
        headers={'Idempotency-Key': 'create-conversation'},
    )
    assert created.status_code == 201, created.text
    conversation = created.json()
    conversation_id = conversation['conversation_id']
    assert http.get(base, params={'result_id': conversation['base_result_id']}).json()['items'] == [conversation]
    assert http.get(base, params={'result_id': 'another-result'}).json()['items'] == []

    task_state = service.get_task(task['task_id'])
    policy = service.append_artifact(
        task_id=task['task_id'],
        task_revision=task_state['task_revision'],
        graph_run_id=task_state['current_graph_run_id'],
        parent_artifact_id=task_state['current_result_id'],
        artifact_type='POLICY_RETRIEVAL_RESULT',
        payload={
            'retrieval_id': 'RET-chat',
            'status': 'OK',
            'covered_control_codes': ['APPROVED_SUPPLIER'],
            'missing_control_codes': [],
            'citations': [{
                'citation_id': 'CIT-chat-1',
                'control_code': 'APPROVED_SUPPLIER',
                'text': 'Approved suppliers require a current registry record.',
                'policy_set_version': '2026.09.1',
                'document_id': 'POL-001',
                'document_version': '1.0',
                'clause_id': 'approved-1',
                'section': 'Approved suppliers',
                'content_sha256': 'a' * 64,
            }],
        },
    )
    investigation = service.append_artifact(
        task_id=task['task_id'],
        task_revision=task_state['task_revision'],
        graph_run_id=task_state['current_graph_run_id'],
        artifact_type='INVESTIGATION_CASE',
        payload={
            'case_id': 'CASE-chat',
            'kind': 'QUOTE',
            'status': 'WAITING_INPUT',
            'stop_reason': 'EVIDENCE_INSUFFICIENT',
            'goal': 'Confirm the unknown shipping amount.',
            'unknown_fields': ['shipping_fee_amount'],
            'clarification': [{'question': 'Please confirm shipping.'}],
            'observations': [],
        },
    )
    message_url = base + '/' + conversation_id + '/messages'
    first = http.post(
        message_url,
        json={'expected_task_revision': 4, 'message': '现在为什么推荐这个报价？'},
        headers={'Idempotency-Key': 'conversation-message-1'},
    )
    assert first.status_code == 202, first.text
    job_id = first.json()['job']['job_id']
    blocked = http.post(
        message_url,
        json={'expected_task_revision': 4, 'message': '再比较一下交期'},
        headers={'Idempotency-Key': 'conversation-message-blocked'},
    )
    assert blocked.status_code == 409
    assert blocked.json()['error']['code'] == 'conversation_turn_in_progress'
    context = service.conversation_job_context(job_id)
    assert context['recent_messages'][-1]['content'] == '现在为什么推荐这个报价？'
    assert f"REQUEST:{first.json()['message']['message_id']}" in context['allowed_reference_ids']
    assert 'POLICY:CIT-chat-1' in context['allowed_reference_ids']
    assert f"INVESTIGATION:{investigation['artifact_id']}" in context['allowed_reference_ids']
    assert f"COMPLIANCE:{context['result_id']}" in context['allowed_reference_ids']
    assert context['frozen_references']['POLICY:CIT-chat-1']['text'].startswith('Approved suppliers')
    result_ref = 'RESULT:' + context['result_id']
    grounded_refs = [
        result_ref,
        f"REQUEST:{first.json()['message']['message_id']}",
        'POLICY:CIT-chat-1',
        f"INVESTIGATION:{investigation['artifact_id']}",
        f"COMPLIANCE:{context['result_id']}",
    ]
    completed = service.complete_conversation_job(
        job_id,
        turn={
            'assistant_text': '当前推荐来自冻结的确定性比较结果；聊天不会改变该结论。',
            'reference_ids': grounded_refs,
            'changes': None,
        },
        attempts=1,
        provider='fixed-test',
        model_id='fixed-conversation-v1',
    )
    assert completed['job_status'] == 'SUCCEEDED'
    assert completed['message']['role'] == 'ASSISTANT'
    assert completed['message']['reference_ids'] == grounded_refs

    second = http.post(
        message_url,
        json={'expected_task_revision': 4, 'message': '改成到货最快优先。'},
        headers={'Idempotency-Key': 'conversation-message-2'},
    )
    second_context = service.conversation_job_context(second.json()['job']['job_id'])
    assert [row['role'] for row in second_context['recent_messages']] == [
        'USER', 'ASSISTANT', 'USER'
    ]
    proposed = service.complete_conversation_job(
        second.json()['job']['job_id'],
        turn={
            'assistant_text': '可以生成“到货最快优先”的决策情景；请先确认该变更。',
            'reference_ids': ['RESULT:' + second_context['result_id']],
            'changes': {'ranking_mode': 'FASTEST_CONFIRMED_DELIVERY'},
        },
        attempts=1,
        provider='fixed-test',
        model_id='fixed-conversation-v1',
    )
    intent_id = proposed['message']['decision_intent_id']
    assert intent_id
    assert proposed['message']['proposed_changes'] == {
        'ranking_mode': 'FASTEST_CONFIRMED_DELIVERY'
    }
    assert service.list_decision_scenarios(task['task_id'])['items'] == []
    confirmed = http.post(
        f"/api/v1/tasks/{task['task_id']}/decision-intents/{intent_id}/confirm",
        json={'expected_task_revision': 4, 'confirm': True},
        headers={'Idempotency-Key': 'confirm-conversation-intent'},
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()['scenario']['status'] == 'READY'

    events = http.get(
        base + '/' + conversation_id + '/events',
        params={'after': 0, 'follow': 'false'},
    )
    assert events.status_code == 200
    assert events.headers['content-type'].startswith('text/event-stream')
    assert 'event: assistant.delta' in events.text
    assert 'event: assistant.completed' in events.text
    loaded = http.get(base + '/' + conversation_id).json()
    assert [row['role'] for row in loaded['messages']] == [
        'USER', 'ASSISTANT', 'USER', 'ASSISTANT'
    ]
    replay = service.decision_conversation_events(task['task_id'], conversation_id)
    assert [event['sequence'] for event in replay] == list(range(1, len(replay) + 1))
    assert service.decision_conversation_events(
        task['task_id'], conversation_id, after_sequence=replay[-2]['sequence']
    ) == replay[-1:]
    resumed = http.get(
        base + '/' + conversation_id + '/events',
        params={'follow': 'false'},
        headers={'Last-Event-ID': str(replay[-2]['sequence'])},
    )
    assert f"id: {replay[-1]['sequence']}" in resumed.text
    assert 'id: 1\n' not in resumed.text

    third = http.post(
        message_url,
        json={'expected_task_revision': 4, 'message': '请继续解释风险。'},
        headers={'Idempotency-Key': 'conversation-message-3'},
    )
    third_job = third.json()['job']['job_id']
    service.conversation_job_context(third_job)
    service.fail_conversation_job(
        third_job,
        code='model_transport_error',
        message='sanitized model failure',
        attempts=2,
    )
    failed = http.get(base + '/' + conversation_id).json()['messages'][-1]
    assert failed['role'] == 'ASSISTANT' and failed['status'] == 'FAILED'
    assert failed['attempts'] == 2 and failed['error_code'] == 'model_transport_error'


def test_stale_running_conversation_job_is_requeued(batch_review):
    http, service, task, runner, _review, body = batch_review
    updated = service.correct_fields(
        task_id=task['task_id'], corrections=body['corrections'],
        expected_task_revision=body['expected_task_revision'],
        idempotency_key='conversation-recovery-ready',
    )
    runner.run_job(updated['job_id'])
    base = f"/api/v1/tasks/{task['task_id']}/decision-conversations"
    conversation = http.post(
        base,
        json={'expected_task_revision': 4},
        headers={'Idempotency-Key': 'conversation-recovery-create'},
    ).json()
    sent = http.post(
        base + '/' + conversation['conversation_id'] + '/messages',
        json={'expected_task_revision': 4, 'message': '请解释当前结果。'},
        headers={'Idempotency-Key': 'conversation-recovery-message'},
    ).json()
    job_id = sent['job']['job_id']
    service.conversation_job_context(job_id)
    with service.session_factory.begin() as session:
        job = session.get(Job, job_id)
        job.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    assert service.next_pending_job_id() == job_id
    with service.session_factory() as session:
        recovered = session.get(Job, job_id)
        assert recovered.status == 'PENDING'
        assert recovered.error_code == 'conversation_worker_interrupted'

    service.conversation_job_context(job_id)
    with service.session_factory.begin() as session:
        session.get(Job, job_id).started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert service.next_pending_job_id() == job_id
    service.conversation_job_context(job_id)
    with service.session_factory.begin() as session:
        session.get(Job, job_id).started_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    assert service.next_pending_job_id() is None
    with service.session_factory() as session:
        exhausted = session.get(Job, job_id)
        assert exhausted.status == 'FAILED'
        assert exhausted.error_code == 'conversation_retry_exhausted'
    loaded = http.get(base + '/' + conversation['conversation_id']).json()
    assert loaded['messages'][-1]['error_code'] == 'conversation_retry_exhausted'


def test_pending_conversation_is_superseded_when_task_changes(batch_review):
    http, service, task, runner, _review, body = batch_review
    updated = service.correct_fields(
        task_id=task['task_id'], corrections=body['corrections'],
        expected_task_revision=body['expected_task_revision'],
        idempotency_key='conversation-stale-ready',
    )
    runner.run_job(updated['job_id'])
    base = f"/api/v1/tasks/{task['task_id']}/decision-conversations"
    conversation = http.post(
        base,
        json={'expected_task_revision': 4},
        headers={'Idempotency-Key': 'conversation-stale-create'},
    ).json()
    sent = http.post(
        base + '/' + conversation['conversation_id'] + '/messages',
        json={'expected_task_revision': 4, 'message': '请解释当前结果'},
        headers={'Idempotency-Key': 'conversation-stale-message'},
    ).json()
    requirement = service.get_task(task['task_id'])['requirement']
    requirement['budget_amount'] = '8100.00'
    changed = http.put(
        f"/api/v1/tasks/{task['task_id']}/requirement",
        json={'expected_task_revision': 4, 'requirement': requirement},
        headers={'Idempotency-Key': 'conversation-stale-update'},
    )
    assert changed.status_code == 202, changed.text
    loaded = http.get(base + '/' + conversation['conversation_id']).json()
    assert loaded['status'] == 'STALE'
    with pytest.raises(BackendError) as stale:
        service.conversation_job_context(sent['job']['job_id'])
    assert stale.value.code == 'conversation_stale'


def test_unresolved_gaps_remain_pending_and_unreviewed_or_foreign_inputs_are_rejected(batch_review, tmp_path):
    http, service, task, _runner, _review, _body = batch_review
    url = f"/api/v1/tasks/{task['task_id']}/selection-gaps"
    response = http.get(url, params={'expected_task_revision': 3})
    assert response.status_code == 200
    assert all(gap['pending_reasons'] for gap in response.json()['gaps'])
    unreviewed = service.create_task(_requirement(), idempotency_key='unreviewed-gap')
    response = http.get(f"/api/v1/tasks/{unreviewed['task_id']}/selection-gaps", params={'expected_task_revision': 1})
    assert response.status_code == 409 and response.json()['error']['code'] == 'selection_review_required'
    outsider = BackendService(service.session_factory, tmp_path / 'other', actor_id='outsider')
    other = TestClient(create_app(outsider, readiness_check=lambda: True))
    assert other.get(url, params={'expected_task_revision': 3}).status_code == 404


def test_artifact_snapshots_remain_ordered_on_equal_windows_clock_ticks(client, monkeypatch):
    import supplier_comparison.backend.service as service_module
    from supplier_comparison.backend.models import WorkflowArtifact
    from sqlalchemy import select

    _http, service = client
    task = service.create_task(_requirement(), idempotency_key='clock-tick')

    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 18, tzinfo=timezone.utc)

    monkeypatch.setattr(service_module, 'datetime', FrozenClock)
    first = service.append_artifact(task_id=task['task_id'], task_revision=1, artifact_type='TEST', payload={'value': 1})
    second = service.append_artifact(task_id=task['task_id'], task_revision=1, artifact_type='TEST', payload={'value': 2})
    with service.session_factory() as session:
        rows = session.scalars(select(WorkflowArtifact).where(WorkflowArtifact.task_id == task['task_id'])
                              .order_by(WorkflowArtifact.created_at.desc())).all()
        assert [a.artifact_id for a in rows] == [second['artifact_id'], first['artifact_id']]
        assert rows[0].created_at > rows[1].created_at
