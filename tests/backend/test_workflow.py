from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import (
    Base,
    DocumentExecution,
    GraphRun,
    Job,
    TaskRevision,
    WorkflowArtifact,
)
from supplier_comparison.backend.service import BackendService
from supplier_comparison.backend.service import ConflictError
from supplier_comparison.backend.workflow import WorkflowRunner
from supplier_comparison.extraction.adapters import ModelCallBudget
from supplier_comparison.extraction.contracts import DocumentContext, ExtractionBatch
from supplier_comparison.extraction.csv_parser import FixedCsvQuoteParser
from supplier_comparison.extraction.dictionary import QuoteDictionary
from supplier_comparison.rules import ProcurementRequirement


ROOT = Path(__file__).resolve().parents[2]
DICTIONARY_PATH = ROOT / "data/contracts/quote_data_field.csv"
CANONICAL_QUOTES = ROOT / "data/generated/inputs/development/quote_V1/quotes.csv"


def _requirement() -> ProcurementRequirement:
    with (
        ROOT / "data/generated/inputs/development/quote_V2/procurement_requirement_v2.csv"
    ).open("r", encoding="utf-8-sig", newline="") as handle:
        row = next(csv.DictReader(handle))
    row["secondary_preference"] = row["secondary_preference"] or None
    return ProcurementRequirement.model_validate(row)


class CanonicalCsvProcessor:
    """Deterministic B parser boundary; only the external model is replaced."""

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.dictionary = QuoteDictionary.load(DICTIONARY_PATH)
        self.calls: list[str] = []
        self.budget_graph_run_ids: list[str] = []

    def process(
        self,
        *,
        path: Path,
        media_type: str,
        context: DocumentContext,
        budget: ModelCallBudget,
    ) -> ExtractionBatch:
        del path, media_type
        self.calls.append(context.document_id)
        self.budget_graph_run_ids.append(budget.graph_run_id)
        with CANONICAL_QUOTES.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or ())
            source = next(row for row in reader if row["supplier_id"] == context.supplier_id)
        source.update(
            scenario_id=context.scenario_id or "",
            quote_id=context.quote_id,
            quote_version=str(context.quote_version),
            document_id=context.document_id,
            supplier_id=context.supplier_id or "",
        )
        generated = self.work_dir / f"{context.document_id}.csv"
        with generated.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow(source)
        return FixedCsvQuoteParser(self.dictionary).parse_row(generated, context, 2)


def test_two_interrupt_workflow_resumes_without_reextracting_documents(
    tmp_path: Path,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    service = BackendService(sessions, tmp_path / "quotes", actor_id="test-user")
    task = service.create_task(
        _requirement(), idempotency_key="create", scenario_id="MCU-DEMO-001"
    )
    revision = task["task_revision"]
    for alias, supplier in (("a", "SUP-022"), ("b", "SUP-023"), ("c", "SUP-024")):
        uploaded = service.upload_quote(
            task["task_id"],
            expected_task_revision=revision,
            supplier_id=supplier,
            original_filename=f"supplier-{alias}.csv",
            media_type="text/csv",
            content=f"placeholder-{alias}".encode(),
            idempotency_key=f"upload-{alias}",
            is_synthetic=True,
        )
        revision = uploaded["task_revision"]
    started = service.start_run(
        task["task_id"],
        expected_task_revision=revision,
        idempotency_key="run",
        provider="fixed",
        model_id="fixed-output",
        environment="FIXED_TEST",
        prompt_version="quote-extraction/1.0.0",
    )
    processor = CanonicalCsvProcessor(tmp_path)
    checkpointer = InMemorySaver()

    first_runner = WorkflowRunner(
        service,
        processor=processor,
        checkpointer=checkpointer,
        dictionary_path=DICTIONARY_PATH,
        evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
    )
    first = first_runner.run_job(started["job_id"])
    assert first["status"] == "WAITING_INPUT"
    assert first["issue"]["issue_type"] == "CONFIRM_MISSING"
    checkpoint_values = first_runner.graph.get_state(
        {"configurable": {"thread_id": started["graph_run_id"]}}
    ).values
    assert "requirement" not in checkpoint_values
    assert "documents" not in checkpoint_values
    assert "storage_path" not in str(checkpoint_values)

    confirmation = service.answer_issue(
        task["task_id"],
        first["issue"]["issue_id"],
        expected_task_revision=revision,
        answer={"answer_type": "CONFIRM_MISSING"},
        idempotency_key="confirm-missing",
    )
    second = WorkflowRunner(
        service,
        processor=processor,
        checkpointer=checkpointer,
        dictionary_path=DICTIONARY_PATH,
        evaluated_at=datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc),
    ).run_job(confirmation["job_id"])
    assert second["status"] == "WAITING_INPUT"
    assert second["issue"]["issue_type"] == "SHIPPING_AMOUNT"
    draft = service.list_results(task["task_id"])[0]["result"]
    assert draft["disposition"] == "PENDING_INPUT"
    assert draft["evaluated_at"] == "2026-09-14T01:00:00Z"
    assert draft["final_recommendation_allowed"] is False
    supplier_a = next(
        item
        for item in draft["supplier_results"]
        if item["supplier_name"] == "Redwood Components"
    )
    assert supplier_a["status"] == "INFEASIBLE"
    assert supplier_a["actual_quantity"] == 2000
    assert supplier_a["total_cost"] == "12800.00"
    supplier_b = next(
        item
        for item in draft["supplier_results"]
        if item["supplier_name"] == "Schwarzwald Circuits"
    )
    assert supplier_b["status"] == "PENDING"
    assert supplier_b["known_cost_subtotal"] == "6800.00"
    draft_supplier_c = next(
        item
        for item in draft["supplier_results"]
        if item["supplier_name"] == "Sterling Components"
    )
    assert draft_supplier_c["status"] == "FEASIBLE"
    assert draft_supplier_c["total_cost"] == "7100.00"

    shipping = service.answer_issue(
        task["task_id"],
        second["issue"]["issue_id"],
        expected_task_revision=revision + 1,
        answer={
            "answer_type": "SHIPPING_AMOUNT",
            "amount": "200.00",
            "currency": "SGD",
        },
        idempotency_key="shipping-amount",
    )
    final = WorkflowRunner(
        service,
        processor=processor,
        checkpointer=checkpointer,
        dictionary_path=DICTIONARY_PATH,
        evaluated_at=datetime(2026, 9, 16, 1, 0, tzinfo=timezone.utc),
    ).run_job(shipping["job_id"])

    assert final["status"] == "SUCCEEDED"
    result = service.list_results(task["task_id"])[0]["result"]
    assert result["disposition"] == "RECOMMENDATION_AVAILABLE"
    assert result["recommended_quote_ids"] == [supplier_b["quote_id"]]
    assert next(
        item for item in result["supplier_results"] if item["quote_id"] == supplier_b["quote_id"]
    )["total_cost"] == "7000.00"
    fields = service.list_quote_fields(task["task_id"], supplier_b["quote_id"])
    shipping_field = next(
        field for field in fields["fields"] if field["field_name"] == "shipping_fee_amount"
    )
    assert shipping_field["origin"] == "USER_INPUT"
    assert shipping_field["normalized_value"] == "200.00"
    assert "storage_path" not in str(fields)
    assert len(processor.calls) == 3
    assert set(processor.budget_graph_run_ids) == {started["graph_run_id"]}
    with sessions() as session:
        review_events = session.scalars(
            select(WorkflowArtifact).where(
                WorkflowArtifact.graph_run_id == started["graph_run_id"],
                WorkflowArtifact.artifact_type == "REVIEW_EVENT",
            )
        ).all()
        correction_events = session.scalars(
            select(WorkflowArtifact).where(
                WorkflowArtifact.graph_run_id == started["graph_run_id"],
                WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
            )
        ).all()
        review_envelopes = session.scalars(
            select(WorkflowArtifact).where(
                WorkflowArtifact.graph_run_id == started["graph_run_id"],
                WorkflowArtifact.artifact_type == "REVIEW_ENVELOPE",
            )
        ).all()
        revision_changes = session.scalars(
            select(TaskRevision)
            .where(TaskRevision.task_id == task["task_id"])
            .order_by(TaskRevision.revision)
        ).all()
        document_executions = session.scalars(
            select(DocumentExecution).where(
                DocumentExecution.graph_run_id == started["graph_run_id"]
            )
        ).all()
    assert len(review_events) == 1
    assert review_events[0].task_revision == revision + 1
    assert review_events[0].payload["reviewer_id"] == "test-user"
    assert len(correction_events) == 2
    assert {event.payload["field_name"] for event in correction_events} == {
        "shipping_fee_status",
        "shipping_fee_amount",
    }
    assert {event.payload["reviewer_id"] for event in correction_events} == {
        "test-user"
    }
    assert review_envelopes
    assert {artifact.schema_version for artifact in review_envelopes} == {
        "review-envelope/1.0.0"
    }
    assert [change.change_type for change in revision_changes[-2:]] == [
        "ISSUE_ANSWERED:CONFIRM_MISSING",
        "ISSUE_ANSWERED:SHIPPING_AMOUNT",
    ]
    assert len(document_executions) == 3
    assert {execution.status for execution in document_executions} == {"REVIEWED"}
    assert all(execution.review_artifact_id for execution in document_executions)

    supplier_c = next(
        item
        for item in result["supplier_results"]
        if item["supplier_name"] == "Sterling Components"
    )
    correction = service.correct_field(
        task_id=task["task_id"],
        quote_id=supplier_c["quote_id"],
        field_name="shipping_fee_amount",
        expected_task_revision=revision + 2,
        raw_value="S$0.00",
        normalized_value="0.00",
        unit="SGD",
        reason="Correct a confirmed extraction error.",
        idempotency_key="correct-c-shipping",
    )
    repeated_correction = service.correct_field(
        task_id=task["task_id"],
        quote_id=supplier_c["quote_id"],
        field_name="shipping_fee_amount",
        expected_task_revision=revision + 2,
        raw_value="S$0.00",
        normalized_value="0.00",
        unit="SGD",
        reason="Correct a confirmed extraction error.",
        idempotency_key="correct-c-shipping",
    )
    assert repeated_correction == correction
    assert service.get_task(task["task_id"])["task_revision"] == revision + 3
    assert correction["graph_run_id"] != started["graph_run_id"]
    with sessions() as session:
        correction_graph = session.get(GraphRun, correction["graph_run_id"])
    assert correction_graph is not None
    assert (
        correction_graph.provider,
        correction_graph.model_id,
        correction_graph.environment,
        correction_graph.prompt_version,
    ) == (
        "fixed",
        "fixed-output",
        "FIXED_TEST",
        "quote-extraction/1.0.0",
    )
    corrected = WorkflowRunner(
        service,
        processor=processor,
        checkpointer=checkpointer,
        dictionary_path=DICTIONARY_PATH,
        evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
    ).run_job(correction["job_id"])
    assert corrected["status"] == "SUCCEEDED"
    result_history = service.list_results(task["task_id"])
    assert result_history[0]["is_current"] is True
    corrected_result = result_history[0]["result"]
    assert corrected_result["recommended_quote_ids"] == [supplier_c["quote_id"]]
    assert next(
        item
        for item in corrected_result["supplier_results"]
        if item["quote_id"] == supplier_c["quote_id"]
    )["total_cost"] == "6600.00"
    assert len(processor.calls) == 3
    previous = service.get_result(task["task_id"], final["result_id"])
    assert previous["is_current"] is False
    assert next(
        item
        for item in previous["result"]["supplier_results"]
        if item["quote_id"] == supplier_b["quote_id"]
    )["total_cost"] == "7000.00"

    with pytest.raises(ConflictError) as late:
        service.publish_result(
            task_id=task["task_id"],
            graph_run_id=started["graph_run_id"],
            task_revision=revision + 2,
            snapshot_id="old-snapshot",
            result_id="old-result",
        )
    assert late.value.code == "stale_result_publish"


def test_worker_failure_does_not_persist_raw_exception_text(tmp_path: Path) -> None:
    class FailingProcessor:
        def process(self, **_kwargs):
            raise RuntimeError("Authorization: secret-provider-token")

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    service = BackendService(sessions, tmp_path / "quotes", actor_id="test-user")
    task = service.create_task(
        _requirement(), idempotency_key="failure-create", scenario_id="MCU-DEMO-001"
    )
    uploaded = service.upload_quote(
        task["task_id"],
        expected_task_revision=1,
        supplier_id="SUP-022",
        original_filename="supplier-a.pdf",
        media_type="application/pdf",
        content=b"placeholder",
        idempotency_key="failure-upload",
        is_synthetic=True,
    )
    started = service.start_run(
        task["task_id"],
        expected_task_revision=uploaded["task_revision"],
        idempotency_key="failure-run",
    )

    runner = WorkflowRunner(
        service,
        processor=FailingProcessor(),
        checkpointer=InMemorySaver(),
        dictionary_path=DICTIONARY_PATH,
        evaluated_at=datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc),
    )
    with pytest.raises(RuntimeError, match="secret-provider-token"):
        runner.run_job(started["job_id"])

    with sessions() as session:
        job = session.get(Job, started["job_id"])
    assert job is not None
    assert job.error_code == "workflow_failed"
    assert job.error_message == "Workflow execution failed."
