from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

import pdfplumber
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
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
_SUPPORTED_MEDIA = {"application/pdf": ".pdf", "text/plain": ".txt"}


class UploadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PolicyFileImportMetadata(UploadModel):
    policy_set_id: str = Field(min_length=1, max_length=128)
    policy_set_version: str = Field(min_length=1, max_length=128)
    policy_id: str = Field(min_length=1, max_length=128)
    document_id: str = Field(min_length=1, max_length=128)
    document_version: str = Field(min_length=1, max_length=64)
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
        if extension is None or Path(safe_name).suffix.lower() != extension:
            raise BackendError(
                "unsupported_policy_media_type",
                "Only PDF and UTF-8 TXT policy files are supported.",
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
                    record = PolicyFileImport(
                        policy_import_id=policy_import_id,
                        actor_id=self._actor_id,
                        revision=1,
                        status="REVIEW_REQUIRED",
                        original_filename=safe_name,
                        media_type=normalized_media,
                        size_bytes=size,
                        source_sha256=source_sha256,
                        storage_path=str(final_path),
                        extracted_text=extracted_text,
                        extraction_metadata=extraction_metadata,
                        **metadata.model_dump(),
                    )
                    session.add(record)
                    session.flush()
                    for position, clause in enumerate(
                        _draft_clauses(extracted_text, fallback_title=metadata.title), start=1
                    ):
                        session.add(
                            PolicyFileImportClause(
                                policy_file_import_clause_id=_id("pfic"),
                                policy_import_id=policy_import_id,
                                position=position,
                                **clause,
                            )
                        )
                    session.flush()
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
        category: str | None = None,
        region: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List published policy set/index bindings that tasks can safely freeze."""
        with self._sessions() as session:
            published_pairs = list(
                session.execute(
                    select(PolicySet, PolicyIndex)
                    .join(
                        PolicyIndex,
                        PolicyIndex.policy_set_record_id
                        == PolicySet.policy_set_record_id,
                    )
                    .where(
                        PolicySet.status == status,
                        PolicyIndex.status == status,
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
                        "status": status,
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
                        **clause.model_dump(),
                    )
                )
            record.revision += 1
            record.status = "READY_TO_PUBLISH"
            record.updated_at = utc_now()
            session.flush()
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

    def publish(
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
            manifest = self._build_manifest(session, record)
            record.status = "PUBLISHING"
            record.updated_at = utc_now()

        try:
            outcome = self._importer.import_loaded(manifest, publish=True)
        except PolicyImportError as exc:
            with self._sessions.begin() as session:
                record = self._owned_record(session, policy_import_id, lock=True)
                if record.revision == expected_revision:
                    record.status = "READY_TO_PUBLISH"
                    record.updated_at = utc_now()
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
            record = self._owned_record(session, policy_import_id, lock=True)
            self._require_revision(record, expected_revision)
            record.status = "PUBLISHED"
            record.revision += 1
            record.policy_index_version = outcome.index_version
            record.published_import_run_id = outcome.import_run_id
            record.updated_at = utc_now()
            session.flush()
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
        if media_type == "text/plain":
            try:
                text = path.read_bytes().decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise BackendError(
                    "policy_txt_not_utf8", "TXT policy files must be UTF-8 encoded."
                ) from exc
            if "\x00" in text:
                raise BackendError("policy_txt_binary", "TXT policy file contains binary data.")
            text = text.strip()
            if not text:
                raise BackendError("policy_document_no_text", "Policy document contains no text.")
            return text, {"parser": "utf-8-text/1.0", "page_count": None}

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
                    "position": clause.position,
                }
                for clause in clauses
            ],
        }

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

    def _build_manifest(
        self, session: Session, record: PolicyFileImport
    ) -> LoadedPolicyManifest:
        draft_clauses = list(
            session.scalars(
                select(PolicyFileImportClause)
                .where(PolicyFileImportClause.policy_import_id == record.policy_import_id)
                .order_by(PolicyFileImportClause.position)
            )
        )
        if not draft_clauses or any(not clause.control_code for clause in draft_clauses):
            raise ConflictError(
                "policy_import_not_ready",
                "Every policy clause must have a control code before publication.",
            )
        clauses = [
            LoadedClause(
                clause_id=clause.clause_id,
                title=clause.title,
                text=clause.text,
                content_sha256=_sha256_text(clause.text),
                control_code=str(clause.control_code).upper(),
                rule_parameters=dict(clause.rule_parameters),
            )
            for clause in draft_clauses
        ]
        extension = _SUPPORTED_MEDIA[record.media_type]
        document = LoadedDocument(
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
        canonical = {
            "schema_version": "1.0.0",
            "policy_set_id": record.policy_set_id,
            "policy_set_version": record.policy_set_version,
            "documents": [document.model_dump(mode="json")],
        }
        return LoadedPolicyManifest(
            schema_version="1.0.0",
            policy_set_id=record.policy_set_id,
            policy_set_version=record.policy_set_version,
            content_sha256=_sha256_text(
                json.dumps(canonical, sort_keys=True, separators=(",", ":"))
            ),
            source_path=f"upload:{record.policy_import_id}",
            documents=[document],
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


def _draft_clauses(text: str, *, fallback_title: str) -> list[dict[str, Any]]:
    matches = list(_CLAUSE_HEADING.finditer(text))
    if not matches:
        return [
            {
                "clause_id": "DRAFT-001",
                "title": fallback_title,
                "text": text.strip(),
                "control_code": None,
                "rule_parameters": {},
            }
        ]
    clauses: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            clauses.append(
                {
                    "clause_id": match.group(1).strip(),
                    "title": match.group(2).strip(),
                    "text": body,
                    "control_code": None,
                    "rule_parameters": {},
                }
            )
    return clauses or [
        {
            "clause_id": "DRAFT-001",
            "title": fallback_title,
            "text": text.strip(),
            "control_code": None,
            "rule_parameters": {},
        }
    ]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_isoformat(value: datetime) -> str:
    aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return aware.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"
