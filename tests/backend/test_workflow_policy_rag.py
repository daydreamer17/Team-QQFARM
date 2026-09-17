from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from supplier_comparison.backend.models import Base, WorkflowArtifact
from supplier_comparison.backend.service import BackendService
from supplier_comparison.backend.workflow import WorkflowRunner
from supplier_comparison.rag.contracts import (
    PolicyCitation,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResult,
    RetrievalStatus,
)

from .test_workflow import CanonicalCsvProcessor, DICTIONARY_PATH, _requirement


POLICY_SET_VERSION = "2026.09.1"
POLICY_INDEX_VERSION = "pidx-workflow-test"
REQUIRED_CONTROL_CODES = {
    "APPROVED_SUPPLIER",
    "ROHS_COMPLIANCE",
    "AMOUNT_APPROVAL",
}


class RecordingPolicyRetriever:
    def __init__(self, *, failing_control_code: str | None = None) -> None:
        self.failing_control_code = failing_control_code
        self.calls: list[RetrievalRequest] = []

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        self.calls.append(request)
        control_code = request.required_control_codes[0]
        retrieval_id = f"RET-{len(self.calls)}"
        if control_code == self.failing_control_code:
            return RetrievalResult(
                retrieval_id=retrieval_id,
                status=RetrievalStatus.NO_EVIDENCE,
                policy_set_version=request.policy_set_version,
                policy_index_version=request.policy_index_version,
                embedding_model="fixed-embedding",
                rerank_model="fixed-rerank",
                filters={"category": request.category, "region": request.region},
                covered_control_codes=[],
                missing_control_codes=[control_code],
                citations=[],
                candidates=[],
                latency_ms={"total": 0.0},
                attempts={"embedding": 1, "rerank": 1},
            )
        text = f"Policy evidence for {control_code}."
        citation = PolicyCitation(
            citation_id=f"CIT-{len(self.calls)}",
            retrieval_id=retrieval_id,
            policy_set_version=request.policy_set_version,
            policy_id=f"POL-{control_code}",
            document_id=f"DOC-{control_code}",
            document_version="1.0.0",
            clause_id=f"CLAUSE-{control_code}",
            section=control_code,
            text=text,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            control_code=control_code,
            bm25_rank=1,
            bm25_score=1.0,
            vector_rank=1,
            vector_score=1.0,
            fusion_rank=1,
            fusion_score=1.0,
            rerank_rank=1,
            rerank_score=1.0,
        )
        return RetrievalResult(
            retrieval_id=retrieval_id,
            status=RetrievalStatus.OK,
            policy_set_version=request.policy_set_version,
            policy_index_version=request.policy_index_version,
            embedding_model="fixed-embedding",
            rerank_model="fixed-rerank",
            filters={"category": request.category, "region": request.region},
            covered_control_codes=[control_code],
            missing_control_codes=[],
            citations=[citation],
            candidates=[
                RetrievalCandidate(
                    clause_id=citation.clause_id,
                    bm25_rank=1,
                    bm25_score=1.0,
                    vector_rank=1,
                    vector_score=1.0,
                    fusion_rank=1,
                    fusion_score=1.0,
                    rerank_rank=1,
                    rerank_score=1.0,
                )
            ],
            latency_ms={"total": 0.0},
            attempts={"embedding": 1, "rerank": 1},
        )


def _run_single_complete_quote(
    tmp_path: Path,
    retriever: RecordingPolicyRetriever,
    *,
    supplier_id: str = "SUP-024",
) -> tuple[BackendService, dict, dict, WorkflowRunner]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    service = BackendService(sessions, tmp_path / "quotes", actor_id="test-user")
    task = service.create_task(
        _requirement(),
        idempotency_key="create",
        scenario_id="MCU-DEMO-001",
        policy_set_version=POLICY_SET_VERSION,
        policy_index_version=POLICY_INDEX_VERSION,
        policy_category="Electronics",
        policy_region="SG",
    )
    uploaded = service.upload_quote(
        task["task_id"],
        expected_task_revision=task["task_revision"],
        supplier_id=supplier_id,
        original_filename=f"{supplier_id}.csv",
        media_type="text/csv",
        content=b"placeholder-c",
        idempotency_key="upload-c",
        is_synthetic=True,
    )
    started = service.start_run(
        task["task_id"],
        expected_task_revision=uploaded["task_revision"],
        idempotency_key="run",
        provider="fixed",
        model_id="fixed-output",
        environment="FIXED_TEST",
        prompt_version="quote-extraction/1.0.0",
    )
    runner = WorkflowRunner(
        service,
        processor=CanonicalCsvProcessor(tmp_path),
        policy_retriever=retriever,
        checkpointer=InMemorySaver(),
        dictionary_path=DICTIONARY_PATH,
        evaluated_at=datetime(2026, 9, 17, 1, 0, tzinfo=timezone.utc),
    )
    outcome = runner.run_job(started["job_id"])
    return service, task, outcome, runner


def test_published_result_requires_all_policy_retrievals_to_succeed(tmp_path: Path) -> None:
    retriever = RecordingPolicyRetriever()

    service, task, outcome, runner = _run_single_complete_quote(tmp_path, retriever)

    assert outcome["status"] == "SUCCEEDED"
    assert {call.required_control_codes[0] for call in retriever.calls} == REQUIRED_CONTROL_CODES
    assert all(call.task_id == task["task_id"] for call in retriever.calls)
    assert all(call.policy_set_version == POLICY_SET_VERSION for call in retriever.calls)
    assert all(call.policy_index_version == POLICY_INDEX_VERSION for call in retriever.calls)
    assert len({call.snapshot_id for call in retriever.calls}) == 1

    current = service.list_results(task["task_id"])[0]
    assert current["is_current"] is True
    assert {item["status"] for item in current["policy_retrievals"]} == {"OK"}
    assert {
        item["citations"][0]["control_code"] for item in current["policy_retrievals"]
    } == REQUIRED_CONTROL_CODES

    state = runner.graph.get_state(
        {"configurable": {"thread_id": outcome["graph_run_id"]}}
    ).values
    assert "citations" not in state
    assert "Policy evidence" not in str(state)
    with service.session_factory() as session:
        snapshot = session.scalar(
            select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task["task_id"],
                WorkflowArtifact.artifact_type == "INPUT_SNAPSHOT",
            )
        )
    assert snapshot is not None
    assert snapshot.payload["policy_set_version"] == POLICY_SET_VERSION
    assert snapshot.payload["policy_index_version"] == POLICY_INDEX_VERSION


def test_missing_policy_evidence_interrupts_and_does_not_publish_result(tmp_path: Path) -> None:
    retriever = RecordingPolicyRetriever(failing_control_code="ROHS_COMPLIANCE")

    service, task, outcome, _runner = _run_single_complete_quote(tmp_path, retriever)

    assert outcome["status"] == "WAITING_INPUT"
    assert outcome["issue"]["issue_type"] == "POLICY_EVIDENCE_REVIEW"
    assert outcome["issue"]["answer_schema"]["retrieval_statuses"]["ROHS_COMPLIANCE"] == (
        "NO_EVIDENCE"
    )
    task_state = service.get_task(task["task_id"])
    assert task_state["current_result_id"] is None
    assert task_state["status"] == "NEEDS_INPUT"
    draft = service.list_results(task["task_id"])[0]
    assert draft["is_current"] is False
    assert {item["status"] for item in draft["policy_retrievals"]} == {
        "OK",
        "NO_EVIDENCE",
    }


def test_policy_retrieval_can_resume_after_a_transient_failure(tmp_path: Path) -> None:
    retriever = RecordingPolicyRetriever(failing_control_code="ROHS_COMPLIANCE")
    service, task, first, runner = _run_single_complete_quote(tmp_path, retriever)
    retriever.failing_control_code = None

    retry = service.answer_issue(
        task["task_id"],
        first["issue"]["issue_id"],
        expected_task_revision=2,
        answer={"answer_type": "RETRY_POLICY_RETRIEVAL"},
        idempotency_key="retry-policy",
    )
    resumed = runner.run_job(retry["job_id"])

    assert resumed["status"] == "SUCCEEDED"
    assert len(retriever.calls) == 6
    assert {call.task_revision for call in retriever.calls[3:]} == {3}
    current = service.list_results(task["task_id"])[0]
    assert current["is_current"] is True
    assert {item["status"] for item in current["policy_retrievals"]} == {"OK"}


def test_no_feasible_result_does_not_call_policy_retrieval(tmp_path: Path) -> None:
    retriever = RecordingPolicyRetriever(failing_control_code="ROHS_COMPLIANCE")

    service, task, outcome, _runner = _run_single_complete_quote(
        tmp_path, retriever, supplier_id="SUP-022"
    )

    assert outcome["status"] == "SUCCEEDED"
    assert retriever.calls == []
    current = service.list_results(task["task_id"])[0]
    assert current["is_current"] is True
    assert current["result"]["disposition"] == "NO_FEASIBLE_QUOTES"
    assert current["policy_retrievals"] == []
