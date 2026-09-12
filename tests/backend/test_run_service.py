from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base, GraphRun, Issue, Job, Task
from supplier_comparison.backend.service import BackendService, ConflictError
from supplier_comparison.rules import ProcurementRequirement


def _requirement() -> ProcurementRequirement:
    return ProcurementRequirement.model_validate(
        {
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
    )


@pytest.fixture
def service(tmp_path: Path) -> BackendService:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    return BackendService(sessions, tmp_path / "quotes", actor_id="test-user")


def test_start_run_creates_one_shot_job_without_advancing_revision(
    service: BackendService,
) -> None:
    task = service.create_task(_requirement(), idempotency_key="create")

    started = service.start_run(
        task["task_id"], expected_task_revision=1, idempotency_key="run-1"
    )

    assert started["task_revision"] == 1
    assert started["job_type"] == "START"
    assert started["job_status"] == "PENDING"
    with service.session_factory() as session:
        stored_task = session.get(Task, task["task_id"])
        graph = session.get(GraphRun, started["graph_run_id"])
        job = session.get(Job, started["job_id"])
    assert stored_task is not None and stored_task.status == "QUEUED"
    assert graph is not None and graph.started_revision == 1
    assert graph.effective_revision == 1
    assert job is not None and job.graph_run_id == graph.graph_run_id


def test_answer_issue_advances_revision_and_creates_idempotent_resume_job(
    service: BackendService,
) -> None:
    task = service.create_task(_requirement(), idempotency_key="create")
    started = service.start_run(
        task["task_id"], expected_task_revision=1, idempotency_key="run-1"
    )
    issue = service.open_issue(
        task_id=task["task_id"],
        graph_run_id=started["graph_run_id"],
        task_revision=1,
        issue_type="CONFIRM_MISSING",
        quote_id="quote-b",
        field_name="shipping_fee_status",
        question="Confirm that the PDF does not state shipping.",
        answer_schema={"answer_type": "CONFIRM_MISSING"},
    )
    task_state = service.get_task(task["task_id"])
    assert task_state["current_issue"]["issue_id"] == issue["issue_id"]
    assert task_state["current_issue"]["created_by"] == "test-user"
    assert task_state["current_job"] == {
        "job_id": started["job_id"],
        "job_type": "START",
        "job_status": "PENDING",
        "task_revision": 1,
    }
    answer = {"answer_type": "CONFIRM_MISSING"}

    first = service.answer_issue(
        task["task_id"],
        issue["issue_id"],
        expected_task_revision=1,
        answer=answer,
        idempotency_key="answer-1",
    )
    repeated = service.answer_issue(
        task["task_id"],
        issue["issue_id"],
        expected_task_revision=1,
        answer=answer,
        idempotency_key="answer-1",
    )

    assert repeated == first
    assert first["task_revision"] == 2
    assert first["job_type"] == "RESUME"
    with service.session_factory() as session:
        stored_issue = session.get(Issue, issue["issue_id"])
        graph = session.get(GraphRun, started["graph_run_id"])
        jobs = session.scalars(
            select(Job).where(Job.graph_run_id == started["graph_run_id"])
        ).all()
    assert stored_issue is not None and stored_issue.status == "RESOLVED"
    assert stored_issue.answer_payload == answer
    assert graph is not None and graph.effective_revision == 2
    assert [job.job_type for job in jobs] == ["START", "RESUME"]


def test_answer_issue_rejects_stale_revision_and_second_answer(
    service: BackendService,
) -> None:
    task = service.create_task(_requirement(), idempotency_key="create")
    started = service.start_run(
        task["task_id"], expected_task_revision=1, idempotency_key="run-1"
    )
    issue = service.open_issue(
        task_id=task["task_id"],
        graph_run_id=started["graph_run_id"],
        task_revision=1,
        issue_type="SHIPPING_AMOUNT",
        quote_id="quote-b",
        field_name="shipping_fee_amount",
        question="Provide shipping in SGD.",
        answer_schema={"answer_type": "SHIPPING_AMOUNT", "currency": "SGD"},
    )

    with pytest.raises(ConflictError) as stale:
        service.answer_issue(
            task["task_id"],
            issue["issue_id"],
            expected_task_revision=0,
            answer={"answer_type": "SHIPPING_AMOUNT", "amount": "200.00", "currency": "SGD"},
            idempotency_key="stale",
        )
    assert stale.value.code == "task_revision_conflict"

    service.answer_issue(
        task["task_id"],
        issue["issue_id"],
        expected_task_revision=1,
        answer={"answer_type": "SHIPPING_AMOUNT", "amount": "200.00", "currency": "SGD"},
        idempotency_key="answer-1",
    )
    with pytest.raises(ConflictError) as resolved:
        service.answer_issue(
            task["task_id"],
            issue["issue_id"],
            expected_task_revision=2,
            answer={"answer_type": "SHIPPING_AMOUNT", "amount": "250.00", "currency": "SGD"},
            idempotency_key="answer-2",
        )
    assert resolved.value.code == "issue_not_open"


def test_new_quote_supersedes_old_run_issue_job_and_current_result(
    service: BackendService,
) -> None:
    task = service.create_task(_requirement(), idempotency_key="create")
    first_quote = service.upload_quote(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-022",
        original_filename="supplier-a.pdf",
        media_type="application/pdf",
        content=b"supplier-a",
        idempotency_key="upload-a",
    )
    started = service.start_run(
        task["task_id"],
        expected_task_revision=first_quote["task_revision"],
        idempotency_key="run",
    )
    issue = service.open_issue(
        task_id=task["task_id"],
        graph_run_id=started["graph_run_id"],
        task_revision=first_quote["task_revision"],
        issue_type="CONFIRM_MISSING",
        quote_id=first_quote["quote_id"],
        field_name="shipping_fee_status",
        question="Confirm missing shipping.",
        answer_schema={"answer_type": "CONFIRM_MISSING"},
    )
    service.publish_result(
        task_id=task["task_id"],
        graph_run_id=started["graph_run_id"],
        task_revision=first_quote["task_revision"],
        snapshot_id="snapshot-old",
        result_id="result-old",
    )

    second_quote = service.upload_quote(
        task["task_id"],
        expected_task_revision=first_quote["task_revision"],
        supplier_id="SUP-023",
        original_filename="supplier-b.pdf",
        media_type="application/pdf",
        content=b"supplier-b",
        idempotency_key="upload-b",
    )

    state = service.get_task(task["task_id"])
    assert second_quote["task_revision"] == 3
    assert state["current_graph_run_id"] is None
    assert state["current_snapshot_id"] is None
    assert state["current_result_id"] is None
    assert state["current_issue"] is None
    assert state["current_job"] is None
    with service.session_factory() as session:
        graph = session.get(GraphRun, started["graph_run_id"])
        stored_issue = session.get(Issue, issue["issue_id"])
        job = session.get(Job, started["job_id"])
    assert graph is not None and graph.status == "SUPERSEDED"
    assert stored_issue is not None and stored_issue.status == "SUPERSEDED"
    assert job is not None and job.status == "SUPERSEDED"

    with pytest.raises(ConflictError) as stale_issue:
        service.answer_issue(
            task["task_id"],
            issue["issue_id"],
            expected_task_revision=second_quote["task_revision"],
            answer={"answer_type": "CONFIRM_MISSING"},
            idempotency_key="stale-answer",
        )
    assert stale_issue.value.code == "issue_not_open"
