from __future__ import annotations

import hashlib
import json
from datetime import timezone
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, Callable
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from supplier_comparison.rules import ProcurementRequirement
from supplier_comparison.extraction import (
    CorrectionAction,
    CorrectionEvent,
    CriticalityContext,
    ExtractionBatch,
    ReviewSeverity,
    apply_candidate_correction,
    review_extraction_batch,
)
from supplier_comparison.extraction.dictionary import QuoteDictionary

from .models import (
    Document,
    DocumentExecution,
    GraphRun,
    IdempotencyRecord,
    Issue,
    Job,
    Quote,
    RequirementRecord,
    Task,
    TaskRevision,
    WorkflowArtifact,
)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class BackendError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ConflictError(BackendError):
    pass


class NotFoundError(BackendError):
    pass


class BackendService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        storage_root: str | Path,
        *,
        actor_id: str,
        quote_dictionary_path: str | Path = "data/contracts/quote_data_field.csv",
    ) -> None:
        self.session_factory = session_factory
        self.storage_root = Path(storage_root)
        self.actor_id = actor_id
        self.quote_dictionary = QuoteDictionary.load(quote_dictionary_path)

    @staticmethod
    def _supersede_current_graph(session: Session, task: Task) -> GraphRun | None:
        """Invalidate execution state when an external input changes."""

        if task.current_graph_run_id is None:
            task.current_snapshot_id = None
            task.current_result_id = None
            return None
        graph = session.get(GraphRun, task.current_graph_run_id)
        if graph is not None:
            graph.status = "SUPERSEDED"
            for issue in session.scalars(
                select(Issue).where(
                    Issue.graph_run_id == graph.graph_run_id,
                    Issue.status == "OPEN",
                )
            ):
                issue.status = "SUPERSEDED"
            for job in session.scalars(
                select(Job).where(
                    Job.graph_run_id == graph.graph_run_id,
                    Job.status.in_(("PENDING", "RUNNING", "WAITING_INPUT")),
                )
            ):
                job.status = "SUPERSEDED"
        task.current_graph_run_id = None
        task.current_snapshot_id = None
        task.current_result_id = None
        return graph

    def _existing_idempotent(
        self,
        session: Session,
        *,
        operation: str,
        key: str,
        request_sha256: str,
    ) -> dict[str, Any] | None:
        record = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == self.actor_id,
                IdempotencyRecord.operation == operation,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        if record is None:
            return None
        if record.request_sha256 != request_sha256:
            raise ConflictError(
                "idempotency_key_reused",
                "Idempotency-Key was already used with a different request.",
            )
        return dict(record.response_payload)

    def _save_idempotent(
        self,
        session: Session,
        *,
        operation: str,
        key: str,
        request_sha256: str,
        response_status: int,
        response: dict[str, Any],
    ) -> None:
        session.add(
            IdempotencyRecord(
                idempotency_id=new_id("idem"),
                actor_id=self.actor_id,
                operation=operation,
                idempotency_key=key,
                request_sha256=request_sha256,
                response_status=response_status,
                response_payload=response,
            )
        )

    def create_task(
        self,
        requirement: ProcurementRequirement,
        *,
        idempotency_key: str,
        scenario_id: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "requirement": requirement.model_dump(mode="json"),
            "scenario_id": scenario_id,
        }
        request_sha = content_hash(request)
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation="create_task",
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task_id = new_id("task")
            task = Task(
                task_id=task_id,
                owner_id=self.actor_id,
                scenario_id=scenario_id,
                current_revision=1,
                status="DRAFT",
            )
            session.add(task)
            # SQLAlchemy cannot infer object dependency ordering from scalar FK
            # values alone when no ORM relationship is assigned.
            session.flush()
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=1,
                    change_type="TASK_CREATED",
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                )
            )
            requirement_payload = requirement.model_dump(mode="json")
            session.add(
                RequirementRecord(
                    requirement_id=new_id("req"),
                    task_id=task_id,
                    task_revision=1,
                    requirement_version=1,
                    payload=requirement_payload,
                    content_sha256=content_hash(requirement_payload),
                )
            )
            response = {
                "task_id": task_id,
                "task_revision": 1,
                "status": "DRAFT",
            }
            self._save_idempotent(
                session,
                operation="create_task",
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=201,
                response=response,
            )
            return response

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            requirement = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            graph = (
                session.get(GraphRun, task.current_graph_run_id)
                if task.current_graph_run_id
                else None
            )
            issue = (
                session.get(Issue, graph.current_interrupt_issue_id)
                if graph is not None and graph.current_interrupt_issue_id
                else None
            )
            job = (
                session.scalar(
                    select(Job)
                    .where(Job.graph_run_id == graph.graph_run_id)
                    .order_by(Job.created_at.desc(), Job.job_id.desc())
                )
                if graph is not None
                else None
            )
            graph_has_corrections = bool(
                graph is not None
                and session.scalar(
                    select(WorkflowArtifact.artifact_id).where(
                        WorkflowArtifact.graph_run_id == graph.graph_run_id,
                        WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
                    )
                )
            )
            correction_batch_incomplete = False
            if graph is not None and graph_has_corrections:
                correction_artifacts = session.scalars(
                    select(WorkflowArtifact).where(
                        WorkflowArtifact.graph_run_id == graph.graph_run_id,
                        WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
                    )
                ).all()
                corrected_targets = {
                    (artifact.quote_id, str(artifact.payload.get("field_name") or ""))
                    for artifact in correction_artifacts
                    if artifact.quote_id and artifact.payload.get("field_name")
                }
                required_targets: set[tuple[str, str]] = set()
                active_quotes = session.scalars(
                    select(Quote).where(
                        Quote.task_id == task_id,
                        Quote.active.is_(True),
                    )
                ).all()
                for active_quote in active_quotes:
                    review_artifacts = session.scalars(
                        select(WorkflowArtifact)
                        .where(
                            WorkflowArtifact.task_id == task_id,
                            WorkflowArtifact.quote_id == active_quote.quote_id,
                            WorkflowArtifact.artifact_type == "REVIEW_ENVELOPE",
                        )
                        .order_by(
                            WorkflowArtifact.task_revision.desc(),
                            WorkflowArtifact.created_at.desc(),
                        )
                    ).all()
                    prior_review = next(
                        (
                            artifact
                            for artifact in review_artifacts
                            if artifact.graph_run_id != graph.graph_run_id
                        ),
                        None,
                    )
                    if prior_review is None:
                        continue
                    batch_query = select(WorkflowArtifact).where(
                        WorkflowArtifact.task_id == task_id,
                        WorkflowArtifact.quote_id == active_quote.quote_id,
                        WorkflowArtifact.artifact_type == "EXTRACTION_BATCH",
                    )
                    current_batch_artifact = session.scalar(
                        batch_query.where(
                            WorkflowArtifact.graph_run_id == graph.graph_run_id
                        ).order_by(
                            WorkflowArtifact.task_revision.desc(),
                            WorkflowArtifact.created_at.desc(),
                        )
                    )
                    if current_batch_artifact is None:
                        current_batch_artifact = session.scalar(
                            batch_query.order_by(
                                WorkflowArtifact.task_revision.desc(),
                                WorkflowArtifact.created_at.desc(),
                            )
                        )
                    candidate_by_name = {}
                    if current_batch_artifact is not None:
                        current_batch = ExtractionBatch.model_validate(
                            current_batch_artifact.payload
                        )
                        candidate_by_name = {
                            candidate.field_name: candidate
                            for candidate in current_batch.candidates
                        }
                    for finding in prior_review.payload.get("review", {}).get(
                        "findings", []
                    ):
                        if (
                            finding.get("resolved")
                            or finding.get("severity") != "BLOCKING"
                        ):
                            continue
                        field_name = str(finding.get("field_name") or "")
                        candidate = candidate_by_name.get(field_name)
                        if (
                            candidate is not None
                            and candidate.validation_status.value == "VERIFIED"
                            and candidate.origin is not None
                            and candidate.origin.value
                            in {"USER_INPUT", "USER_CORRECTION"}
                        ):
                            continue
                        if field_name:
                            required_targets.add(
                                (active_quote.quote_id, field_name)
                            )
                correction_batch_incomplete = bool(
                    required_targets - corrected_targets
                )
            documents = session.execute(
                select(Document, Quote)
                .join(Quote, Quote.quote_id == Document.quote_id)
                .where(
                    Document.task_id == task_id,
                    Quote.active.is_(True),
                    Document.quote_version == Quote.current_version,
                )
                .order_by(Quote.created_at, Quote.quote_id)
            ).all()
            return {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "task_revision": task.current_revision,
                "status": task.status,
                "current_graph_run_id": task.current_graph_run_id,
                "current_snapshot_id": task.current_snapshot_id,
                "current_result_id": task.current_result_id,
                "current_issue": self._issue_response(issue) if issue is not None else None,
                "current_job": (
                    {
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "job_status": job.status,
                        "task_revision": job.task_revision,
                        "error_code": job.error_code,
                        "error_message": job.error_message,
                        "has_corrections": graph_has_corrections,
                        "correction_batch_incomplete": correction_batch_incomplete,
                    }
                    if job is not None
                    else None
                ),
                "quotes": [
                    {
                        "quote_id": quote.quote_id,
                        "quote_version": quote.current_version,
                        "supplier_id": quote.supplier_id,
                        "document_id": document.document_id,
                        "document_version": document.document_version,
                        "original_filename": document.original_filename,
                    }
                    for document, quote in documents
                ],
                "requirement": dict(requirement.payload) if requirement else None,
            }

    def list_quotes(self, task_id: str) -> dict[str, Any]:
        """Return every logical quote and immutable uploaded document version."""

        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            quotes = session.scalars(
                select(Quote)
                .where(Quote.task_id == task_id)
                .order_by(Quote.created_at.desc(), Quote.quote_id.desc())
            ).all()
            documents = session.scalars(
                select(Document)
                .where(Document.task_id == task_id)
                .order_by(
                    Document.quote_id,
                    Document.quote_version.desc(),
                    Document.document_version.desc(),
                )
            ).all()
            documents_by_quote: dict[str, list[Document]] = {}
            for document in documents:
                documents_by_quote.setdefault(document.quote_id, []).append(document)
            return {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "items": [
                    {
                        "quote_id": quote.quote_id,
                        "supplier_id": quote.supplier_id,
                        "current_version": quote.current_version,
                        "active": quote.active,
                        "created_at": quote.created_at.isoformat(),
                        "versions": [
                            {
                                "quote_version": document.quote_version,
                                "document_id": document.document_id,
                                "document_version": document.document_version,
                                "original_filename": document.original_filename,
                                "media_type": document.media_type,
                                "size_bytes": document.size_bytes,
                                "document_sha256": document.sha256,
                                "is_synthetic": document.is_synthetic,
                                "is_current": (
                                    quote.active
                                    and document.quote_version == quote.current_version
                                ),
                                "created_at": document.created_at.isoformat(),
                            }
                            for document in documents_by_quote.get(quote.quote_id, [])
                        ],
                    }
                    for quote in quotes
                ],
            }

    def list_tasks(self, *, limit: int = 20) -> dict[str, Any]:
        with self.session_factory() as session:
            tasks = session.scalars(
                select(Task)
                .where(Task.owner_id == self.actor_id)
                .order_by(Task.updated_at.desc(), Task.task_id.desc())
                .limit(limit)
            ).all()
            items = []
            for task in tasks:
                requirement = session.scalar(
                    select(RequirementRecord)
                    .where(RequirementRecord.task_id == task.task_id)
                    .order_by(RequirementRecord.requirement_version.desc())
                )
                payload = dict(requirement.payload) if requirement else {}
                items.append(
                    {
                        "task_id": task.task_id,
                        "scenario_id": task.scenario_id,
                        "task_revision": task.current_revision,
                        "status": task.status,
                        "current_result_id": task.current_result_id,
                        "manufacturer": payload.get("manufacturer"),
                        "manufacturer_part_number": payload.get(
                            "manufacturer_part_number"
                        ),
                        "created_at": task.created_at.isoformat(),
                        "updated_at": task.updated_at.isoformat(),
                    }
                )
            return {"items": items}

    def list_issues(self, task_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            issues = session.scalars(
                select(Issue)
                .where(Issue.task_id == task_id)
                .order_by(Issue.created_at, Issue.issue_id)
            ).all()
            return [
                {
                    **self._issue_response(issue),
                    "answer": issue.answer_payload,
                    "resolved_revision": issue.resolved_revision,
                    "answered_by": issue.answered_by,
                    "answered_at": (
                        issue.answered_at.isoformat() if issue.answered_at else None
                    ),
                }
                for issue in issues
            ]

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            issue = session.get(Issue, issue_id)
            if issue is None:
                raise NotFoundError("issue_not_found", "Issue was not found.")
            response = self._issue_response(issue)
            response.update(
                answer=issue.answer_payload,
                resolved_revision=issue.resolved_revision,
                answered_by=issue.answered_by,
                answered_at=(
                    self._aware_datetime(issue.answered_at).isoformat()
                    if issue.answered_at
                    else None
                ),
            )
            return response

    def current_issue(self, graph_run_id: str) -> dict[str, Any] | None:
        with self.session_factory() as session:
            graph = session.get(GraphRun, graph_run_id)
            if graph is None or graph.current_interrupt_issue_id is None:
                return None
            issue = session.get(Issue, graph.current_interrupt_issue_id)
            return self._issue_response(issue) if issue is not None else None

    def workflow_context(self, graph_run_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            graph = session.get(GraphRun, graph_run_id)
            if graph is None:
                raise NotFoundError("graph_run_not_found", "Graph run was not found.")
            task = session.get(Task, graph.task_id)
            if task is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            requirement = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task.task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            documents = session.execute(
                select(Document, Quote)
                .join(Quote, Quote.quote_id == Document.quote_id)
                .where(Document.task_id == task.task_id, Quote.active.is_(True))
                .order_by(Quote.quote_id)
            ).all()
            return {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "task_revision": task.current_revision,
                "started_revision": graph.started_revision,
                "effective_revision": graph.effective_revision,
                "graph_run_id": graph.graph_run_id,
                "thread_id": graph.thread_id,
                "requirement": dict(requirement.payload) if requirement else None,
                "documents": [
                    {
                        "document_id": document.document_id,
                        "document_version": document.document_version,
                        "quote_id": document.quote_id,
                        "quote_version": document.quote_version,
                        "supplier_id": quote.supplier_id,
                        "media_type": document.media_type,
                        "storage_path": document.storage_path,
                        "document_sha256": document.sha256,
                        "is_synthetic": document.is_synthetic,
                    }
                    for document, quote in documents
                ],
            }

    def artifact_payload(self, artifact_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            artifact = session.get(WorkflowArtifact, artifact_id)
            if artifact is None:
                raise NotFoundError("artifact_not_found", "Workflow artifact was not found.")
            return dict(artifact.payload)

    def link_document_artifacts(
        self,
        document_execution_id: str,
        *,
        parsed_artifact_id: str | None = None,
        batch_artifact_id: str | None = None,
        review_artifact_id: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            execution = session.get(DocumentExecution, document_execution_id)
            if execution is None:
                raise NotFoundError(
                    "document_execution_not_found", "Document execution was not found."
                )
            if parsed_artifact_id is not None:
                execution.parsed_artifact_id = parsed_artifact_id
            if batch_artifact_id is not None:
                execution.batch_artifact_id = batch_artifact_id
            if review_artifact_id is not None:
                execution.review_artifact_id = review_artifact_id
            if status is not None:
                execution.status = status
            return self._document_execution_response(execution)

    def claim_job(self, job_id: str) -> dict[str, Any]:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(
                select(Job).where(Job.job_id == job_id).with_for_update()
            )
            if job is None:
                raise NotFoundError("job_not_found", "Job was not found.")
            task = session.scalar(
                select(Task).where(Task.task_id == job.task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == job.graph_run_id)
                .with_for_update()
            )
            if task is None or graph is None:
                raise NotFoundError("job_context_missing", "Job context was not found.")
            if job.status != "PENDING":
                raise ConflictError("job_not_pending", "Job is not pending.")
            if task.current_graph_run_id != graph.graph_run_id:
                job.status = "SUPERSEDED"
                graph.status = "SUPERSEDED"
                raise ConflictError("graph_run_superseded", "Graph run was superseded.")
            if job.attempts >= 3:
                raise BackendError("job_attempt_budget_exceeded", "Job attempt budget was exceeded.")
            job.status = "RUNNING"
            job.attempts += 1
            job.started_at = utc_now()
            graph.status = "RUNNING"
            task.status = "RUNNING"
            return self._job_response(job)

    def finish_job(self, job_id: str, *, waiting_input: bool) -> dict[str, Any]:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(
                select(Job).where(Job.job_id == job_id).with_for_update()
            )
            if job is None:
                raise NotFoundError("job_not_found", "Job was not found.")
            task = session.scalar(
                select(Task).where(Task.task_id == job.task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == job.graph_run_id)
                .with_for_update()
            )
            if graph is None or task is None:
                raise NotFoundError("job_context_missing", "Job context was not found.")
            job.status = "WAITING_INPUT" if waiting_input else "SUCCEEDED"
            job.finished_at = utc_now()
            is_current = (
                task.current_graph_run_id == graph.graph_run_id
                and task.current_revision == graph.effective_revision
            )
            if not is_current:
                graph.status = "SUPERSEDED"
                job.status = "SUPERSEDED"
            elif waiting_input:
                graph.status = "INTERRUPTED"
                task.status = "NEEDS_INPUT"
            else:
                graph.status = "SUCCEEDED"
                task.status = "COMPLETED"
            return self._job_response(job)

    def fail_job(self, job_id: str, *, code: str, message: str) -> None:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(
                select(Job).where(Job.job_id == job_id).with_for_update()
            )
            if job is None:
                return
            task = session.scalar(
                select(Task).where(Task.task_id == job.task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == job.graph_run_id)
                .with_for_update()
            )
            job.finished_at = utc_now()
            if (
                graph is not None
                and task is not None
                and task.current_graph_run_id == graph.graph_run_id
            ):
                job.status = "FAILED"
                job.error_code = code
                job.error_message = message[:1000]
                graph.status = "FAILED"
                task.status = "FAILED"
            else:
                job.status = "SUPERSEDED"
                if graph is not None:
                    graph.status = "SUPERSEDED"

    def publish_result(
        self,
        *,
        task_id: str,
        graph_run_id: str,
        task_revision: int,
        snapshot_id: str,
        result_id: str,
    ) -> None:
        with self.session_factory.begin() as session:
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == graph_run_id)
                .with_for_update()
            )
            if task is None or graph is None:
                raise NotFoundError("publish_context_missing", "Publish context was not found.")
            if task.current_graph_run_id != graph_run_id or task.current_revision != task_revision:
                raise ConflictError(
                    "stale_result_publish",
                    "A stale graph run cannot replace the current result.",
                    current_revision=task.current_revision,
                    result_revision=task_revision,
                )
            task.current_snapshot_id = snapshot_id
            task.current_result_id = result_id

    def list_results(self, task_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            artifacts = session.scalars(
                select(WorkflowArtifact)
                .where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.artifact_type == "COMPARISON_RESULT",
                )
                .order_by(
                    WorkflowArtifact.task_revision.desc(),
                    WorkflowArtifact.created_at.desc(),
                )
            ).all()
            return [
                {
                    "result_id": artifact.artifact_id,
                    "task_revision": artifact.task_revision,
                    "graph_run_id": artifact.graph_run_id,
                    "is_current": artifact.artifact_id == task.current_result_id,
                    "result": dict(artifact.payload),
                }
                for artifact in artifacts
            ]

    def get_result(self, task_id: str, result_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            artifact = session.get(WorkflowArtifact, result_id)
            if (
                task is None
                or task.owner_id != self.actor_id
                or artifact is None
                or artifact.task_id != task_id
                or artifact.artifact_type != "COMPARISON_RESULT"
            ):
                raise NotFoundError("result_not_found", "Result was not found.")
            return {
                "result_id": artifact.artifact_id,
                "task_revision": artifact.task_revision,
                "graph_run_id": artifact.graph_run_id,
                "is_current": artifact.artifact_id == task.current_result_id,
                "result": dict(artifact.payload),
            }

    def list_quote_fields(self, task_id: str, quote_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            quote = session.get(Quote, quote_id)
            if (
                task is None
                or task.owner_id != self.actor_id
                or quote is None
                or quote.task_id != task_id
            ):
                raise NotFoundError("quote_not_found", "Quote was not found.")
            batch_artifact = session.scalar(
                select(WorkflowArtifact)
                .where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.quote_id == quote_id,
                    WorkflowArtifact.artifact_type == "EXTRACTION_BATCH",
                )
                .order_by(
                    WorkflowArtifact.task_revision.desc(),
                    WorkflowArtifact.created_at.desc(),
                )
            )
            if batch_artifact is None:
                return {
                    "quote_id": quote_id,
                    "quote_version": quote.current_version,
                    "review_status": None,
                    "review_findings": [],
                    "fields": [],
                }
            batch = dict(batch_artifact.payload)
            sources = {
                source["source_id"]: source
                for source in batch.get("parsed_input", {}).get("sources", [])
            }
            fields = []
            for candidate in batch.get("candidates", []):
                evidence = []
                for citation in candidate.get("source_refs", []):
                    source = sources.get(citation.get("source_id"), {})
                    evidence.append(
                        {
                            "source_id": citation.get("source_id"),
                            "quoted_text": citation.get("quoted_text"),
                            "kind": source.get("kind"),
                            "page_number": source.get("page_number"),
                            "row_number": source.get("row_number"),
                            "column_name": source.get("column_name"),
                            "bbox": source.get("bbox"),
                            "coordinate_space": source.get("coordinate_space"),
                        }
                    )
                fields.append(
                    {
                        key: candidate.get(key)
                        for key in (
                            "field_name",
                            "field_version",
                            "raw_value",
                            "normalized_value",
                            "unit",
                            "validation_status",
                            "origin",
                        )
                    }
                    | {"evidence": evidence}
                )
            review_query = select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task_id,
                WorkflowArtifact.quote_id == quote_id,
                WorkflowArtifact.artifact_type == "REVIEW_ENVELOPE",
            )
            review_artifact = session.scalar(
                review_query.where(
                    WorkflowArtifact.graph_run_id
                    == (task.current_graph_run_id or batch_artifact.graph_run_id)
                ).order_by(
                    WorkflowArtifact.task_revision.desc(),
                    WorkflowArtifact.created_at.desc(),
                )
            )
            if review_artifact is None:
                review_artifact = session.scalar(
                    review_query.order_by(
                        WorkflowArtifact.task_revision.desc(),
                        WorkflowArtifact.created_at.desc(),
                    )
                )
            review_status = (
                review_artifact.payload.get("review_status")
                if review_artifact is not None
                else None
            )
            review_findings = (
                review_artifact.payload.get("review", {}).get("findings", [])
                if review_artifact is not None
                else []
            )
            review_is_current = bool(
                review_artifact is not None
                and review_artifact.graph_run_id == task.current_graph_run_id
            )
            if not review_is_current:
                corrected_fields = {
                    field["field_name"]
                    for field in fields
                    if field.get("validation_status") == "VERIFIED"
                    and field.get("origin") in {"USER_INPUT", "USER_CORRECTION"}
                }
                review_findings = [
                    finding
                    for finding in review_findings
                    if finding.get("field_name") not in corrected_fields
                    or finding.get("resolved")
                ]
            return {
                "quote_id": quote_id,
                "quote_version": quote.current_version,
                "review_status": review_status,
                "batch_artifact_id": batch_artifact.artifact_id,
                "review_findings": review_findings,
                "fields": fields,
            }

    def correction_event_payloads_for_batch(
        self, batch_artifact_id: str
    ) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            batch_artifact = session.get(WorkflowArtifact, batch_artifact_id)
            if batch_artifact is None or batch_artifact.artifact_type != "EXTRACTION_BATCH":
                raise NotFoundError(
                    "extraction_batch_artifact_not_found",
                    "Extraction batch artifact was not found.",
                )
            artifacts = session.scalars(
                select(WorkflowArtifact)
                .where(
                    WorkflowArtifact.graph_run_id == batch_artifact.graph_run_id,
                    WorkflowArtifact.task_revision == batch_artifact.task_revision,
                    WorkflowArtifact.quote_id == batch_artifact.quote_id,
                    WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
                )
                .order_by(WorkflowArtifact.created_at, WorkflowArtifact.artifact_id)
            ).all()
            return [dict(artifact.payload) for artifact in artifacts]

    def correct_field(
        self,
        *,
        task_id: str,
        quote_id: str,
        field_name: str,
        expected_task_revision: int,
        raw_value: str,
        normalized_value: str | int | bool,
        unit: str | None,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        return self.correct_fields(
            task_id=task_id,
            expected_task_revision=expected_task_revision,
            corrections=[
                {
                    'quote_id': quote_id,
                    'field_name': field_name,
                    'raw_value': raw_value,
                    'normalized_value': normalized_value,
                    'unit': unit,
                    'reason': reason,
                }
            ],
            idempotency_key=idempotency_key,
        )

    def correct_fields(
        self,
        *,
        task_id: str,
        expected_task_revision: int,
        corrections: list[dict[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        from .models import utc_now

        if not corrections:
            raise BackendError(
                'field_corrections_empty',
                'At least one field correction is required.',
            )
        targets: set[tuple[str, str]] = set()
        for item in corrections:
            target = (str(item['quote_id']), str(item['field_name']))
            if target in targets:
                raise BackendError(
                    'field_correction_duplicate',
                    'Each quote field can be corrected only once per submission.',
                    quote_id=target[0],
                    field_name=target[1],
                )
            targets.add(target)
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "corrections": corrections,
        }
        request_sha = content_hash(request)
        operation = f"correct_fields:{task_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            self._require_revision(task, expected_task_revision)
            latest_batches = {}
            active_quotes = session.scalars(
                select(Quote).where(Quote.task_id == task_id, Quote.active.is_(True))
            ).all()
            quote_by_id = {item.quote_id: item for item in active_quotes}
            for quote_id, _field_name in targets:
                if quote_id not in quote_by_id:
                    raise NotFoundError("quote_not_found", "Quote was not found.")
            for active_quote in active_quotes:
                artifact = session.scalar(
                    select(WorkflowArtifact)
                    .where(
                        WorkflowArtifact.task_id == task_id,
                        WorkflowArtifact.quote_id == active_quote.quote_id,
                        WorkflowArtifact.artifact_type == "EXTRACTION_BATCH",
                    )
                    .order_by(
                        WorkflowArtifact.task_revision.desc(),
                        WorkflowArtifact.created_at.desc(),
                    )
                )
                if artifact is None:
                    raise ConflictError(
                        "extraction_batch_missing",
                        "Every active quote must have an extraction batch before correction.",
                        quote_id=active_quote.quote_id,
                    )
                latest_batches[active_quote.quote_id] = artifact

            required_targets: set[tuple[str, str]] = set()
            deferred_shipping_targets: set[tuple[str, str]] = set()
            for active_quote in active_quotes:
                review_query = select(WorkflowArtifact).where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.quote_id == active_quote.quote_id,
                    WorkflowArtifact.artifact_type == "REVIEW_ENVELOPE",
                )
                review_artifact = None
                if task.current_graph_run_id is not None:
                    review_artifact = session.scalar(
                        review_query.where(
                            WorkflowArtifact.graph_run_id == task.current_graph_run_id
                        ).order_by(
                            WorkflowArtifact.task_revision.desc(),
                            WorkflowArtifact.created_at.desc(),
                        )
                    )
                if review_artifact is None:
                    review_artifact = session.scalar(
                        review_query.order_by(
                            WorkflowArtifact.task_revision.desc(),
                            WorkflowArtifact.created_at.desc(),
                        )
                    )
                if review_artifact is None:
                    continue
                current_batch = ExtractionBatch.model_validate(
                    latest_batches[active_quote.quote_id].payload
                )
                candidate_by_name = {
                    candidate.field_name: candidate
                    for candidate in current_batch.candidates
                }
                findings = review_artifact.payload.get("review", {}).get("findings", [])
                for finding in findings:
                    if finding.get("resolved") or finding.get("severity") != "BLOCKING":
                        continue
                    field_name = str(finding.get("field_name") or "")
                    candidate = candidate_by_name.get(field_name)
                    if (
                        candidate is not None
                        and candidate.validation_status.value == "VERIFIED"
                        and candidate.origin is not None
                        and candidate.origin.value in {"USER_INPUT", "USER_CORRECTION"}
                    ):
                        continue
                    target = (active_quote.quote_id, field_name)
                    required_targets.add(target)
                    if (
                        field_name == 'shipping_fee_status'
                        and candidate is not None
                        and (
                            candidate.normalized_value in {None, 'UNKNOWN'}
                            or candidate.validation_status.value == 'MISSING'
                        )
                    ):
                        deferred_shipping_targets.add(target)
            if len(deferred_shipping_targets) == 1:
                required_targets -= deferred_shipping_targets
            missing_targets = sorted(required_targets - targets)
            if missing_targets:
                raise ConflictError(
                    "blocking_corrections_incomplete",
                    "All unresolved blocking fields must be corrected together.",
                    blocking_fields=[
                        {"quote_id": quote_id, "field_name": field_name}
                        for quote_id, field_name in missing_targets
                    ],
                )

            reviewed_at = utc_now()
            corrected_batches = {
                quote_id: ExtractionBatch.model_validate(artifact.payload)
                for quote_id, artifact in latest_batches.items()
            }
            correction_events: dict[str, list[CorrectionEvent]] = {}
            for item in corrections:
                quote_id = str(item["quote_id"])
                field_name = str(item["field_name"])
                try:
                    corrected_batch, correction = apply_candidate_correction(
                        corrected_batches[quote_id],
                        field_name=field_name,
                        action=CorrectionAction.USER_CORRECTION,
                        raw_value=str(item["raw_value"]),
                        normalized_value=item["normalized_value"],
                        unit=item.get("unit"),
                        reason_code="AUTHORIZED_FIELD_CORRECTION",
                        reason=str(item["reason"]),
                        reviewer_id=self.actor_id,
                        reviewed_at=reviewed_at,
                    )
                except (ValueError, TypeError) as exc:
                    raise BackendError(
                        "field_correction_invalid",
                        "Field correction is invalid.",
                        quote_id=quote_id,
                        field_name=field_name,
                    ) from exc
                corrected_batches[quote_id] = corrected_batch
                correction_events.setdefault(quote_id, []).append(correction)

            all_correction_events: dict[str, list[CorrectionEvent]] = {}
            for quote_id, events in correction_events.items():
                source_artifact = latest_batches[quote_id]
                prior_events = [
                    CorrectionEvent.model_validate(artifact.payload)
                    for artifact in session.scalars(
                        select(WorkflowArtifact)
                        .where(
                            WorkflowArtifact.graph_run_id
                            == source_artifact.graph_run_id,
                            WorkflowArtifact.task_revision
                            == source_artifact.task_revision,
                            WorkflowArtifact.quote_id == quote_id,
                            WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
                        )
                        .order_by(
                            WorkflowArtifact.created_at,
                            WorkflowArtifact.artifact_id,
                        )
                    ).all()
                ]
                all_correction_events[quote_id] = prior_events + events

            requirement_record = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            if requirement_record is None:
                raise ConflictError(
                    "requirement_missing",
                    "The task has no procurement requirement.",
                )
            requirement = ProcurementRequirement.model_validate(
                requirement_record.payload
            )
            preflight_blockers: dict[str, list[dict[str, Any]]] = {}
            for quote_id, events in all_correction_events.items():
                corrected_batch = corrected_batches[quote_id]
                document = session.get(
                    Document,
                    corrected_batch.parsed_input.context.document_id,
                )
                if document is None:
                    raise ConflictError(
                        "correction_document_missing",
                        "The corrected extraction batch has no source document.",
                        quote_id=quote_id,
                    )
                envelope = review_extraction_batch(
                    corrected_batch,
                    self.quote_dictionary,
                    CriticalityContext(
                        required_revision=requirement.revision,
                        base_unit=requirement.base_unit,
                    ),
                    input_is_synthetic=document.is_synthetic,
                    reviewed_at=reviewed_at,
                    corrections=tuple(events),
                )
                blockers = [
                    {
                        "field_name": finding.field_name,
                        "codes": list(finding.codes),
                        "message": finding.message,
                    }
                    for finding in (envelope.review.findings if envelope.review else ())
                    if finding.severity == ReviewSeverity.BLOCKING
                    and not finding.resolved
                    and (quote_id, finding.field_name) not in deferred_shipping_targets
                ]
                if blockers:
                    preflight_blockers[quote_id] = blockers
            if preflight_blockers:
                raise ConflictError(
                    "corrections_still_require_review",
                    "The proposed corrections still contain blocking review findings.",
                    blocking_findings=preflight_blockers,
                )

            next_revision = task.current_revision + 1
            old_graph = (
                session.get(GraphRun, task.current_graph_run_id)
                if task.current_graph_run_id
                else None
            )
            self._supersede_current_graph(session, task)
            graph_run_id = new_id("graph")
            job_id = new_id("job")
            graph = GraphRun(
                graph_run_id=graph_run_id,
                task_id=task_id,
                thread_id=graph_run_id,
                started_revision=next_revision,
                effective_revision=next_revision,
                status="PENDING",
                provider=old_graph.provider if old_graph is not None else None,
                model_id=old_graph.model_id if old_graph is not None else None,
                environment=old_graph.environment if old_graph is not None else None,
                prompt_version=(
                    old_graph.prompt_version if old_graph is not None else None
                ),
            )
            session.add(graph)
            for quote_id, events in correction_events.items():
                source_artifact = latest_batches[quote_id]
                parent_artifact_id = source_artifact.artifact_id
                correction_payloads = [
                    correction.model_dump(mode="json")
                    for correction in all_correction_events[quote_id]
                ]
                for correction_payload in correction_payloads:
                    correction_artifact = WorkflowArtifact(
                        artifact_id=new_id("artifact"),
                        task_id=task_id,
                        task_revision=next_revision,
                        artifact_type="CORRECTION_EVENT",
                        quote_id=quote_id,
                        graph_run_id=graph_run_id,
                        parent_artifact_id=parent_artifact_id,
                        payload=correction_payload,
                        content_sha256=content_hash(correction_payload),
                    )
                    session.add(correction_artifact)
                    parent_artifact_id = correction_artifact.artifact_id
                corrected_batch = corrected_batches[quote_id]
                corrected_payload = corrected_batch.model_dump(mode="json")
                corrected_artifact = WorkflowArtifact(
                    artifact_id=new_id("artifact"),
                    task_id=task_id,
                    task_revision=next_revision,
                    artifact_type="EXTRACTION_BATCH",
                    schema_version=corrected_batch.schema_version,
                    quote_id=quote_id,
                    graph_run_id=graph_run_id,
                    parent_artifact_id=parent_artifact_id,
                    payload=corrected_payload,
                    content_sha256=content_hash(corrected_payload),
                )
                session.add(corrected_artifact)
                latest_batches[quote_id] = corrected_artifact
            documents = session.scalars(
                select(Document).where(Document.task_id == task_id)
            ).all()
            for document in documents:
                selected_batch = latest_batches[document.quote_id]
                session.add(
                    DocumentExecution(
                        document_execution_id=new_id("docexec"),
                        graph_run_id=graph_run_id,
                        document_id=document.document_id,
                        status="EXTRACTED",
                        calls_used=0,
                        max_calls=8,
                        batch_artifact_id=selected_batch.artifact_id,
                    )
                )
            task.current_revision = next_revision
            task.current_graph_run_id = graph_run_id
            task.current_snapshot_id = None
            task.current_result_id = None
            task.status = "QUEUED"
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=next_revision,
                    change_type=(
                        f"FIELD_CORRECTED:{corrections[0]['field_name']}"
                        if len(corrections) == 1
                        else f"FIELDS_CORRECTED:{len(corrections)}"
                    ),
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                )
            )
            session.add(
                Job(
                    job_id=job_id,
                    task_id=task_id,
                    graph_run_id=graph_run_id,
                    job_type="START",
                    status="PENDING",
                    task_revision=next_revision,
                )
            )
            response = {
                "task_id": task_id,
                "task_revision": next_revision,
                "graph_run_id": graph_run_id,
                "job_id": job_id,
                "job_type": "START",
                "job_status": "PENDING",
                "correction_count": len(corrections),
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    def upload_quote(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        supplier_id: str,
        original_filename: str,
        media_type: str,
        content: bytes,
        idempotency_key: str,
        is_synthetic: bool = False,
    ) -> dict[str, Any]:
        return self.upload_quote_stream(
            task_id,
            expected_task_revision=expected_task_revision,
            supplier_id=supplier_id,
            original_filename=original_filename,
            media_type=media_type,
            stream=BytesIO(content),
            idempotency_key=idempotency_key,
            is_synthetic=is_synthetic,
        )

    def upload_quote_stream(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        supplier_id: str,
        original_filename: str,
        media_type: str,
        stream: BinaryIO,
        idempotency_key: str,
        is_synthetic: bool = False,
        max_bytes: int = 5 * 1024 * 1024,
        chunk_size: int = 64 * 1024,
    ) -> dict[str, Any]:
        if media_type not in {"application/pdf", "text/csv"}:
            raise BackendError(
                "unsupported_media_type",
                "Only application/pdf and text/csv quote files are supported.",
            )
        if max_bytes < 1 or chunk_size < 1:
            raise ValueError("upload limits must be positive")

        staging_directory = self.storage_root / ".staging"
        staging_directory.mkdir(parents=True, exist_ok=True)
        staged_path = staging_directory / f"{new_id('upload')}.tmp"
        digest = hashlib.sha256()
        size_bytes = 0
        try:
            with staged_path.open("xb") as handle:
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise BackendError(
                            "upload_stream_invalid",
                            "Quote upload stream must produce bytes.",
                        )
                    size_bytes += len(chunk)
                    if size_bytes > max_bytes:
                        raise BackendError(
                            "file_too_large",
                            "Quote file exceeds the 5 MiB limit.",
                            max_file_size_bytes=max_bytes,
                        )
                    digest.update(chunk)
                    handle.write(chunk)
        except Exception:
            if staged_path.exists():
                staged_path.unlink()
            raise

        file_sha = digest.hexdigest()
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "supplier_id": supplier_id,
            "original_filename": original_filename,
            "media_type": media_type,
            "content_sha256": file_sha,
            "is_synthetic": is_synthetic,
        }
        request_sha = content_hash(request)
        operation = f"upload_quote:{task_id}"
        final_path: Path | None = None
        try:
            with self.session_factory.begin() as session:
                repeated = self._existing_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                )
                if repeated is not None:
                    return repeated
                task = session.scalar(
                    select(Task).where(Task.task_id == task_id).with_for_update()
                )
                if task is None or task.owner_id != self.actor_id:
                    raise NotFoundError("task_not_found", "Task was not found.")
                repeated = self._existing_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                )
                if repeated is not None:
                    return repeated
                if task.current_revision != expected_task_revision:
                    raise ConflictError(
                        "task_revision_conflict",
                        "Task revision has changed.",
                        expected=expected_task_revision,
                        actual=task.current_revision,
                    )
                self._supersede_current_graph(session, task)
                quote_id = new_id("quote")
                document_id = new_id("doc")
                extension = ".pdf" if media_type == "application/pdf" else ".csv"
                final_path = (
                    self.storage_root
                    / task_id
                    / quote_id
                    / "v1"
                    / document_id
                    / f"source{extension}"
                )
                final_path.parent.mkdir(parents=True, exist_ok=True)
                if final_path.exists():
                    raise ConflictError(
                        "immutable_storage_conflict",
                        "Generated quote storage location already exists.",
                    )
                staged_path.rename(final_path)
                session.add(
                    Quote(
                        quote_id=quote_id,
                        task_id=task_id,
                        supplier_id=supplier_id,
                        current_version=1,
                    )
                )
                session.flush()
                session.add(
                    Document(
                        document_id=document_id,
                        task_id=task_id,
                        quote_id=quote_id,
                        quote_version=1,
                        document_version=1,
                        original_filename=Path(original_filename).name,
                        media_type=media_type,
                        size_bytes=size_bytes,
                        sha256=file_sha,
                        storage_path=str(final_path),
                        is_synthetic=is_synthetic,
                    )
                )
                task.current_revision += 1
                task.status = "DRAFT"
                session.add(
                    TaskRevision(
                        revision_id=new_id("rev"),
                        task_id=task_id,
                        revision=task.current_revision,
                        change_type="QUOTE_UPLOADED",
                        actor_id=self.actor_id,
                        request_sha256=request_sha,
                    )
                )
                response = {
                    "task_id": task_id,
                    "task_revision": task.current_revision,
                    "quote_id": quote_id,
                    "quote_version": 1,
                    "document_id": document_id,
                    "document_version": 1,
                    "document_sha256": file_sha,
                    "storage_path": str(final_path),
                }
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=201,
                    response=response,
                )
                return response
        except Exception:
            if final_path is not None and final_path.exists():
                final_path.unlink()
            raise
        finally:
            if staged_path.exists():
                staged_path.unlink()

    def append_artifact(
        self,
        *,
        task_id: str,
        task_revision: int,
        artifact_type: str,
        payload: dict[str, Any],
        schema_version: str | None = None,
        parent_artifact_id: str | None = None,
        quote_id: str | None = None,
        document_id: str | None = None,
        graph_run_id: str | None = None,
    ) -> dict[str, Any]:
        artifact_id = new_id("artifact")
        payload_sha = content_hash(payload)
        with self.session_factory.begin() as session:
            if session.get(Task, task_id) is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            session.add(
                WorkflowArtifact(
                    artifact_id=artifact_id,
                    task_id=task_id,
                    task_revision=task_revision,
                    artifact_type=artifact_type,
                    schema_version=schema_version,
                    parent_artifact_id=parent_artifact_id,
                    quote_id=quote_id,
                    document_id=document_id,
                    graph_run_id=graph_run_id,
                    payload=payload,
                    content_sha256=payload_sha,
                )
            )
        return {"artifact_id": artifact_id, "content_sha256": payload_sha}

    def start_run(
        self,
        task_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "provider": provider,
            "model_id": model_id,
            "environment": environment,
            "prompt_version": prompt_version,
        }
        request_sha = content_hash(request)
        operation = f"start_run:{task_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            self._require_revision(task, expected_task_revision)
            if task.current_graph_run_id is not None:
                current = session.get(GraphRun, task.current_graph_run_id)
                if current is not None and current.status in {
                    "PENDING",
                    "RUNNING",
                    "INTERRUPTED",
                }:
                    raise ConflictError(
                        "graph_run_active", "The task already has an active graph run."
                    )
            graph_run_id = new_id("graph")
            job_id = new_id("job")
            session.add(
                GraphRun(
                    graph_run_id=graph_run_id,
                    task_id=task_id,
                    thread_id=graph_run_id,
                    started_revision=task.current_revision,
                    effective_revision=task.current_revision,
                    status="PENDING",
                    provider=provider,
                    model_id=model_id,
                    environment=environment,
                    prompt_version=prompt_version,
                )
            )
            session.add(
                Job(
                    job_id=job_id,
                    task_id=task_id,
                    graph_run_id=graph_run_id,
                    job_type="START",
                    status="PENDING",
                    task_revision=task.current_revision,
                )
            )
            task.current_graph_run_id = graph_run_id
            task.status = "QUEUED"
            response = {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "graph_run_id": graph_run_id,
                "job_id": job_id,
                "job_type": "START",
                "job_status": "PENDING",
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    def retry_failed_resume_job(
        self,
        task_id: str,
        job_id: str,
        *,
        expected_task_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Requeue a failed RESUME job without starting a new graph or model run."""

        request = {
            "task_id": task_id,
            "job_id": job_id,
            "expected_task_revision": expected_task_revision,
        }
        request_sha = content_hash(request)
        operation = f"retry_resume_job:{job_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            job = session.scalar(
                select(Job).where(Job.job_id == job_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            if job is None or job.task_id != task_id:
                raise NotFoundError("job_not_found", "Job was not found.")
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == job.graph_run_id)
                .with_for_update()
            )
            if graph is None or task.current_graph_run_id != graph.graph_run_id:
                raise ConflictError(
                    "graph_run_superseded", "The job belongs to a superseded graph run."
                )
            self._require_revision(task, expected_task_revision)
            if job.job_type != "RESUME":
                raise ConflictError(
                    "job_retry_unsupported",
                    "Only a failed resume job can be retried from its checkpoint.",
                )
            if job.status != "FAILED":
                raise ConflictError("job_not_failed", "Job is not failed.")
            if job.attempts >= 3:
                raise BackendError(
                    "job_attempt_budget_exceeded", "Job attempt budget was exceeded."
                )
            job.status = "PENDING"
            job.error_code = None
            job.error_message = None
            job.started_at = None
            job.finished_at = None
            graph.status = "PENDING"
            task.status = "QUEUED"
            response = {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "graph_run_id": graph.graph_run_id,
                "job_id": job.job_id,
                "job_type": job.job_type,
                "job_status": job.status,
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    def ensure_document_execution(
        self,
        *,
        graph_run_id: str,
        document_id: str,
        max_calls: int = 8,
    ) -> dict[str, Any]:
        if max_calls < 1:
            raise BackendError(
                "model_call_budget_invalid", "Model call budget must be positive."
            )
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(DocumentExecution).where(
                    DocumentExecution.graph_run_id == graph_run_id,
                    DocumentExecution.document_id == document_id,
                )
            )
            if existing is None:
                if session.get(GraphRun, graph_run_id) is None:
                    raise NotFoundError("graph_run_not_found", "Graph run was not found.")
                if session.get(Document, document_id) is None:
                    raise NotFoundError("document_not_found", "Document was not found.")
                existing = DocumentExecution(
                    document_execution_id=new_id("docexec"),
                    graph_run_id=graph_run_id,
                    document_id=document_id,
                    status="PENDING",
                    calls_used=0,
                    max_calls=max_calls,
                )
                session.add(existing)
                session.flush()
            return self._document_execution_response(existing)

    def record_document_calls(
        self,
        document_execution_id: str,
        *,
        expected_calls_used: int,
        calls_after: int,
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            execution = session.get(DocumentExecution, document_execution_id)
            if execution is None:
                raise NotFoundError(
                    "document_execution_not_found", "Document execution was not found."
                )
            if execution.calls_used != expected_calls_used:
                raise ConflictError(
                    "model_call_count_conflict",
                    "Stored model call count has changed.",
                    expected=expected_calls_used,
                    actual=execution.calls_used,
                )
            if calls_after < expected_calls_used or calls_after > execution.max_calls:
                raise BackendError(
                    "model_call_budget_exceeded",
                    "Document model call budget was exceeded.",
                    calls_after=calls_after,
                    max_calls=execution.max_calls,
                )
            execution.calls_used = calls_after
            return self._document_execution_response(execution)

    def open_issue(
        self,
        *,
        task_id: str,
        graph_run_id: str,
        task_revision: int,
        issue_type: str,
        quote_id: str | None,
        field_name: str | None,
        question: str,
        answer_schema: dict[str, Any],
    ) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            graph = session.scalar(
                select(GraphRun)
                .where(GraphRun.graph_run_id == graph_run_id)
                .with_for_update()
            )
            if graph is None or task is None or graph.task_id != task_id:
                raise NotFoundError("graph_run_not_found", "Graph run was not found.")
            if task.current_graph_run_id != graph_run_id:
                raise ConflictError(
                    "graph_run_superseded", "Graph run was superseded."
                )
            existing = session.scalar(
                select(Issue).where(
                    Issue.graph_run_id == graph_run_id,
                    Issue.issue_type == issue_type,
                    Issue.quote_id == quote_id,
                    Issue.field_name == field_name,
                )
            )
            if existing is not None:
                return self._issue_response(existing)
            if graph.effective_revision != task_revision:
                raise ConflictError(
                    "graph_revision_conflict",
                    "Graph run revision has changed.",
                    expected=task_revision,
                    actual=graph.effective_revision,
                )
            issue = Issue(
                issue_id=new_id("issue"),
                task_id=task_id,
                graph_run_id=graph_run_id,
                quote_id=quote_id,
                field_name=field_name,
                issue_type=issue_type,
                status="OPEN",
                question=question,
                answer_schema=answer_schema,
                created_revision=task_revision,
                created_by=self.actor_id,
            )
            session.add(issue)
            graph.status = "INTERRUPTED"
            graph.current_interrupt_issue_id = issue.issue_id
            task.status = "NEEDS_INPUT"
            session.flush()
            return self._issue_response(issue)

    def answer_issue(
        self,
        task_id: str,
        issue_id: str,
        *,
        expected_task_revision: int,
        answer: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "issue_id": issue_id,
            "expected_task_revision": expected_task_revision,
            "answer": answer,
        }
        request_sha = content_hash(request)
        operation = f"answer_issue:{issue_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            task = session.scalar(
                select(Task).where(Task.task_id == task_id).with_for_update()
            )
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            repeated = self._existing_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
            )
            if repeated is not None:
                return repeated
            self._require_revision(task, expected_task_revision)
            issue = session.get(Issue, issue_id)
            if issue is None or issue.task_id != task_id:
                raise NotFoundError("issue_not_found", "Issue was not found.")
            if issue.status != "OPEN":
                raise ConflictError("issue_not_open", "Issue is no longer open.")
            expected_answer_type = issue.answer_schema.get("answer_type")
            if answer.get("answer_type") != expected_answer_type:
                raise BackendError(
                    "answer_type_invalid",
                    "Answer type does not match the issue schema.",
                    expected=expected_answer_type,
                )
            expected_currency = issue.answer_schema.get("currency")
            if (
                expected_answer_type == "SHIPPING_AMOUNT"
                and expected_currency is not None
                and answer.get("currency") != expected_currency
            ):
                raise BackendError(
                    "answer_currency_invalid",
                    "Answer currency does not match the procurement requirement.",
                    expected=expected_currency,
                    actual=answer.get("currency"),
                )
            graph = session.get(GraphRun, issue.graph_run_id)
            if graph is None or task.current_graph_run_id != graph.graph_run_id:
                raise ConflictError(
                    "graph_run_superseded", "The issue belongs to a superseded graph run."
                )
            task.current_revision += 1
            task.status = "QUEUED"
            graph.effective_revision = task.current_revision
            graph.status = "PENDING"
            graph.current_interrupt_issue_id = None
            issue.status = "RESOLVED"
            issue.answer_payload = answer
            issue.resolved_revision = task.current_revision
            issue.answered_by = self.actor_id
            from .models import utc_now

            issue.answered_at = utc_now()
            session.add(
                TaskRevision(
                    revision_id=new_id("rev"),
                    task_id=task_id,
                    revision=task.current_revision,
                    change_type=f"ISSUE_ANSWERED:{issue.issue_type}",
                    actor_id=self.actor_id,
                    request_sha256=request_sha,
                )
            )
            job_id = new_id("job")
            session.add(
                Job(
                    job_id=job_id,
                    task_id=task_id,
                    graph_run_id=graph.graph_run_id,
                    issue_id=issue.issue_id,
                    job_type="RESUME",
                    status="PENDING",
                    task_revision=task.current_revision,
                )
            )
            response = {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "graph_run_id": graph.graph_run_id,
                "job_id": job_id,
                "job_type": "RESUME",
                "job_status": "PENDING",
            }
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=202,
                response=response,
            )
            return response

    @staticmethod
    def _require_revision(task: Task, expected: int) -> None:
        if task.current_revision != expected:
            raise ConflictError(
                "task_revision_conflict",
                "Task revision has changed.",
                expected=expected,
                actual=task.current_revision,
            )

    @staticmethod
    def _issue_response(issue: Issue) -> dict[str, Any]:
        return {
            "issue_id": issue.issue_id,
            "task_id": issue.task_id,
            "graph_run_id": issue.graph_run_id,
            "quote_id": issue.quote_id,
            "field_name": issue.field_name,
            "issue_type": issue.issue_type,
            "status": issue.status,
            "question": issue.question,
            "answer_schema": dict(issue.answer_schema),
            "created_revision": issue.created_revision,
            "created_by": issue.created_by,
        }

    @staticmethod
    def _document_execution_response(execution: DocumentExecution) -> dict[str, Any]:
        return {
            "document_execution_id": execution.document_execution_id,
            "graph_run_id": execution.graph_run_id,
            "document_id": execution.document_id,
            "status": execution.status,
            "calls_used": execution.calls_used,
            "max_calls": execution.max_calls,
            "parsed_artifact_id": execution.parsed_artifact_id,
            "batch_artifact_id": execution.batch_artifact_id,
            "review_artifact_id": execution.review_artifact_id,
        }

    @staticmethod
    def _job_response(job: Job) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "task_id": job.task_id,
            "graph_run_id": job.graph_run_id,
            "issue_id": job.issue_id,
            "job_type": job.job_type,
            "status": job.status,
            "task_revision": job.task_revision,
            "attempts": job.attempts,
        }

    @staticmethod
    def _aware_datetime(value):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=timezone.utc)
        return value
