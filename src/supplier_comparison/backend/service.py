from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
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
    ReviewEnvelope,
    ReviewSeverity,
    ValidationStatus,
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
    QuoteDraft,
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
        self.quote_dictionary_path = Path(quote_dictionary_path)
        self.quote_dictionary = QuoteDictionary.load(self.quote_dictionary_path)

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
        policy_set_version: str | None = None,
        policy_index_version: str | None = None,
        policy_category: str | None = None,
        policy_region: str | None = None,
    ) -> dict[str, Any]:
        policy_binding = (
            policy_set_version,
            policy_index_version,
            policy_category,
            policy_region,
        )
        if any(value is not None for value in policy_binding) and not all(
            isinstance(value, str) and value.strip() for value in policy_binding
        ):
            raise BackendError(
                "policy_binding_incomplete",
                "Policy set, index, category, and region must be supplied together.",
            )
        normalized_policy_binding = tuple(
            value.strip() if isinstance(value, str) else None for value in policy_binding
        )
        (
            policy_set_version,
            policy_index_version,
            policy_category,
            policy_region,
        ) = normalized_policy_binding
        request = {
            "requirement": requirement.model_dump(mode="json"),
            "scenario_id": scenario_id,
            "policy_set_version": policy_set_version,
            "policy_index_version": policy_index_version,
            "policy_category": policy_category,
            "policy_region": policy_region,
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
                policy_set_version=policy_set_version,
                policy_index_version=policy_index_version,
                policy_category=policy_category,
                policy_region=policy_region,
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
                "policy_binding": self._policy_binding_response(task),
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
                "policy_binding": self._policy_binding_response(task),
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
                        "created_at": job.created_at.isoformat(),
                        "started_at": (
                            job.started_at.isoformat()
                            if job.started_at is not None
                            else None
                        ),
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

    def upload_quote_draft_stream(
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
        provider: str | None = None,
        model_id: str | None = None,
        environment: str | None = None,
        prompt_version: str | None = None,
        max_bytes: int = 5 * 1024 * 1024,
        chunk_size: int = 64 * 1024,
    ) -> dict[str, Any]:
        """Persist a review draft without changing authoritative task inputs."""

        if media_type not in {"application/pdf", "text/csv"}:
            raise BackendError(
                "unsupported_media_type",
                "Only application/pdf and text/csv quote files are supported.",
            )
        if max_bytes < 1 or chunk_size < 1:
            raise ValueError("upload limits must be positive")

        staging_directory = self.storage_root / ".staging"
        staging_directory.mkdir(parents=True, exist_ok=True)
        staged_path = staging_directory / f"{new_id('draft_upload')}.tmp"
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
                            "Quote draft upload stream must produce bytes.",
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
            if size_bytes == 0:
                raise BackendError("empty_file", "Quote draft file is empty.")
        except Exception:
            if staged_path.exists():
                staged_path.unlink()
            raise

        file_sha = digest.hexdigest()
        dictionary_sha = hashlib.sha256(self.quote_dictionary_path.read_bytes()).hexdigest()
        request = {
            "task_id": task_id,
            "expected_task_revision": expected_task_revision,
            "supplier_id": supplier_id,
            "original_filename": Path(original_filename).name,
            "media_type": media_type,
            "content_sha256": file_sha,
            "is_synthetic": is_synthetic,
            "provider": provider,
            "model_id": model_id,
            "environment": environment,
            "prompt_version": prompt_version,
        }
        request_sha = content_hash(request)
        operation = f"upload_quote_draft:{task_id}"
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
                self._require_revision(task, expected_task_revision)
                for stale in session.scalars(
                    select(QuoteDraft).where(
                        QuoteDraft.task_id == task_id,
                        QuoteDraft.status.in_(
                            ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
                        ),
                        QuoteDraft.base_task_revision != task.current_revision,
                    )
                ):
                    stale.status = "STALE"
                    stale.revision += 1
                active = session.scalar(
                    select(QuoteDraft).where(
                        QuoteDraft.task_id == task_id,
                        QuoteDraft.status.in_(
                            ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
                        ),
                    )
                )
                if active is not None:
                    raise ConflictError(
                        "active_quote_draft_exists",
                        "Discard or submit the active quote draft before uploading another.",
                        quote_draft_id=active.quote_draft_id,
                    )
                draft_id = new_id("draft")
                quote_id = new_id("quote")
                document_id = new_id("doc")
                job_id = new_id("job")
                extension = ".pdf" if media_type == "application/pdf" else ".csv"
                final_path = (
                    self.storage_root
                    / ".drafts"
                    / task_id
                    / draft_id
                    / f"source{extension}"
                )
                final_path.parent.mkdir(parents=True, exist_ok=True)
                staged_path.rename(final_path)
                draft = QuoteDraft(
                    quote_draft_id=draft_id,
                    task_id=task_id,
                    actor_id=self.actor_id,
                    base_task_revision=task.current_revision,
                    revision=1,
                    status="PROCESSING",
                    proposed_quote_id=quote_id,
                    proposed_document_id=document_id,
                    supplier_id=supplier_id.strip(),
                    original_filename=Path(original_filename).name,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    sha256=file_sha,
                    storage_path=str(final_path),
                    is_synthetic=is_synthetic,
                    provider=provider,
                    model_id=model_id,
                    environment=environment,
                    prompt_version=prompt_version,
                    dictionary_sha256=dictionary_sha,
                )
                session.add(draft)
                session.add(
                    Job(
                        job_id=job_id,
                        task_id=task_id,
                        graph_run_id=None,
                        quote_draft_id=draft_id,
                        job_type="DRAFT_REVIEW",
                        status="PENDING",
                        task_revision=task.current_revision,
                    )
                )
                session.flush()
                response = self._quote_draft_response(session, draft)
                self._save_idempotent(
                    session,
                    operation=operation,
                    key=idempotency_key,
                    request_sha256=request_sha,
                    response_status=202,
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

    def list_quote_drafts(self, task_id: str) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            for stale in session.scalars(
                select(QuoteDraft).where(
                    QuoteDraft.task_id == task_id,
                    QuoteDraft.status.in_(
                        ("UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT")
                    ),
                    QuoteDraft.base_task_revision != task.current_revision,
                )
            ):
                stale.status = "STALE"
                stale.revision += 1
            drafts = session.scalars(
                select(QuoteDraft)
                .where(QuoteDraft.task_id == task_id)
                .order_by(QuoteDraft.created_at.desc(), QuoteDraft.quote_draft_id.desc())
            ).all()
            return {
                "task_id": task_id,
                "task_revision": task.current_revision,
                "items": [self._quote_draft_response(session, draft) for draft in drafts],
            }

    def get_quote_draft(self, task_id: str, draft_id: str) -> dict[str, Any]:
        with self.session_factory.begin() as session:
            draft = self._owned_quote_draft(session, task_id, draft_id)
            task = session.get(Task, task_id)
            if (
                task is not None
                and task.current_revision != draft.base_task_revision
                and draft.status in {"UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT"}
            ):
                draft.status = "STALE"
                draft.revision += 1
            return self._quote_draft_response(session, draft)

    def quote_draft_job_context(self, job_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if job is None or job.job_type != "DRAFT_REVIEW" or not job.quote_draft_id:
                raise NotFoundError("draft_job_not_found", "Quote draft job was not found.")
            draft = session.get(QuoteDraft, job.quote_draft_id)
            task = session.get(Task, job.task_id)
            requirement = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == job.task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            if draft is None or task is None or requirement is None:
                raise NotFoundError("draft_job_context_missing", "Quote draft job context is missing.")
            return {
                "job_id": job.job_id,
                "task_id": task.task_id,
                "task_revision": draft.base_task_revision,
                "scenario_id": task.scenario_id,
                "quote_draft_id": draft.quote_draft_id,
                "quote_id": draft.proposed_quote_id,
                "document_id": draft.proposed_document_id,
                "supplier_id": draft.supplier_id,
                "media_type": draft.media_type,
                "storage_path": draft.storage_path,
                "document_sha256": draft.sha256,
                "is_synthetic": draft.is_synthetic,
                "calls_used": draft.calls_used,
                "max_calls": draft.max_calls,
                "requirement": dict(requirement.payload),
            }

    def claim_quote_draft_job(self, job_id: str) -> dict[str, Any]:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or job.job_type != "DRAFT_REVIEW" or not job.quote_draft_id:
                raise NotFoundError("draft_job_not_found", "Quote draft job was not found.")
            draft = session.scalar(
                select(QuoteDraft)
                .where(QuoteDraft.quote_draft_id == job.quote_draft_id)
                .with_for_update()
            )
            task = session.scalar(select(Task).where(Task.task_id == job.task_id).with_for_update())
            if draft is None or task is None:
                raise NotFoundError("draft_job_context_missing", "Quote draft job context is missing.")
            if job.status != "PENDING":
                raise ConflictError("job_not_pending", "Job is not pending.")
            if task.current_revision != draft.base_task_revision:
                draft.status = "STALE"
                draft.revision += 1
                job.status = "SUPERSEDED"
                raise ConflictError("quote_draft_stale", "Task changed while the quote draft was open.")
            if job.attempts >= 3:
                raise BackendError("job_attempt_budget_exceeded", "Job attempt budget was exceeded.")
            job.status = "RUNNING"
            job.attempts += 1
            job.started_at = utc_now()
            draft.status = "PROCESSING"
            return self._job_response(job)

    def record_quote_draft_calls(self, draft_id: str, calls_after: int) -> None:
        with self.session_factory.begin() as session:
            draft = session.scalar(
                select(QuoteDraft).where(QuoteDraft.quote_draft_id == draft_id).with_for_update()
            )
            if draft is not None:
                draft.calls_used = calls_after

    def complete_quote_draft_job(
        self,
        job_id: str,
        *,
        parsed_artifact_id: str,
        batch_artifact_id: str,
        review_artifact_id: str,
        downstream_ready: bool,
    ) -> dict[str, Any]:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or not job.quote_draft_id:
                raise NotFoundError("draft_job_not_found", "Quote draft job was not found.")
            draft = session.scalar(
                select(QuoteDraft).where(QuoteDraft.quote_draft_id == job.quote_draft_id).with_for_update()
            )
            if draft is None:
                raise NotFoundError("quote_draft_not_found", "Quote draft was not found.")
            draft.parsed_artifact_id = parsed_artifact_id
            draft.batch_artifact_id = batch_artifact_id
            draft.review_artifact_id = review_artifact_id
            draft.status = "READY_TO_SUBMIT" if downstream_ready else "REVIEW_REQUIRED"
            draft.error_code = None
            draft.error_message = None
            job.status = "SUCCEEDED"
            job.finished_at = utc_now()
            return self._quote_draft_response(session, draft)

    def fail_quote_draft_job(self, job_id: str, *, code: str, message: str) -> None:
        from .models import utc_now

        with self.session_factory.begin() as session:
            job = session.scalar(select(Job).where(Job.job_id == job_id).with_for_update())
            if job is None or not job.quote_draft_id:
                return
            draft = session.scalar(
                select(QuoteDraft).where(QuoteDraft.quote_draft_id == job.quote_draft_id).with_for_update()
            )
            job.status = "FAILED"
            job.error_code = code
            job.error_message = message[:1000]
            job.finished_at = utc_now()
            if draft is not None:
                draft.status = "FAILED"
                draft.error_code = code
                draft.error_message = message[:1000]

    def correct_quote_draft(
        self,
        task_id: str,
        draft_id: str,
        *,
        expected_draft_revision: int,
        corrections: list[dict[str, Any]],
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "draft_id": draft_id,
            "expected_draft_revision": expected_draft_revision,
            "corrections": corrections,
        }
        request_sha = content_hash(request)
        operation = f"correct_quote_draft:{draft_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key, request_sha256=request_sha
            )
            if repeated is not None:
                return repeated
            draft = self._owned_quote_draft(session, task_id, draft_id, lock=True)
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            if task.current_revision != draft.base_task_revision:
                draft.status = "STALE"
                draft.revision += 1
                raise ConflictError("quote_draft_stale", "Task changed while the quote draft was open.")
            if draft.revision != expected_draft_revision:
                raise ConflictError(
                    "quote_draft_revision_conflict",
                    "Quote draft revision has changed.",
                    expected=expected_draft_revision,
                    actual=draft.revision,
                )
            if draft.status != "REVIEW_REQUIRED" or not draft.batch_artifact_id or not draft.review_artifact_id:
                raise ConflictError("quote_draft_not_reviewable", "Quote draft is not awaiting field corrections.")
            envelope = ReviewEnvelope.model_validate(
                session.get(WorkflowArtifact, draft.review_artifact_id).payload
            )
            blocking = {
                finding.field_name
                for finding in (envelope.review.findings if envelope.review else ())
                if finding.severity == ReviewSeverity.BLOCKING and not finding.resolved
            }
            targets = {str(item.get("field_name", "")) for item in corrections}
            if not corrections or not targets.issubset(blocking):
                raise BackendError(
                    "draft_correction_scope_invalid",
                    "Only unresolved blocking fields can be corrected.",
                    blocking_fields=sorted(blocking),
                )
            batch_artifact = session.get(WorkflowArtifact, draft.batch_artifact_id)
            if batch_artifact is None:
                raise ConflictError("draft_batch_missing", "Quote draft extraction batch is missing.")
            batch = ExtractionBatch.model_validate(batch_artifact.payload)
            events: list[CorrectionEvent] = []
            reviewed_at = datetime.now(timezone.utc)
            for item in corrections:
                field_name = str(item["field_name"])
                candidate = next((c for c in batch.candidates if c.field_name == field_name), None)
                action = (
                    CorrectionAction.USER_INPUT
                    if candidate is not None and candidate.validation_status == ValidationStatus.MISSING
                    else CorrectionAction.USER_CORRECTION
                )
                try:
                    batch, event = apply_candidate_correction(
                        batch,
                        field_name=field_name,
                        action=action,
                        raw_value=str(item["raw_value"]),
                        normalized_value=item["normalized_value"],
                        unit=item.get("unit"),
                        reason_code="QUOTE_DRAFT_FIELD_CORRECTION",
                        reason=str(item["reason"]),
                        reviewer_id=self.actor_id,
                        reviewed_at=reviewed_at,
                    )
                except (ValueError, TypeError) as exc:
                    raise BackendError(
                        "field_correction_invalid",
                        "Quote draft field correction is invalid.",
                        field_name=field_name,
                    ) from exc
                events.append(event)
            requirement_record = session.scalar(
                select(RequirementRecord)
                .where(RequirementRecord.task_id == task_id)
                .order_by(RequirementRecord.requirement_version.desc())
            )
            if requirement_record is None:
                raise ConflictError("requirement_missing", "The task has no procurement requirement.")
            requirement = ProcurementRequirement.model_validate(requirement_record.payload)
            corrected_payload = batch.model_dump(mode="json")
            corrected_artifact = WorkflowArtifact(
                artifact_id=new_id("artifact"),
                task_id=task_id,
                task_revision=draft.base_task_revision,
                artifact_type="EXTRACTION_BATCH",
                schema_version=batch.schema_version,
                parent_artifact_id=batch_artifact.artifact_id,
                quote_id=draft.proposed_quote_id,
                document_id=draft.proposed_document_id,
                payload=corrected_payload,
                content_sha256=content_hash(corrected_payload),
            )
            session.add(corrected_artifact)
            for event in events:
                payload = event.model_dump(mode="json")
                session.add(
                    WorkflowArtifact(
                        artifact_id=new_id("artifact"),
                        task_id=task_id,
                        task_revision=draft.base_task_revision,
                        artifact_type="CORRECTION_EVENT",
                        parent_artifact_id=corrected_artifact.artifact_id,
                        quote_id=draft.proposed_quote_id,
                        document_id=draft.proposed_document_id,
                        payload=payload,
                        content_sha256=content_hash(payload),
                    )
                )
            reviewed = review_extraction_batch(
                batch,
                self.quote_dictionary,
                CriticalityContext(required_revision=requirement.revision, base_unit=requirement.base_unit),
                input_is_synthetic=draft.is_synthetic,
                reviewed_at=reviewed_at,
                corrections=tuple(events),
            )
            review_payload = reviewed.model_dump(mode="json")
            review_artifact = WorkflowArtifact(
                artifact_id=new_id("artifact"),
                task_id=task_id,
                task_revision=draft.base_task_revision,
                artifact_type="REVIEW_ENVELOPE",
                schema_version=reviewed.schema_version,
                parent_artifact_id=corrected_artifact.artifact_id,
                quote_id=draft.proposed_quote_id,
                document_id=draft.proposed_document_id,
                payload=review_payload,
                content_sha256=content_hash(review_payload),
            )
            session.add(review_artifact)
            draft.batch_artifact_id = corrected_artifact.artifact_id
            draft.review_artifact_id = review_artifact.artifact_id
            draft.revision += 1
            draft.status = "READY_TO_SUBMIT" if reviewed.downstream_ready else "REVIEW_REQUIRED"
            session.flush()
            response = self._quote_draft_response(session, draft)
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=200,
                response=response,
            )
            return response

    def submit_quote_draft(
        self,
        task_id: str,
        draft_id: str,
        *,
        expected_task_revision: int,
        expected_draft_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        from .models import utc_now

        request = {
            "task_id": task_id,
            "draft_id": draft_id,
            "expected_task_revision": expected_task_revision,
            "expected_draft_revision": expected_draft_revision,
        }
        request_sha = content_hash(request)
        operation = f"submit_quote_draft:{draft_id}"
        source_path: Path | None = None
        final_path: Path | None = None
        try:
            with self.session_factory.begin() as session:
                repeated = self._existing_idempotent(
                    session, operation=operation, key=idempotency_key, request_sha256=request_sha
                )
                if repeated is not None:
                    return repeated
                task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
                draft = self._owned_quote_draft(session, task_id, draft_id, lock=True)
                if task is None:
                    raise NotFoundError("task_not_found", "Task was not found.")
                self._require_revision(task, expected_task_revision)
                if task.current_revision != draft.base_task_revision:
                    draft.status = "STALE"
                    draft.revision += 1
                    raise ConflictError("quote_draft_stale", "Task changed while the quote draft was open.")
                if draft.revision != expected_draft_revision:
                    raise ConflictError(
                        "quote_draft_revision_conflict",
                        "Quote draft revision has changed.",
                        expected=expected_draft_revision,
                        actual=draft.revision,
                    )
                if draft.status != "READY_TO_SUBMIT" or not draft.batch_artifact_id:
                    raise ConflictError(
                        "quote_draft_not_ready",
                        "Resolve every blocking field before formally submitting the quote.",
                    )
                self._supersede_current_graph(session, task)
                extension = ".pdf" if draft.media_type == "application/pdf" else ".csv"
                source_path = Path(draft.storage_path)
                final_path = (
                    self.storage_root
                    / task_id
                    / draft.proposed_quote_id
                    / "v1"
                    / draft.proposed_document_id
                    / f"source{extension}"
                )
                final_path.parent.mkdir(parents=True, exist_ok=True)
                if not source_path.exists():
                    raise ConflictError("draft_file_missing", "Quote draft file is missing.")
                if final_path.exists():
                    raise ConflictError("immutable_storage_conflict", "Quote storage location already exists.")
                source_path.rename(final_path)
                session.add(
                    Quote(
                        quote_id=draft.proposed_quote_id,
                        task_id=task_id,
                        supplier_id=draft.supplier_id,
                        current_version=1,
                    )
                )
                session.flush()
                session.add(
                    Document(
                        document_id=draft.proposed_document_id,
                        task_id=task_id,
                        quote_id=draft.proposed_quote_id,
                        quote_version=1,
                        document_version=1,
                        original_filename=draft.original_filename,
                        media_type=draft.media_type,
                        size_bytes=draft.size_bytes,
                        sha256=draft.sha256,
                        storage_path=str(final_path),
                        is_synthetic=draft.is_synthetic,
                    )
                )
                task.current_revision += 1
                task.status = "DRAFT"
                draft.status = "SUBMITTED"
                draft.revision += 1
                draft.submitted_at = utc_now()
                draft.storage_path = str(final_path)
                session.add(
                    TaskRevision(
                        revision_id=new_id("rev"),
                        task_id=task_id,
                        revision=task.current_revision,
                        change_type="QUOTE_DRAFT_SUBMITTED",
                        actor_id=self.actor_id,
                        request_sha256=request_sha,
                    )
                )
                response = {
                    "task_id": task_id,
                    "task_revision": task.current_revision,
                    "quote_draft_id": draft.quote_draft_id,
                    "draft_revision": draft.revision,
                    "status": draft.status,
                    "quote_id": draft.proposed_quote_id,
                    "quote_version": 1,
                    "document_id": draft.proposed_document_id,
                    "document_version": 1,
                    "document_sha256": draft.sha256,
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
            if final_path is not None and final_path.exists() and source_path is not None:
                source_path.parent.mkdir(parents=True, exist_ok=True)
                final_path.rename(source_path)
            raise

    def discard_quote_draft(
        self,
        task_id: str,
        draft_id: str,
        *,
        expected_draft_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "task_id": task_id,
            "draft_id": draft_id,
            "expected_draft_revision": expected_draft_revision,
        }
        request_sha = content_hash(request)
        operation = f"discard_quote_draft:{draft_id}"
        with self.session_factory.begin() as session:
            repeated = self._existing_idempotent(
                session, operation=operation, key=idempotency_key, request_sha256=request_sha
            )
            if repeated is not None:
                return repeated
            draft = self._owned_quote_draft(session, task_id, draft_id, lock=True)
            if draft.revision != expected_draft_revision:
                raise ConflictError(
                    "quote_draft_revision_conflict",
                    "Quote draft revision has changed.",
                    expected=expected_draft_revision,
                    actual=draft.revision,
                )
            if draft.status in {"SUBMITTED", "DISCARDED"}:
                raise ConflictError("quote_draft_terminal", "Quote draft is already closed.")
            draft.status = "DISCARDED"
            draft.revision += 1
            for job in session.scalars(
                select(Job).where(
                    Job.quote_draft_id == draft_id,
                    Job.status.in_(("PENDING", "RUNNING")),
                )
            ):
                job.status = "SUPERSEDED"
            session.flush()
            response = self._quote_draft_response(session, draft)
            self._save_idempotent(
                session,
                operation=operation,
                key=idempotency_key,
                request_sha256=request_sha,
                response_status=200,
                response=response,
            )
            return response

    def job_type(self, job_id: str) -> str:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                raise NotFoundError("job_not_found", "Job was not found.")
            return job.job_type

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
                        "planned_order_date": payload.get("planned_order_date"),
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
                "policy_set_version": task.policy_set_version,
                "policy_index_version": task.policy_index_version,
                "policy_category": task.policy_category,
                "policy_region": task.policy_region,
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

    def next_pending_job_id(self) -> str | None:
        """Return the oldest pending job for the single background worker."""

        with self.session_factory() as session:
            return session.scalar(
                select(Job.job_id)
                .where(Job.status == "PENDING")
                .order_by(Job.created_at.asc(), Job.job_id.asc())
                .limit(1)
            )

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
                    "decision_impact": self._decision_impact_payload(session, artifact.artifact_id),
                    "policy_retrievals": self._policy_retrieval_payloads(
                        session, artifact.artifact_id
                    ),
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
                "decision_impact": self._decision_impact_payload(session, artifact.artifact_id),
                "policy_retrievals": self._policy_retrieval_payloads(
                    session, artifact.artifact_id
                ),
            }

    @staticmethod
    def _decision_impact_payload(session: Session, result_id: str) -> dict[str, Any] | None:
        result = session.get(WorkflowArtifact, result_id)
        snapshot = session.get(WorkflowArtifact, result.parent_artifact_id) if result and result.parent_artifact_id else None
        impact_id = snapshot.payload.get("decision_impact_artifact_id") if snapshot else None
        artifact = session.get(WorkflowArtifact, impact_id) if impact_id else None
        return dict(artifact.payload) if artifact is not None else None

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

    def list_review_problems(self, task_id: str) -> dict[str, Any]:
        """Read all current findings together without inventing human approvals."""
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            quotes = session.scalars(select(Quote).where(
                Quote.task_id == task_id, Quote.active.is_(True)
            ).order_by(Quote.quote_id)).all()
            reports = []
            problems = []
            impact = session.scalar(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task_id,
                WorkflowArtifact.graph_run_id == task.current_graph_run_id,
                WorkflowArtifact.task_revision == task.current_revision,
                WorkflowArtifact.artifact_type == "DECISION_IMPACT_RESULT",
            ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc()))
            nonblocking = set(impact.payload.get("nonblocking_unknown_quote_ids", [])) if impact else set()
            for quote in quotes:
                execution = session.scalar(select(DocumentExecution).join(
                    Document, Document.document_id == DocumentExecution.document_id
                ).where(
                    DocumentExecution.graph_run_id == task.current_graph_run_id,
                    Document.quote_id == quote.quote_id,
                    Document.quote_version == quote.current_version,
                ).order_by(DocumentExecution.document_execution_id))
                batch_artifact = session.get(WorkflowArtifact, execution.batch_artifact_id) if execution and execution.batch_artifact_id else None
                review_artifact = session.get(WorkflowArtifact, execution.review_artifact_id) if execution and execution.review_artifact_id else None
                # Corrections queue a new batch, but do not reuse an old review.
                batch = batch_artifact.payload if batch_artifact else {}
                candidates = {c["field_name"]: c for c in batch.get("candidates", [])}
                document = session.get(Document, execution.document_id) if execution else None
                current_review = bool(
                    review_artifact and batch_artifact
                    and review_artifact.graph_run_id == task.current_graph_run_id
                    and review_artifact.task_revision <= task.current_revision
                    and review_artifact.payload.get("batch") == batch_artifact.payload
                )
                envelope = review_artifact.payload if current_review else {}
                report = {
                    "quote_id": quote.quote_id, "supplier_id": quote.supplier_id,
                    "quote_version": quote.current_version,
                    "document_id": document.document_id if document else None,
                    "original_filename": document.original_filename if document else None,
                    "batch_artifact_id": batch_artifact.artifact_id if batch_artifact else None,
                    "review_artifact_id": review_artifact.artifact_id if current_review else None,
                    "review_status": envelope.get("review_status"),
                    "review_pending": not current_review,
                    "fields": list(candidates.values()),
                    "evidence_sources": [
                        {key: source.get(key) for key in (
                            "source_id", "kind", "raw_text", "page_number", "row_number", "column_name"
                        )}
                        for source in batch.get("parsed_input", {}).get("sources", [])
                    ],
                }
                reports.append(report)
                for finding in (envelope.get("review") or {}).get("findings", []):
                    if finding.get("resolved") or finding.get("decision") == "PASS":
                        continue
                    candidate = candidates.get(finding["field_name"], {})
                    is_blocking = finding.get("severity") == "BLOCKING" or finding.get("decision") == "REJECTED"
                    # Dominance proof applies only to audited missing fees.
                    needs_resolution = is_blocking and quote.quote_id not in nonblocking
                    problems.append(dict(finding) | {
                        "quote_id": quote.quote_id, "quote_version": quote.current_version,
                        "field_version": candidate.get("field_version"),
                        "raw_value": candidate.get("raw_value"),
                        "normalized_value": candidate.get("normalized_value"),
                        "unit": candidate.get("unit"),
                        "document_id": report["document_id"],
                        "original_filename": report["original_filename"],
                        "needs_resolution": needs_resolution,
                        "resolution": "FIELD_CORRECTION" if candidate and finding.get("review_reason") != "SYSTEM_IDENTITY_ERROR" else "REEXTRACT_OR_SYSTEM_REPAIR",
                    })
                # The comparison boundary can detect issues beyond extraction
                # findings (for example FREE shipping with a positive amount).
                comparison_rows = (impact.payload.get("comparison") or {}).get("supplier_results", []) if impact else []
                comparison_row = next((row for row in comparison_rows if row["quote_id"] == quote.quote_id), {})
                for issue in comparison_row.get("pending_reasons", []):
                    for field_name in issue.get("fields", []):
                        if any(p["quote_id"] == quote.quote_id and p["field_name"] == field_name
                               and issue["code"] in p["codes"] for p in problems):
                            continue
                        candidate = candidates.get(field_name, {})
                        problems.append({
                            "finding_id": f"comparison:{quote.quote_id}:{field_name}:{issue['code']}",
                            "field_name": field_name, "codes": [issue["code"]],
                            "message": issue["message"], "decision": "REVIEW_REQUIRED",
                            "severity": "BLOCKING", "review_reason": "COMPARISON_INPUT_ISSUE",
                            "quote_id": quote.quote_id, "quote_version": quote.current_version,
                            "field_version": candidate.get("field_version"),
                            "raw_value": candidate.get("raw_value"),
                            "normalized_value": candidate.get("normalized_value"), "unit": candidate.get("unit"),
                            "document_id": report["document_id"], "original_filename": report["original_filename"],
                            "needs_resolution": quote.quote_id not in nonblocking,
                            "resolution": "FIELD_CORRECTION" if candidate else "REEXTRACT_OR_SYSTEM_REPAIR",
                        })
            return {
                "task_id": task_id, "task_revision": task.current_revision,
                "graph_run_id": task.current_graph_run_id, "task_status": task.status,
                "review_pending": not reports or any(report["review_pending"] for report in reports),
                "quotes": reports, "problems": problems,
                "blocking_problem_count": sum(p["needs_resolution"] for p in problems),
                "problem_count": len(problems),
            }

    def selection_analysis_input(self, task_id: str, *, expected_task_revision: int,
                                 evaluated_at: datetime | None = None):
        """Current audited quote scope only; never turn form values into reviewed facts."""
        from supplier_comparison.extraction import ReviewEnvelope
        from supplier_comparison.extraction.errors import DownstreamNotReadyError
        from supplier_comparison.rules import ComparisonRequest, DecisionImpactRequest, quote_input_for_decision_impact

        task = self.get_task(task_id)
        review = self.list_review_problems(task_id)
        if task['task_revision'] != expected_task_revision or review['task_revision'] != expected_task_revision:
            raise ConflictError('task_revision_conflict', 'Analysis requires the current task revision.')
        if review['review_pending'] or not task['current_graph_run_id']:
            raise ConflictError('selection_review_required', 'Run extraction and review before selection analysis.')
        context = self.workflow_context(task['current_graph_run_id'])
        documents = {d['quote_id']: d for d in context['documents']}
        envelopes = []
        try:
            for row in review['quotes']:
                envelope = ReviewEnvelope.model_validate(self.artifact_payload(row['review_artifact_id']))
                batch = envelope.batch
                document = documents[row['quote_id']]
                if batch is None:
                    raise ConflictError('selection_review_required', 'A model-failed quote cannot be analyzed.')
                parsed = batch.parsed_input
                identity = parsed.context
                if (identity.task_id != task_id or identity.quote_id != row['quote_id']
                        or identity.quote_version != document['quote_version']
                        or identity.document_id != document['document_id']
                        or identity.document_version != document['document_version']
                        or parsed.document_sha256 != document['document_sha256']):
                    raise ConflictError('selection_input_stale', 'Reviewed input identity no longer matches the quote.')
                envelopes.append(envelope)
            if set(documents) != {r['quote_id'] for r in review['quotes']}:
                raise ConflictError('selection_input_stale', 'Analysis must cover every active quote.')
            quotes = tuple(quote_input_for_decision_impact(e) for e in envelopes)
        except DownstreamNotReadyError as exc:
            raise ConflictError('selection_review_required', 'Resolve unsafe review findings before analysis.') from exc
        # Freeze the workflow evaluation instant instead of silently changing quote validity.
        if evaluated_at is None:
            with self.session_factory() as session:
                report = session.scalar(select(WorkflowArtifact).where(
                    WorkflowArtifact.task_id == task_id,
                    WorkflowArtifact.graph_run_id == task['current_graph_run_id'],
                    WorkflowArtifact.task_revision == expected_task_revision,
                    WorkflowArtifact.artifact_type == 'DECISION_IMPACT_RESULT',
                ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc()))
                stamp = report.payload.get('comparison', {}).get('evaluated_at') if report else None
            if not stamp:
                raise ConflictError('selection_review_required', 'A frozen preliminary comparison is required.')
            evaluated_at = datetime.fromisoformat(stamp)
        result = DecisionImpactRequest(
            task_id=task_id, task_revision=expected_task_revision,
            comparison=ComparisonRequest(requirement=ProcurementRequirement.model_validate(task['requirement']),
                                         quotes=quotes, evaluated_at=evaluated_at),
            policy_binding={key: context[key] for key in (
                'policy_set_version', 'policy_index_version', 'policy_category', 'policy_region')},
            review_bindings={e.batch.parsed_input.context.quote_id:
                            e.batch.parsed_input.document_sha256 + ':' + e.review.review_run_id for e in envelopes},
        )
        latest = self.get_task(task_id)
        if latest['task_revision'] != expected_task_revision or latest['current_graph_run_id'] != task['current_graph_run_id']:
            raise ConflictError('selection_input_stale', 'Input changed during analysis.')
        return result

    def selection_gaps(self, task_id: str, *, expected_task_revision: int):
        from supplier_comparison.rules import analyze_selection_gap, draft_clarification
        before = self.get_task(task_id)
        result = analyze_selection_gap(self.selection_analysis_input(task_id, expected_task_revision=expected_task_revision))
        latest = self.get_task(task_id)
        if latest['task_revision'] != expected_task_revision or latest['current_graph_run_id'] != before['current_graph_run_id']:
            raise ConflictError('selection_input_stale', 'Input changed during analysis.')
        return result.model_dump(mode='json') | {'clarification_drafts': [draft_clarification(gap) for gap in result.gaps]}

    def requirement_simulation(self, task_id: str, *, expected_task_revision: int, changes, user_authorized: bool):
        from supplier_comparison.rules import simulate_requirement_change
        before = self.get_task(task_id)
        request = self.selection_analysis_input(task_id, expected_task_revision=expected_task_revision)
        try:
            result = simulate_requirement_change(request, changes, user_authorized=user_authorized)
        except ValueError as exc:
            raise BackendError('simulation_change_invalid', 'Authorized changes must satisfy the requirement contract.') from exc
        latest = self.get_task(task_id)
        if latest['task_revision'] != expected_task_revision or latest['current_graph_run_id'] != before['current_graph_run_id']:
            raise ConflictError('selection_input_stale', 'Input changed during simulation.')
        return {'task_id': task_id, 'task_revision': expected_task_revision,
                'result': result.model_dump(mode='json')}

    def reserve_policy_retry(self, task_id: str, *, graph_run_id: str, task_revision: int,
                             max_attempts: int, payload: dict) -> bool:
        """Reserve a durable graph-wide retry BEFORE external IO (including replay)."""
        with self.session_factory.begin() as session:
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError('task_not_found', 'Task was not found.')
            if task.current_revision != task_revision or task.current_graph_run_id != graph_run_id:
                raise ConflictError('investigation_input_changed', 'Policy retry inputs are stale.')
            attempts = session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task_id, WorkflowArtifact.graph_run_id == graph_run_id,
                WorkflowArtifact.artifact_type == 'POLICY_RETRY_ATTEMPT',
            )).all()
            if len(attempts) >= max_attempts:
                return False
            session.add(WorkflowArtifact(
                artifact_id=new_id('artifact'), task_id=task_id, task_revision=task_revision,
                graph_run_id=graph_run_id, artifact_type='POLICY_RETRY_ATTEMPT',
                payload=payload, content_sha256=content_hash(payload), schema_version='policy-investigation/1.0.0',
            ))
        return True

    def list_investigations(self, task_id: str) -> list[dict[str, Any]]:
        """Latest public snapshot per case; historical records never become authority."""
        with self.session_factory() as session:
            task = session.get(Task, task_id)
            if task is None or task.owner_id != self.actor_id:
                raise NotFoundError("task_not_found", "Task was not found.")
            artifacts = session.scalars(select(WorkflowArtifact).where(
                WorkflowArtifact.task_id == task_id,
                WorkflowArtifact.artifact_type == "INVESTIGATION_CASE",
            ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc())).all()
            records = {}
            for artifact in artifacts:
                case_id = artifact.payload["case_id"]
                if case_id in records:
                    continue
                current = artifact.graph_run_id == task.current_graph_run_id and artifact.task_revision == task.current_revision
                records[case_id] = dict(artifact.payload) | {
                    "artifact_id": artifact.artifact_id, "is_current": current,
                    "stored_status": artifact.payload["status"],
                    "status": artifact.payload["status"] if current else "STALE",
                    "stop_reason": artifact.payload["stop_reason"] if current else "INPUT_CHANGED",
                }
            return list(records.values())

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
            payloads = [dict(artifact.payload) for artifact in artifacts]
            # A second submission must retain audit events for previously
            # corrected fields. Follow immutable ancestry, never another quote.
            candidates = {c["field_name"]: c for c in batch_artifact.payload.get("candidates", [])}
            seen_fields = {payload["field_name"] for payload in payloads}
            visited = {batch_artifact.artifact_id}
            parent_id = batch_artifact.parent_artifact_id
            while parent_id and parent_id not in visited:
                visited.add(parent_id)
                parent = session.get(WorkflowArtifact, parent_id)
                if parent is None or parent.task_id != batch_artifact.task_id or parent.quote_id != batch_artifact.quote_id:
                    break
                historical = [parent] if parent.artifact_type == "CORRECTION_EVENT" else []
                if parent.artifact_type == "EXTRACTION_BATCH":
                    historical = session.scalars(select(WorkflowArtifact).where(
                        WorkflowArtifact.graph_run_id == parent.graph_run_id,
                        WorkflowArtifact.task_revision == parent.task_revision,
                        WorkflowArtifact.quote_id == parent.quote_id,
                        WorkflowArtifact.artifact_type == "CORRECTION_EVENT",
                    ).order_by(WorkflowArtifact.created_at.desc(), WorkflowArtifact.artifact_id.desc())).all()
                for event in historical:
                    payload = event.payload
                    candidate = candidates.get(payload.get("field_name"), {})
                    if payload["field_name"] not in seen_fields and payload.get("after", {}).get("field_id") == candidate.get("field_id"):
                        payloads.append(dict(payload))
                        seen_fields.add(payload["field_name"])
                parent_id = parent.parent_artifact_id
            return payloads

    def correct_field(self, *, task_id: str, quote_id: str, field_name: str,
                      expected_task_revision: int, raw_value: str,
                      normalized_value: str | int | bool, unit: str | None,
                      reason: str, idempotency_key: str) -> dict[str, Any]:
        return self.correct_fields(task_id=task_id, expected_task_revision=expected_task_revision,
            corrections=[dict(quote_id=quote_id, field_name=field_name, raw_value=raw_value,
                              normalized_value=normalized_value, unit=unit, reason=reason)],
            idempotency_key=idempotency_key, _single=True)

    def correct_fields(self, *, task_id: str, expected_task_revision: int,
                       corrections: list[dict[str, Any]], idempotency_key: str,
                       _single: bool = False) -> dict[str, Any]:
        from .models import utc_now

        if not corrections or len(corrections) > 100:
            raise BackendError("field_correction_invalid", "Submit between 1 and 100 corrections.")
        pairs = [(c.get("quote_id"), c.get("field_name")) for c in corrections]
        if any(not q or not n for q, n in pairs) or len(set(pairs)) != len(pairs):
            raise BackendError("field_correction_invalid", "Duplicate or empty correction fields.")
        request = {"task_id": task_id, "expected_task_revision": expected_task_revision,
                   "corrections": corrections}
        if _single:
            request = {"task_id": task_id, "expected_task_revision": expected_task_revision, **corrections[0]}
        request_sha = content_hash(request)
        operation = (f"correct_field:{task_id}:{pairs[0][0]}:{pairs[0][1]}" if _single
                     else f"correct_fields:{task_id}")
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
                raise NotFoundError("quote_not_found", "Quote was not found.")
            for quote_id, _field in pairs:
                quote = session.get(Quote, quote_id)
                if quote is None or quote.task_id != task_id or not quote.active:
                    raise NotFoundError("quote_not_found", "Active quote was not found.")
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
                parsed = ExtractionBatch.model_validate(artifact.payload).parsed_input
                document = session.get(Document, parsed.context.document_id)
                if (
                    parsed.context.task_id != task_id
                    or parsed.context.quote_id != active_quote.quote_id
                    or parsed.context.quote_version != active_quote.current_version
                    or document is None or document.task_id != task_id
                    or document.quote_id != active_quote.quote_id
                    or document.quote_version != active_quote.current_version
                    or parsed.context.document_version != document.document_version
                    or parsed.document_sha256 != document.sha256
                ):
                    raise ConflictError("extraction_batch_stale", "Re-extract the current quote before correction.", quote_id=active_quote.quote_id)
                latest_batches[active_quote.quote_id] = artifact
            reviewed_at = utc_now()
            corrected_batches = {}
            events = []
            errors = []
            for item in corrections:
                quote_id = item["quote_id"]
                source_artifact = latest_batches[quote_id]
                source_batch = corrected_batches.get(quote_id) or ExtractionBatch.model_validate(source_artifact.payload)
                candidate = next((c for c in source_batch.candidates if c.field_name == item["field_name"]), None)
                if "expected_field_version" in item and (
                    candidate is None or candidate.field_version != item["expected_field_version"]
                ):
                    errors.append({"quote_id": quote_id, "field_name": item["field_name"],
                                   "code": "field_version_conflict", "actual": candidate.field_version if candidate else None})
                    continue
                try:
                    corrected_batch, correction = apply_candidate_correction(
                        source_batch, field_name=item["field_name"], action=CorrectionAction.USER_CORRECTION,
                        raw_value=item["raw_value"], normalized_value=item["normalized_value"],
                        unit=item.get("unit"), reason_code="AUTHORIZED_FIELD_CORRECTION",
                        reason=item["reason"], reviewer_id=self.actor_id, reviewed_at=reviewed_at)
                except (ValueError, TypeError, KeyError) as exc:
                    errors.append({"quote_id": quote_id, "field_name": item["field_name"],
                                   "code": "field_correction_invalid"})
                    continue
                corrected_batches[quote_id] = corrected_batch
                events.append((quote_id, correction))
            if errors:
                if _single and errors[0]["code"] == "field_correction_invalid":
                    raise BackendError("field_correction_invalid", "Field correction is invalid.",
                                       quote_id=errors[0]["quote_id"], field_name=errors[0]["field_name"])
                error_type = ConflictError if any(e["code"] == "field_version_conflict" for e in errors) else BackendError
                raise error_type("field_correction_batch_invalid", "No corrections were saved; resolve all reported errors.", errors=errors)
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
            parents = {qid: artifact.artifact_id for qid, artifact in latest_batches.items()}
            for quote_id, correction in events:
                payload = correction.model_dump(mode="json")
                artifact = WorkflowArtifact(artifact_id=new_id("artifact"), task_id=task_id,
                    task_revision=next_revision, artifact_type="CORRECTION_EVENT", quote_id=quote_id,
                    graph_run_id=graph_run_id, parent_artifact_id=parents[quote_id],
                    payload=payload, content_sha256=content_hash(payload))
                session.add(artifact)
                parents[quote_id] = artifact.artifact_id
            for quote_id, corrected_batch in corrected_batches.items():
                payload = corrected_batch.model_dump(mode="json")
                artifact = WorkflowArtifact(artifact_id=new_id("artifact"), task_id=task_id,
                    task_revision=next_revision, artifact_type="EXTRACTION_BATCH",
                    schema_version=corrected_batch.schema_version, quote_id=quote_id,
                    graph_run_id=graph_run_id, parent_artifact_id=parents[quote_id],
                    payload=payload, content_sha256=content_hash(payload))
                session.add(artifact)
                latest_batches[quote_id] = artifact
            documents = session.scalars(
                select(Document).where(Document.task_id == task_id, Document.quote_id.in_(latest_batches))
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
                    change_type=(f"FIELD_CORRECTED:{pairs[0][1]}" if _single else "FIELDS_CORRECTED_BATCH"),
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
            task = session.scalar(select(Task).where(Task.task_id == task_id).with_for_update())
            if task is None:
                raise NotFoundError("task_not_found", "Task was not found.")
            # Windows can produce equal clock ticks for successive saves. Random
            # artifact UUIDs must not decide which investigation snapshot is latest.
            latest_stamp = session.scalar(select(WorkflowArtifact.created_at).where(
                WorkflowArtifact.task_id == task_id,
            ).order_by(WorkflowArtifact.created_at.desc()).limit(1))
            stamp = datetime.now(timezone.utc)
            if latest_stamp is not None:
                latest_stamp = latest_stamp.replace(tzinfo=timezone.utc) if latest_stamp.tzinfo is None else latest_stamp
                if latest_stamp >= stamp:
                    stamp = latest_stamp + timedelta(microseconds=1)
            session.add(
                WorkflowArtifact(
                    artifact_id=artifact_id,
                    created_at=stamp,
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
            session.flush()
            dictionary_sha = hashlib.sha256(self.quote_dictionary_path.read_bytes()).hexdigest()
            documents = session.scalars(
                select(Document).where(Document.task_id == task_id)
            ).all()
            for document in documents:
                reviewed_draft = session.scalar(
                    select(QuoteDraft).where(
                        QuoteDraft.task_id == task_id,
                        QuoteDraft.proposed_document_id == document.document_id,
                        QuoteDraft.status == "SUBMITTED",
                        QuoteDraft.sha256 == document.sha256,
                        QuoteDraft.provider == provider,
                        QuoteDraft.model_id == model_id,
                        QuoteDraft.environment == environment,
                        QuoteDraft.prompt_version == prompt_version,
                        QuoteDraft.dictionary_sha256 == dictionary_sha,
                        QuoteDraft.batch_artifact_id.is_not(None),
                    )
                )
                if reviewed_draft is not None:
                    session.add(
                        DocumentExecution(
                            document_execution_id=new_id("docexec"),
                            graph_run_id=graph_run_id,
                            document_id=document.document_id,
                            status="EXTRACTED",
                            calls_used=reviewed_draft.calls_used,
                            max_calls=reviewed_draft.max_calls,
                            parsed_artifact_id=reviewed_draft.parsed_artifact_id,
                            batch_artifact_id=reviewed_draft.batch_artifact_id,
                            review_artifact_id=None,
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
            if issue.issue_type == "BATCH_FIELD_REVIEW":
                raise BackendError("batch_review_requires_correction", "Use the versioned batch field correction endpoint.")
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
            task.current_snapshot_id = None
            task.current_result_id = None
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

    def _owned_quote_draft(
        self,
        session: Session,
        task_id: str,
        draft_id: str,
        *,
        lock: bool = False,
    ) -> QuoteDraft:
        query = select(QuoteDraft).where(
            QuoteDraft.quote_draft_id == draft_id,
            QuoteDraft.task_id == task_id,
            QuoteDraft.actor_id == self.actor_id,
        )
        if lock:
            query = query.with_for_update()
        draft = session.scalar(query)
        if draft is None:
            raise NotFoundError("quote_draft_not_found", "Quote draft was not found.")
        return draft

    def _quote_draft_response(self, session: Session, draft: QuoteDraft) -> dict[str, Any]:
        batch_artifact = (
            session.get(WorkflowArtifact, draft.batch_artifact_id)
            if draft.batch_artifact_id
            else None
        )
        review_artifact = (
            session.get(WorkflowArtifact, draft.review_artifact_id)
            if draft.review_artifact_id
            else None
        )
        batch_payload = dict(batch_artifact.payload) if batch_artifact is not None else {}
        sources = {
            source.get("source_id"): source
            for source in batch_payload.get("parsed_input", {}).get("sources", [])
        }
        fields: list[dict[str, Any]] = []
        for candidate in batch_payload.get("candidates", []):
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
        review_payload = dict(review_artifact.payload) if review_artifact is not None else {}
        review = review_payload.get("review") or {}
        job = session.scalar(
            select(Job)
            .where(Job.quote_draft_id == draft.quote_draft_id)
            .order_by(Job.created_at.desc(), Job.job_id.desc())
        )
        task = session.get(Task, draft.task_id)
        effective_status = draft.status
        if (
            task is not None
            and task.current_revision != draft.base_task_revision
            and draft.status in {"UPLOADED", "PROCESSING", "REVIEW_REQUIRED", "READY_TO_SUBMIT"}
        ):
            effective_status = "STALE"
        return {
            "quote_draft_id": draft.quote_draft_id,
            "task_id": draft.task_id,
            "base_task_revision": draft.base_task_revision,
            "draft_revision": draft.revision,
            "status": effective_status,
            "proposed_quote_id": draft.proposed_quote_id,
            "proposed_document_id": draft.proposed_document_id,
            "supplier_id": draft.supplier_id,
            "original_filename": draft.original_filename,
            "media_type": draft.media_type,
            "size_bytes": draft.size_bytes,
            "document_sha256": draft.sha256,
            "is_synthetic": draft.is_synthetic,
            "review_status": review_payload.get("review_status"),
            "review_findings": review.get("findings", []),
            "fields": fields,
            "calls_used": draft.calls_used,
            "max_calls": draft.max_calls,
            "error_code": draft.error_code,
            "error_message": draft.error_message,
            "job": (
                {
                    "job_id": job.job_id,
                    "job_type": job.job_type,
                    "job_status": job.status,
                    "attempts": job.attempts,
                    "created_at": self._aware_datetime(job.created_at).isoformat(),
                    "started_at": (
                        self._aware_datetime(job.started_at).isoformat()
                        if job.started_at is not None
                        else None
                    ),
                }
                if job is not None
                else None
            ),
            "created_at": self._aware_datetime(draft.created_at).isoformat(),
            "updated_at": self._aware_datetime(draft.updated_at).isoformat(),
            "submitted_at": (
                self._aware_datetime(draft.submitted_at).isoformat()
                if draft.submitted_at is not None
                else None
            ),
        }

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
    def _policy_binding_response(task: Task) -> dict[str, str] | None:
        values = (
            task.policy_set_version,
            task.policy_index_version,
            task.policy_category,
            task.policy_region,
        )
        if not all(values):
            return None
        return {
            "policy_set_version": task.policy_set_version,
            "policy_index_version": task.policy_index_version,
            "category": task.policy_category,
            "region": task.policy_region,
        }

    @staticmethod
    def _policy_retrieval_payloads(session, result_id: str) -> list[dict[str, Any]]:
        artifacts = session.scalars(
            select(WorkflowArtifact)
            .where(
                WorkflowArtifact.parent_artifact_id == result_id,
                WorkflowArtifact.artifact_type == "POLICY_RETRIEVAL_RESULT",
            )
            .order_by(WorkflowArtifact.created_at, WorkflowArtifact.artifact_id)
        ).all()
        return [dict(artifact.payload) for artifact in artifacts]

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
