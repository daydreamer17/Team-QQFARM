from __future__ import annotations

import hashlib
import json
import re
from contextlib import contextmanager
from threading import Lock
from weakref import WeakValueDictionary
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select, text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from supplier_comparison.backend.models import IdempotencyRecord, utc_now
from supplier_comparison.backend.service import (
    BackendError,
    ConflictError,
    NotFoundError,
    content_hash,
)

from .importer import PolicyImportError, PolicyImporter
from .manifest import LoadedClause, LoadedDocument, LoadedPolicyManifest
from .models import (
    PolicyClause,
    PolicyDocument,
    PolicyFileImport,
    PolicyFileImportClause,
    PolicyIndex,
    PolicySet,
)


_CLAUSE_HEADING = re.compile(r"^## \[([^\]]+)\]\s+(.+?)\s*$", re.MULTILINE)
_AMOUNT_VALUE = re.compile(
    r"\b(SGD|USD|EUR|CNY|MYR)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
    re.IGNORECASE,
)
_SUPPORTED_MEDIA = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
}
_NON_POLICY_FILENAMES = {"readme.md", "readme.txt"}
_CONFLICT_REASON_CODES = {"CONFLICTING_RULE", "DUPLICATE_CLAUSE_ID"}
_LOCAL_PUBLISH_LOCKS: WeakValueDictionary = WeakValueDictionary()
_LOCAL_PUBLISH_GUARD = Lock()


class UploadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyFileImportMetadata(UploadModel):
    # Technical identifiers remain accepted for administrative/backward-compatible
    # imports. Ordinary uploads omit them and let the service generate immutable,
    # collision-resistant values.
    policy_set_id: str | None = Field(default=None, min_length=1, max_length=128)
    policy_set_version: str | None = Field(default=None, min_length=1, max_length=128)
    policy_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_id: str | None = Field(default=None, min_length=1, max_length=128)
    document_version: str | None = Field(default=None, min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=512)
    effective_from: datetime
    effective_to: datetime | None = None
    categories: list[str] = Field(min_length=1)
    regions: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dates(self) -> "PolicyFileImportMetadata":
        if self.effective_from.tzinfo is None or self.effective_from.utcoffset() is None:
            raise ValueError("effective_from must include a timezone")
        if self.effective_to is not None:
            if self.effective_to.tzinfo is None or self.effective_to.utcoffset() is None:
                raise ValueError("effective_to must include a timezone")
            if self.effective_to <= self.effective_from:
                raise ValueError("effective_to must be after effective_from")
        return self

    @field_validator("categories", "regions")
    @classmethod
    def validate_scope(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned) or len(set(cleaned)) != len(cleaned):
            raise ValueError("scope values must be non-empty and unique")
        return cleaned


class PolicyDraftClauseInput(UploadModel):
    clause_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=50_000)
    control_code: str = Field(min_length=1, max_length=128)
    rule_parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("clause_id", "title", "text")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value cannot be blank")
        return cleaned

    @field_validator("control_code")
    @classmethod
    def normalize_control_code(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("control_code cannot be blank")
        return cleaned


class PolicyFileImportService:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        storage_root: str | Path,
        importer: PolicyImporter,
        *,
        actor_id: str,
        max_bytes: int = 5 * 1024 * 1024,
        max_pdf_pages: int = 50,
        max_extracted_characters: int = 200_000,
    ) -> None:
        if max_bytes < 1 or max_pdf_pages < 1 or max_extracted_characters < 1:
            raise ValueError("policy upload limits must be positive")
        self._sessions = sessions
        self._storage_root = Path(storage_root)
        self._importer = importer
        self._actor_id = actor_id
        self._max_bytes = max_bytes
        self._max_pdf_pages = max_pdf_pages
        self._max_extracted_characters = max_extracted_characters

    def upload_stream(
        self,
        *,
        metadata: PolicyFileImportMetadata,
        original_filename: str,
        media_type: str,
        stream: BinaryIO,
        idempotency_key: str,
    ) -> dict[str, Any]:
        normalized_media = media_type.split(";", 1)[0].strip().lower()
        extension = _SUPPORTED_MEDIA.get(normalized_media)
        safe_name = Path(original_filename.replace("\\", "/")).name
        if not safe_name or len(safe_name) > 512:
            raise BackendError(
                "policy_filename_invalid", "Policy filename is missing or too long."
            )
        if safe_name.casefold() in _NON_POLICY_FILENAMES:
            raise BackendError(
                "policy_document_not_policy",
                "README is documentation and cannot be uploaded as policy content.",
            )
        if extension is None or Path(safe_name).suffix.lower() != extension:
            raise BackendError(
                "unsupported_policy_media_type",
                "Only PDF, UTF-8 TXT, and Markdown policy files are supported.",
            )
        staging = self._storage_root / ".staging"
        staging.mkdir(parents=True, exist_ok=True)
        staged_path = staging / f"upload_{uuid4().hex}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            with staged_path.open("xb") as handle:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    if not isinstance(chunk, (bytes, bytearray)):
                        raise BackendError(
                            "upload_stream_invalid", "Policy upload stream must produce bytes."
                        )
                    size += len(chunk)
                    if size > self._max_bytes:
                        raise BackendError(
                            "file_too_large",
                            "Policy file exceeds the configured size limit.",
                            max_file_size_bytes=self._max_bytes,
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            if size == 0:
                raise BackendError("policy_file_empty", "Policy file cannot be empty.")
            extracted_text, extraction_metadata = self._extract(staged_path, normalized_media)
            if len(extracted_text) > self._max_extracted_characters:
                raise BackendError(
                    "policy_text_limit_exceeded",
                    "Extracted policy text exceeds the configured character limit.",
                    max_extracted_characters=self._max_extracted_characters,
                    actual_characters=len(extracted_text),
                )
            source_sha256 = digest.hexdigest()
            request_sha256 = content_hash(
                {
                    "metadata": metadata.model_dump(mode="json"),
                    "original_filename": safe_name,
                    "media_type": normalized_media,
                    "source_sha256": source_sha256,
                }
            )
            operation = "upload_policy_file"
            final_path: Path | None = None
            try:
                with self._sessions.begin() as session:
                    repeated = self._existing_idempotent(
                        session, operation, idempotency_key, request_sha256
                    )
                    if repeated is not None:
                        return repeated
                    policy_import_id = _id("pfi")
                    identifier_suffix = policy_import_id.removeprefix("pfi_")
                    resolved_metadata = metadata.model_dump()
                    resolved_metadata.update(
                        {
                            "policy_set_id": metadata.policy_set_id
                            or f"policy-set-{identifier_suffix}",
                            "policy_set_version": metadata.policy_set_version or "1.0.0",
                            "policy_id": metadata.policy_id
                            or f"POL-{identifier_suffix.upper()}",
                            "document_id": metadata.document_id
                            or f"DOC-{identifier_suffix.upper()}",
                            "document_version": metadata.document_version or "1.0.0",
                        }
                    )
                    final_path = (
                        self._storage_root
                        / policy_import_id
                        / "v1"
                        / f"source{extension}"
                    )
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    if final_path.exists():
                        raise ConflictError(
                            "immutable_storage_conflict",
                            "Generated policy storage location already exists.",
                        )
                    staged_path.rename(final_path)
                    draft_clauses = _draft_clauses(
                        extracted_text,
                        fallback_title=metadata.title,
                        fallback_id_prefix=identifier_suffix[:8].upper(),
                        document_hint=safe_name,
                    )
                    record = PolicyFileImport(
                        policy_import_id=policy_import_id,
                        actor_id=self._actor_id,
                        revision=1,
                        status=(
                            "READY_TO_PUBLISH"
                            if all(
                                clause["classification"]["status"] == "AUTO_ACCEPTED"
                                for clause in draft_clauses
                            )
                            else "REVIEW_REQUIRED"
                        ),
                        original_filename=safe_name,
                        media_type=normalized_media,
                        size_bytes=size,
                        source_sha256=source_sha256,
                        storage_path=str(final_path),
                        extracted_text=extracted_text,
                        extraction_metadata=extraction_metadata,
                        **resolved_metadata,
                    )
                    session.add(record)
                    session.flush()
                    for position, clause in enumerate(draft_clauses, start=1):
                        session.add(
                            PolicyFileImportClause(
                                policy_file_import_clause_id=_id("pfic"),
                                policy_import_id=policy_import_id,
                                position=position,
                                **clause,
                            )
                        )
                    session.flush()
                    self._reanalyze_policy_group(session, record)
                    response = self._serialize(session, record)
                    self._save_idempotent(
                        session,
                        operation,
                        idempotency_key,
                        request_sha256,
                        response,
                        status_code=201,
                    )
                    return response
            except Exception:
                if final_path is not None and final_path.exists():
                    final_path.unlink()
                raise
        finally:
            if staged_path.exists():
                staged_path.unlink()

    def get(self, policy_import_id: str) -> dict[str, Any]:
        with self._sessions() as session:
            record = self._owned_record(session, policy_import_id)
            return self._serialize(session, record)

    def list_imports(
        self,
        *,
        status: str | None = None,
        policy_set_id: str | None = None,
        policy_set_version: str | None = None,
        category: str | None = None,
        region: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List the current actor's imports without returning document bodies."""
        with self._sessions() as session:
            statement = select(PolicyFileImport).where(
                PolicyFileImport.actor_id == self._actor_id
            )
            if status is not None:
                statement = statement.where(PolicyFileImport.status == status)
            if policy_set_id is not None:
                statement = statement.where(
                    PolicyFileImport.policy_set_id == policy_set_id
                )
            if policy_set_version is not None:
                statement = statement.where(
                    PolicyFileImport.policy_set_version == policy_set_version
                )
            records = list(
                session.scalars(
                    statement.order_by(
                        PolicyFileImport.updated_at.desc(),
                        PolicyFileImport.policy_import_id,
                    )
                )
            )
            records = [
                record
                for record in records
                if (category is None or category in record.categories)
                and (region is None or region in record.regions)
            ]
            total = len(records)
            page = records[offset : offset + limit]
            import_ids = [record.policy_import_id for record in page]
            clause_counts: dict[str, int] = {}
            if import_ids:
                clause_counts = {
                    policy_import_id: int(count)
                    for policy_import_id, count in session.execute(
                        select(
                            PolicyFileImportClause.policy_import_id,
                            func.count(PolicyFileImportClause.policy_file_import_clause_id),
                        )
                        .where(PolicyFileImportClause.policy_import_id.in_(import_ids))
                        .group_by(PolicyFileImportClause.policy_import_id)
                    )
                }
            return {
                "items": [
                    self._serialize_summary(
                        record,
                        clause_count=clause_counts.get(record.policy_import_id, 0),
                    )
                    for record in page
                ],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def list_policy_sets(
        self,
        *,
        status: str = "PUBLISHED",
        include_inactive: bool = False,
        category: str | None = None,
        region: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List published policy set/index bindings that tasks can safely freeze."""
        with self._sessions() as session:
            policy_statuses = (
                [status, "INACTIVE"] if include_inactive else [status]
            )
            published_pairs = list(
                session.execute(
                    select(PolicySet, PolicyIndex)
                    .join(
                        PolicyIndex,
                        PolicyIndex.policy_set_record_id
                        == PolicySet.policy_set_record_id,
                    )
                    .where(
                        PolicySet.status.in_(policy_statuses),
                        PolicyIndex.status == "PUBLISHED",
                    )
                    .order_by(
                        PolicyIndex.published_at.desc(),
                        PolicySet.policy_set_id,
                        PolicySet.policy_set_version,
                        PolicyIndex.policy_index_version,
                    )
                )
            )
            policy_set_record_ids = {
                policy_set.policy_set_record_id for policy_set, _index in published_pairs
            }
            documents_by_set: dict[str, list[PolicyDocument]] = {
                record_id: [] for record_id in policy_set_record_ids
            }
            clause_counts: dict[str, int] = {}
            if policy_set_record_ids:
                for document in session.scalars(
                    select(PolicyDocument)
                    .where(
                        PolicyDocument.policy_set_record_id.in_(policy_set_record_ids)
                    )
                    .order_by(PolicyDocument.document_id)
                ):
                    documents_by_set[document.policy_set_record_id].append(document)
                clause_counts = {
                    record_id: int(count)
                    for record_id, count in session.execute(
                        select(
                            PolicyClause.policy_set_record_id,
                            func.count(PolicyClause.policy_clause_record_id),
                        )
                        .where(PolicyClause.policy_set_record_id.in_(policy_set_record_ids))
                        .group_by(PolicyClause.policy_set_record_id)
                    )
                }

            items: list[dict[str, Any]] = []
            for policy_set, index in published_pairs:
                documents = documents_by_set[policy_set.policy_set_record_id]
                matching_documents = [
                    document
                    for document in documents
                    if (category is None or category in document.categories)
                    and (region is None or region in document.regions)
                ]
                if not matching_documents:
                    continue
                categories = sorted(
                    {value for document in documents for value in document.categories}
                )
                regions = sorted(
                    {value for document in documents for value in document.regions}
                )
                published_at = index.published_at or policy_set.published_at
                items.append(
                    {
                        "policy_set_id": policy_set.policy_set_id,
                        "policy_set_version": policy_set.policy_set_version,
                        "policy_index_version": index.policy_index_version,
                        "status": policy_set.status,
                        "categories": categories,
                        "regions": regions,
                        "document_count": len(documents),
                        "clause_count": clause_counts.get(
                            policy_set.policy_set_record_id, 0
                        ),
                        "provider": index.provider,
                        "embedding_model": index.embedding_model,
                        "embedding_dimension": index.embedding_dimension,
                        "preprocessing_version": index.preprocessing_version,
                        "published_at": (
                            _utc_isoformat(published_at) if published_at else None
                        ),
                    }
                )

            total = len(items)
            return {
                "items": items[offset : offset + limit],
                "total": total,
                "limit": limit,
                "offset": offset,
            }

    def deactivate_policy_set(
        self,
        policy_set_id: str,
        policy_set_version: str,
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Prevent new bindings while retaining immutable policy history."""
        request_sha256 = content_hash(
            {
                "policy_set_id": policy_set_id,
                "policy_set_version": policy_set_version,
            }
        )
        operation = f"deactivate_policy_set:{policy_set_id}:{policy_set_version}"
        with self._sessions.begin() as session:
            repeated = self._existing_idempotent(
                session, operation, idempotency_key, request_sha256
            )
            if repeated is not None:
                return repeated
            policy_set = session.scalar(
                select(PolicySet)
                .where(
                    PolicySet.policy_set_id == policy_set_id,
                    PolicySet.policy_set_version == policy_set_version,
                )
                .with_for_update()
            )
            if policy_set is None:
                raise NotFoundError(
                    "policy_set_not_found", "Policy set version was not found."
                )
            if policy_set.status not in {"PUBLISHED", "INACTIVE"}:
                raise ConflictError(
                    "policy_set_not_published",
                    "Only a published policy set version can be deactivated.",
                    status=policy_set.status,
                )
            published_index_count = session.scalar(
                select(func.count())
                .select_from(PolicyIndex)
                .where(
                    PolicyIndex.policy_set_record_id
                    == policy_set.policy_set_record_id,
                    PolicyIndex.status == "PUBLISHED",
                )
            )
            if not published_index_count:
                raise ConflictError(
                    "policy_set_index_unavailable",
                    "Published policy index was not found.",
                )
            policy_set.status = "INACTIVE"
            response = {
                "policy_set_id": policy_set.policy_set_id,
                "policy_set_version": policy_set.policy_set_version,
                "status": policy_set.status,
            }
            self._save_idempotent(
                session,
                operation,
                idempotency_key,
                request_sha256,
                response,
                status_code=200,
            )
            return response

    def require_active_binding(
        self,
        *,
        policy_set_version: str,
        policy_index_version: str,
        category: str,
        region: str,
    ) -> None:
        """Reject new bindings to missing, inactive, or out-of-scope versions."""
        with self._sessions() as session:
            policy_set = session.scalar(
                select(PolicySet)
                .join(
                    PolicyIndex,
                    PolicyIndex.policy_set_record_id
                    == PolicySet.policy_set_record_id,
                )
                .where(
                    PolicySet.policy_set_version == policy_set_version,
                    PolicySet.status == "PUBLISHED",
                    PolicyIndex.policy_index_version == policy_index_version,
                    PolicyIndex.status == "PUBLISHED",
                )
            )
            if policy_set is None:
                raise ConflictError(
                    "policy_binding_unavailable",
                    "The selected policy version is inactive or unavailable.",
                )
            documents = list(
                session.scalars(
                    select(PolicyDocument).where(
                        PolicyDocument.policy_set_record_id
                        == policy_set.policy_set_record_id
                    )
                )
            )
            if not any(
                category in document.categories and region in document.regions
                for document in documents
            ):
                raise ConflictError(
                    "policy_binding_scope_mismatch",
                    "The selected policy version does not cover this category and region.",
                )

    def replace_clauses(
        self,
        policy_import_id: str,
        *,
        expected_revision: int,
        clauses: list[PolicyDraftClauseInput],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not clauses:
            raise BackendError("policy_clauses_empty", "At least one reviewed clause is required.")
        clause_ids = [clause.clause_id for clause in clauses]
        if len(set(clause_ids)) != len(clause_ids):
            raise BackendError("duplicate_policy_clause_id", "Clause IDs must be unique.")
        request_sha256 = content_hash(
            {
                "policy_import_id": policy_import_id,
                "expected_revision": expected_revision,
                "clauses": [clause.model_dump(mode="json") for clause in clauses],
            }
        )
        operation = f"review_policy_file:{policy_import_id}"
        with self._sessions.begin() as session:
            repeated = self._existing_idempotent(
                session, operation, idempotency_key, request_sha256
            )
            if repeated is not None:
                return repeated
            record = self._owned_record(session, policy_import_id, lock=True)
            if record.status == "PUBLISHED":
                raise ConflictError(
                    "published_policy_immutable", "Published policy content cannot be changed."
                )
            if record.status == "PUBLISHING":
                raise ConflictError(
                    "policy_publish_in_progress", "Policy publication is already in progress."
                )
            self._require_revision(record, expected_revision)
            session.execute(
                delete(PolicyFileImportClause).where(
                    PolicyFileImportClause.policy_import_id == policy_import_id
                )
            )
            for position, clause in enumerate(clauses, start=1):
                session.add(
                    PolicyFileImportClause(
                        policy_file_import_clause_id=_id("pfic"),
                        policy_import_id=policy_import_id,
                        position=position,
                        classification={
                            "status": "AUTO_ACCEPTED",
                            "base_status": "AUTO_ACCEPTED",
                            "method": "MANUAL",
                            "reason_codes": ["MANUAL_CONFIRMED"],
                            "conflicts_with": [],
                        },
                        **clause.model_dump(),
                    )
                )
            record.revision += 1
            record.updated_at = utc_now()
            session.flush()
            self._reanalyze_policy_group(session, record)
            response = self._serialize(session, record)
            self._save_idempotent(
                session,
                operation,
                idempotency_key,
                request_sha256,
                response,
                status_code=200,
            )
            return response

    @contextmanager
    def _publication_lock(self, policy_import_id: str):
        with self._sessions() as session:
            record = self._owned_record(session, policy_import_id, lock=False)
            engine = session.get_bind()
            # Publishing two versions of one policy set must be serialized so
            # that only the latest completed publication remains active.
            publication_key = record.policy_set_id
        if engine.dialect.name == "postgresql":
            # Session locks survive importer commits, but are released when a
            # killed process loses its connection. No stale lease can hide a
            # still-running publisher or allow a second importer to overtake it.
            key = int.from_bytes(hashlib.sha256(("policy-publish:" + publication_key).encode()).digest()[:8], "big", signed=True)
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                if not connection.scalar(sql_text("SELECT pg_try_advisory_lock(:key)"), {"key": key}):
                    raise ConflictError("policy_publish_in_progress", "Policy publication is already running; retry after it finishes or the interrupted process exits.")
                try:
                    yield
                finally:
                    try:
                        connection.execute(sql_text("SELECT pg_advisory_unlock(:key)"), {"key": key})
                    except Exception:
                        connection.invalidate()
                        raise
        else:
            # SQLite is used only by the single-process test/local adapter.
            key = (id(engine), publication_key)
            with _LOCAL_PUBLISH_GUARD:
                lock = _LOCAL_PUBLISH_LOCKS.setdefault(key, Lock())
            if not lock.acquire(blocking=False):
                raise ConflictError("policy_publish_in_progress", "Policy publication is already running.")
            try:
                yield
            finally:
                lock.release()

    def publish(self, policy_import_id: str, *, expected_revision: int, idempotency_key: str) -> dict[str, Any]:
        with self._publication_lock(policy_import_id):
            return self._publish_locked(policy_import_id, expected_revision=expected_revision, idempotency_key=idempotency_key)

    def _publish_locked(
        self,
        policy_import_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request_sha256 = content_hash(
            {
                "policy_import_id": policy_import_id,
                "expected_revision": expected_revision,
            }
        )
        operation = f"publish_policy_file:{policy_import_id}"
        with self._sessions.begin() as session:
            repeated = self._existing_idempotent(
                session, operation, idempotency_key, request_sha256
            )
            if repeated is not None:
                return repeated
            record = self._owned_record(session, policy_import_id, lock=True)
            self._require_revision(record, expected_revision)
            if record.status not in {"READY_TO_PUBLISH", "PUBLISHING"}:
                raise ConflictError(
                    "policy_import_not_ready",
                    "Policy clauses must be reviewed before publication.",
                    status=record.status,
                )
            group_records = self._publication_group(session, record, lock=True)
            incomplete = [
                item.original_filename
                for item in group_records
                if item.status not in {"READY_TO_PUBLISH", "PUBLISHING"}
            ]
            if incomplete:
                raise ConflictError(
                    "policy_set_review_incomplete",
                    "Every file in the policy set must be reviewed before publication.",
                    filenames=incomplete,
                )
            manifest = self._build_manifest(session, group_records)
            group_revisions = {
                item.policy_import_id: item.revision for item in group_records
            }
            publishing_at = utc_now()
            for item in group_records:
                item.status = "PUBLISHING"
                item.updated_at = publishing_at

        try:
            outcome = self._importer.import_loaded(manifest, publish=True)
        except PolicyImportError as exc:
            with self._sessions.begin() as session:
                records = list(
                    session.scalars(
                        select(PolicyFileImport)
                        .where(
                            PolicyFileImport.policy_import_id.in_(group_revisions),
                            PolicyFileImport.actor_id == self._actor_id,
                        )
                        .with_for_update()
                    )
                )
                failed_at = utc_now()
                for item in records:
                    if item.revision == group_revisions[item.policy_import_id]:
                        item.status = "READY_TO_PUBLISH"
                        item.updated_at = failed_at
            if "same version has different content" in str(exc):
                raise ConflictError(
                    "policy_version_content_mismatch",
                    "The policy version is already published with different content.",
                ) from exc
            raise BackendError(
                "policy_publish_failed", "Policy embedding or publication failed."
            ) from exc

        with self._sessions.begin() as session:
            repeated = self._existing_idempotent(
                session, operation, idempotency_key, request_sha256
            )
            if repeated is not None:
                return repeated
            records = list(
                session.scalars(
                    select(PolicyFileImport)
                    .where(
                        PolicyFileImport.policy_import_id.in_(group_revisions),
                        PolicyFileImport.actor_id == self._actor_id,
                    )
                    .with_for_update()
                )
            )
            if len(records) != len(group_revisions):
                raise ConflictError(
                    "policy_import_revision_conflict",
                    "Policy set files changed during publication.",
                )
            published_at = utc_now()
            for item in records:
                self._require_revision(item, group_revisions[item.policy_import_id])
                item.status = "PUBLISHED"
                item.revision += 1
                item.policy_index_version = outcome.index_version
                item.published_import_run_id = outcome.import_run_id
                item.updated_at = published_at
            session.flush()
            record = next(
                item for item in records if item.policy_import_id == policy_import_id
            )
            response = self._serialize(session, record)
            self._save_idempotent(
                session,
                operation,
                idempotency_key,
                request_sha256,
                response,
                status_code=200,
            )
            return response

    def _extract(self, path: Path, media_type: str) -> tuple[str, dict[str, Any]]:
        if media_type in {"text/plain", "text/markdown"}:
            try:
                text = path.read_bytes().decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                format_name = "Markdown" if media_type == "text/markdown" else "TXT"
                error_code = (
                    "policy_markdown_not_utf8"
                    if media_type == "text/markdown"
                    else "policy_txt_not_utf8"
                )
                raise BackendError(
                    error_code,
                    f"{format_name} policy files must be UTF-8 encoded.",
                ) from exc
            if "\x00" in text:
                error_code = (
                    "policy_markdown_binary"
                    if media_type == "text/markdown"
                    else "policy_txt_binary"
                )
                raise BackendError(
                    error_code, "Policy text file contains binary data."
                )
            text = text.strip()
            if not text:
                raise BackendError("policy_document_no_text", "Policy document contains no text.")
            parser = (
                "utf-8-markdown/1.0"
                if media_type == "text/markdown"
                else "utf-8-text/1.0"
            )
            return text, {"parser": parser, "page_count": None}

        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise BackendError("invalid_policy_pdf", "Uploaded file is not a valid PDF.")
        try:
            with pdfplumber.open(path) as pdf:
                if len(pdf.pages) > self._max_pdf_pages:
                    raise BackendError(
                        "policy_pdf_page_limit_exceeded",
                        "Policy PDF exceeds the configured page limit.",
                        max_pdf_pages=self._max_pdf_pages,
                        actual_pages=len(pdf.pages),
                    )
                pages = [(page.extract_text() or "").strip() for page in pdf.pages]
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError("invalid_policy_pdf", "Policy PDF is damaged or unreadable.") from exc
        text = "\n\n".join(page for page in pages if page).strip()
        if not text:
            raise BackendError(
                "policy_pdf_requires_ocr",
                "Policy PDF contains no extractable text; OCR is not enabled for policy uploads.",
            )
        return text, {"parser": "pdfplumber/1.0", "page_count": len(pages)}

    def _owned_record(
        self, session: Session, policy_import_id: str, *, lock: bool = False
    ) -> PolicyFileImport:
        statement = select(PolicyFileImport).where(
            PolicyFileImport.policy_import_id == policy_import_id,
            PolicyFileImport.actor_id == self._actor_id,
        )
        if lock:
            statement = statement.with_for_update()
        record = session.scalar(statement)
        if record is None:
            raise NotFoundError("policy_import_not_found", "Policy import was not found.")
        return record

    @staticmethod
    def _require_revision(record: PolicyFileImport, expected: int) -> None:
        if record.revision != expected:
            raise ConflictError(
                "policy_import_revision_conflict",
                "Policy import revision has changed.",
                expected=expected,
                actual=record.revision,
            )

    def _serialize(self, session: Session, record: PolicyFileImport) -> dict[str, Any]:
        clauses = list(
            session.scalars(
                select(PolicyFileImportClause)
                .where(PolicyFileImportClause.policy_import_id == record.policy_import_id)
                .order_by(PolicyFileImportClause.position)
            )
        )
        return {
            "policy_import_id": record.policy_import_id,
            "status": record.status,
            "revision": record.revision,
            "original_filename": record.original_filename,
            "media_type": record.media_type,
            "size_bytes": record.size_bytes,
            "source_sha256": record.source_sha256,
            "extracted_text": record.extracted_text,
            "extraction_metadata": dict(record.extraction_metadata),
            "policy_set_id": record.policy_set_id,
            "policy_set_version": record.policy_set_version,
            "policy_id": record.policy_id,
            "document_id": record.document_id,
            "document_version": record.document_version,
            "title": record.title,
            "effective_from": _utc_isoformat(record.effective_from),
            "effective_to": _utc_isoformat(record.effective_to) if record.effective_to else None,
            "categories": list(record.categories),
            "regions": list(record.regions),
            "policy_index_version": record.policy_index_version,
            "published_import_run_id": record.published_import_run_id,
            "clauses": [
                {
                    "clause_id": clause.clause_id,
                    "title": clause.title,
                    "text": clause.text,
                    "control_code": clause.control_code,
                    "rule_parameters": dict(clause.rule_parameters),
                    "classification": dict(clause.classification),
                    "position": clause.position,
                }
                for clause in clauses
            ],
        }

    @staticmethod
    def _reanalyze_policy_group(
        session: Session, anchor: PolicyFileImport
    ) -> None:
        records = list(
            session.scalars(
                select(PolicyFileImport).where(
                    PolicyFileImport.actor_id == anchor.actor_id,
                    PolicyFileImport.policy_set_id == anchor.policy_set_id,
                    PolicyFileImport.policy_set_version == anchor.policy_set_version,
                    PolicyFileImport.status != "PUBLISHED",
                )
            )
        )
        record_by_id = {record.policy_import_id: record for record in records}
        clauses = list(
            session.scalars(
                select(PolicyFileImportClause)
                .where(
                    PolicyFileImportClause.policy_import_id.in_(record_by_id)
                )
                .order_by(
                    PolicyFileImportClause.policy_import_id,
                    PolicyFileImportClause.position,
                )
            )
        )
        for clause in clauses:
            analysis = dict(clause.classification or {})
            base_status = analysis.get("base_status") or analysis.get("status") or (
                "AUTO_ACCEPTED" if clause.control_code else "ADMIN_REVIEW"
            )
            reasons = [
                reason
                for reason in analysis.get("reason_codes", [])
                if reason not in _CONFLICT_REASON_CODES
            ]
            clause.classification = {
                **analysis,
                "status": base_status,
                "base_status": base_status,
                "reason_codes": reasons,
                "conflicts_with": [],
            }

        def mark_conflict(
            conflicting: list[PolicyFileImportClause], reason_code: str
        ) -> None:
            identities = {
                clause.policy_file_import_clause_id: (
                    f"{record_by_id[clause.policy_import_id].original_filename}:"
                    f"{clause.clause_id}"
                )
                for clause in conflicting
            }
            for clause in conflicting:
                analysis = dict(clause.classification)
                reasons = list(analysis.get("reason_codes", []))
                if reason_code not in reasons:
                    reasons.append(reason_code)
                analysis.update(
                    {
                        "status": "ADMIN_REVIEW",
                        "reason_codes": reasons,
                        "conflicts_with": [
                            identity
                            for clause_id, identity in identities.items()
                            if clause_id != clause.policy_file_import_clause_id
                        ],
                    }
                )
                clause.classification = analysis

        clauses_by_id: dict[str, list[PolicyFileImportClause]] = {}
        for clause in clauses:
            clauses_by_id.setdefault(clause.clause_id, []).append(clause)
        for duplicates in clauses_by_id.values():
            if len(duplicates) > 1:
                mark_conflict(duplicates, "DUPLICATE_CLAUSE_ID")

        rules: dict[tuple[str, str], list[PolicyFileImportClause]] = {}
        for clause in clauses:
            rule_key = clause.rule_parameters.get("rule_key")
            if clause.control_code and isinstance(rule_key, str) and rule_key:
                rules.setdefault((clause.control_code, rule_key), []).append(clause)
        for candidates in rules.values():
            signatures = {
                json.dumps(
                    {
                        key: value
                        for key, value in clause.rule_parameters.items()
                        if key != "rule_key"
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                for clause in candidates
            }
            if len(candidates) > 1 and len(signatures) > 1:
                mark_conflict(candidates, "CONFLICTING_RULE")

        clauses_by_record: dict[str, list[PolicyFileImportClause]] = {
            record.policy_import_id: [] for record in records
        }
        for clause in clauses:
            clauses_by_record[clause.policy_import_id].append(clause)
        for record in records:
            if record.status in {"PUBLISHING", "PUBLISHED"}:
                continue
            record_clauses = clauses_by_record[record.policy_import_id]
            record.status = (
                "READY_TO_PUBLISH"
                if record_clauses
                and all(
                    clause.control_code
                    and clause.classification.get("status") == "AUTO_ACCEPTED"
                    for clause in record_clauses
                )
                else "REVIEW_REQUIRED"
            )

    @staticmethod
    def _serialize_summary(
        record: PolicyFileImport, *, clause_count: int
    ) -> dict[str, Any]:
        return {
            "policy_import_id": record.policy_import_id,
            "status": record.status,
            "revision": record.revision,
            "original_filename": record.original_filename,
            "media_type": record.media_type,
            "size_bytes": record.size_bytes,
            "policy_set_id": record.policy_set_id,
            "policy_set_version": record.policy_set_version,
            "policy_id": record.policy_id,
            "document_id": record.document_id,
            "document_version": record.document_version,
            "title": record.title,
            "categories": list(record.categories),
            "regions": list(record.regions),
            "policy_index_version": record.policy_index_version,
            "clause_count": clause_count,
            "created_at": _utc_isoformat(record.created_at),
            "updated_at": _utc_isoformat(record.updated_at),
        }

    def _publication_group(
        self,
        session: Session,
        record: PolicyFileImport,
        *,
        lock: bool = False,
    ) -> list[PolicyFileImport]:
        statement = (
            select(PolicyFileImport)
            .where(
                PolicyFileImport.actor_id == self._actor_id,
                PolicyFileImport.policy_set_id == record.policy_set_id,
                PolicyFileImport.policy_set_version == record.policy_set_version,
                PolicyFileImport.status != "PUBLISHED",
            )
            .order_by(PolicyFileImport.document_id, PolicyFileImport.policy_import_id)
        )
        if lock:
            statement = statement.with_for_update()
        records = list(session.scalars(statement))
        return records or [record]

    def _build_manifest(
        self, session: Session, records: list[PolicyFileImport]
    ) -> LoadedPolicyManifest:
        if not records:
            raise ConflictError("policy_import_not_ready", "Policy set has no files.")
        documents: list[LoadedDocument] = []
        seen_document_ids: set[str] = set()
        seen_clause_ids: set[str] = set()
        for record in records:
            if record.document_id in seen_document_ids:
                raise ConflictError(
                    "duplicate_policy_document_id",
                    "Document IDs must be unique within a policy set.",
                    document_id=record.document_id,
                )
            seen_document_ids.add(record.document_id)
            draft_clauses = list(
                session.scalars(
                    select(PolicyFileImportClause)
                    .where(
                        PolicyFileImportClause.policy_import_id
                        == record.policy_import_id
                    )
                    .order_by(PolicyFileImportClause.position)
                )
            )
            if not draft_clauses or any(
                not clause.control_code
                or clause.classification.get("status") != "AUTO_ACCEPTED"
                for clause in draft_clauses
            ):
                raise ConflictError(
                    "policy_import_not_ready",
                    "Every policy clause must have a control code before publication.",
                    filename=record.original_filename,
                )
            clauses: list[LoadedClause] = []
            for clause in draft_clauses:
                if clause.clause_id in seen_clause_ids:
                    raise ConflictError(
                        "duplicate_policy_clause_id",
                        "Clause IDs must be unique across every file in a policy set.",
                        clause_id=clause.clause_id,
                    )
                seen_clause_ids.add(clause.clause_id)
                clauses.append(
                    LoadedClause(
                        clause_id=clause.clause_id,
                        title=clause.title,
                        text=clause.text,
                        content_sha256=_sha256_text(clause.text),
                        control_code=str(clause.control_code).upper(),
                        rule_parameters=dict(clause.rule_parameters),
                    )
                )
            extension = _SUPPORTED_MEDIA[record.media_type]
            documents.append(
                LoadedDocument(
                    policy_id=record.policy_id,
                    document_id=record.document_id,
                    document_version=record.document_version,
                    title=record.title,
                    relative_path=f"uploads/{record.document_id}/source{extension}",
                    effective_from=record.effective_from,
                    effective_to=record.effective_to,
                    categories=list(record.categories),
                    regions=list(record.regions),
                    content_sha256=record.source_sha256,
                    clauses=clauses,
                )
            )
        first = records[0]
        canonical = {
            "schema_version": "1.0.0",
            "policy_set_id": first.policy_set_id,
            "policy_set_version": first.policy_set_version,
            "documents": [document.model_dump(mode="json") for document in documents],
        }
        return LoadedPolicyManifest(
            schema_version="1.0.0",
            policy_set_id=first.policy_set_id,
            policy_set_version=first.policy_set_version,
            content_sha256=_sha256_text(
                json.dumps(canonical, sort_keys=True, separators=(",", ":"))
            ),
            source_path=f"upload-set:{first.policy_set_id}:{first.policy_set_version}",
            documents=documents,
        )

    def _existing_idempotent(
        self,
        session: Session,
        operation: str,
        key: str,
        request_sha256: str,
    ) -> dict[str, Any] | None:
        record = session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.actor_id == self._actor_id,
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
        operation: str,
        key: str,
        request_sha256: str,
        response: dict[str, Any],
        *,
        status_code: int,
    ) -> None:
        session.add(
            IdempotencyRecord(
                idempotency_id=_id("idem"),
                actor_id=self._actor_id,
                operation=operation,
                idempotency_key=key,
                request_sha256=request_sha256,
                response_status=status_code,
                response_payload=response,
            )
        )


def _draft_clauses(
    text: str,
    *,
    fallback_title: str,
    fallback_id_prefix: str = "",
    document_hint: str | None = None,
) -> list[dict[str, Any]]:
    fallback_clause_id = (
        f"DRAFT-{fallback_id_prefix}-001" if fallback_id_prefix else "DRAFT-001"
    )
    matches = list(_CLAUSE_HEADING.finditer(text))
    if not matches:
        analysis = _analyze_clause(
            fallback_title, text, document_hint or fallback_title
        )
        return [
            {
                "clause_id": fallback_clause_id,
                "title": fallback_title,
                "text": text.strip(),
                **analysis,
            }
        ]
    clauses: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            title = match.group(2).strip()
            analysis = _analyze_clause(
                title, body, document_hint or fallback_title
            )
            clauses.append(
                {
                    "clause_id": match.group(1).strip(),
                    "title": title,
                    "text": body,
                    **analysis,
                }
            )
    fallback_analysis = _analyze_clause(
        fallback_title, text, document_hint or fallback_title
    )
    return clauses or [
        {
            "clause_id": fallback_clause_id,
            "title": fallback_title,
            "text": text.strip(),
            **fallback_analysis,
        }
    ]


def _document_control_code(document_title: str) -> str | None:
    document = document_title.casefold().replace("-", "_").replace(" ", "_")
    if any(
        marker in document
        for marker in (
            "environmental_compliance",
            "electronics_compliance",
            "electronics_rohs",
            "环境合规",
            "rohs_证明",
        )
    ):
        return "ROHS_COMPLIANCE"
    if any(
        marker in document
        for marker in (
            "supplier_due_diligence",
            "supplier_qualification",
            "approved_supplier",
            "供应商准入",
            "供应商资质",
        )
    ):
        return "APPROVED_SUPPLIER"
    if any(
        marker in document
        for marker in (
            "spend_approval",
            "procurement_approval",
            "amount_approval",
            "金额审批",
            "采购审批",
        )
    ):
        return "AMOUNT_APPROVAL"
    return None


def _control_code_scores(title: str, text: str) -> dict[str, int]:
    title_value = title.casefold()
    value = f"{title}\n{text}".casefold()
    informational_markers = (
        "报价版本冻结", "审计记录", "审计链", "人工覆盖", "证据留存",
        "紧急采购认定", "事后复核", "例外关闭", "版本变更",
        "记录修改前值", "业务影响",
    )
    if any(marker in title_value for marker in informational_markers):
        return {"INFORMATIONAL": 2}
    keyword_groups = (
        (
            "ROHS_COMPLIANCE",
            ("rohs", "环境合规", "环境证明", "符合性声明", "物料字段变更"),
        ),
        (
            "APPROVED_SUPPLIER",
            (
                "供应商准入", "供应商身份", "供应商编号", "收款账户", "主数据",
                "临时供应商", "制裁筛查", "准入例外", "未完成常规准入",
                "approved supplier", "supplier qualification", "supplier due diligence",
                "supplier identity", "vendor master",
            ),
        ),
        (
            "AMOUNT_APPROVAL",
            (
                "金额审批", "审批金额", "总成本", "金额门槛", "采购金额", "金额字段变更",
                "单价", "运费", "税费", "汇率", "币种", "预算",
                "amount approval", "total cost", "approval threshold", "exchange rate", "budget",
            ),
        ),
    )
    return {
        code: sum(1 for keyword in keywords if keyword in value)
        for code, keywords in keyword_groups
    }


def _infer_control_code(title: str, text: str, document_title: str) -> str | None:
    """Classify supported controls conservatively and leave real ambiguity visible."""
    document_code = _document_control_code(document_title)
    if document_code:
        return document_code
    value = f"{title}\n{text}".casefold()
    scores = _control_code_scores(title, text)
    best_code, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score > 0 and list(scores.values()).count(best_score) == 1:
        return best_code
    if "金额审批" in value:
        return "AMOUNT_APPROVAL"

    if any(
        marker in value
        for marker in (
            "报价版本冻结", "审计记录", "审计链", "人工覆盖", "证据留存",
            "紧急采购认定", "事后复核", "例外关闭", "版本变更",
            "记录修改前值", "业务影响",
        )
    ):
        return "INFORMATIONAL"
    return None


def _analyze_clause(title: str, text: str, document_title: str) -> dict[str, Any]:
    document_code = _document_control_code(document_title)
    unsupported_markers = {
        "CYBERSECURITY_ASSESSMENT": (
            "网络安全", "渗透测试", "漏洞扫描", "cybersecurity", "penetration test",
        ),
        "SUSTAINABILITY_SCORING": (
            "碳排放评分", "esg 评分", "可持续性评分", "carbon scoring",
        ),
        "LABOR_PRACTICE_AUDIT": (
            "劳工审计", "强迫劳动审查", "labor practice audit",
        ),
    }
    value = f"{title}\n{text}".casefold()
    for capability, markers in unsupported_markers.items():
        if any(marker in value for marker in markers):
            return {
                "control_code": None,
                "rule_parameters": {},
                "classification": {
                    "status": "UNSUPPORTED",
                    "base_status": "UNSUPPORTED",
                    "method": "CAPABILITY_REGISTRY",
                    "reason_codes": ["UNSUPPORTED_CAPABILITY"],
                    "unsupported_capability": capability,
                    "conflicts_with": [],
                },
            }

    control_code = _infer_control_code(title, text, document_title)
    if control_code is None:
        scores = _control_code_scores(title, text)
        best_score = max(scores.values())
        reasons = [
            "AMBIGUOUS_CONTROL"
            if best_score > 0 and list(scores.values()).count(best_score) > 1
            else "UNRECOGNIZED_CONTROL"
        ]
        return {
            "control_code": None,
            "rule_parameters": {},
            "classification": {
                "status": "ADMIN_REVIEW",
                "base_status": "ADMIN_REVIEW",
                "method": "CONTENT_RULE",
                "reason_codes": reasons,
                "conflicts_with": [],
            },
        }

    parameters: dict[str, Any] = {}
    reasons: list[str] = []
    title_value = title.casefold()
    if control_code == "AMOUNT_APPROVAL" and any(
        marker in title_value for marker in ("门槛", "阈值", "threshold")
    ):
        amount = _AMOUNT_VALUE.search(text)
        if amount is None:
            reasons.append("MISSING_REQUIRED_PARAMETER")
        else:
            parameters = {
                "rule_key": "approval_threshold:" + re.sub(
                    r"\s+", "_", title.strip().casefold()
                ),
                "currency": amount.group(1).upper(),
                "threshold": amount.group(2).replace(",", ""),
                "operator": ">=",
            }

    status = "ADMIN_REVIEW" if reasons else "AUTO_ACCEPTED"
    return {
        "control_code": control_code,
        "rule_parameters": parameters,
        "classification": {
            "status": status,
            "base_status": status,
            "method": "DOCUMENT_PROFILE" if document_code else "CONTENT_RULE",
            "reason_codes": reasons,
            "conflicts_with": [],
        },
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_isoformat(value: datetime) -> str:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
