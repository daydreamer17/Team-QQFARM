from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from supplier_comparison.backend.api import create_app
from supplier_comparison.backend.models import Base
from supplier_comparison.backend.service import BackendService
from supplier_comparison.backend.service import BackendError
from supplier_comparison.backend.workflow import WorkflowRunner
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
