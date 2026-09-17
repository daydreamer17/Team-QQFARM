from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from supplier_comparison.backend.models import Base, JSON_VALUE, utc_now


class PolicySet(Base):
    __tablename__ = "policy_sets"

    policy_set_record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_set_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_set_version: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("policy_set_id", "policy_set_version"),)


class PolicyDocument(Base):
    __tablename__ = "policy_documents"

    policy_document_record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_set_record_id: Mapped[str] = mapped_column(
        ForeignKey("policy_sets.policy_set_record_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False)
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    categories: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    regions: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("policy_set_record_id", "document_id"),)


class PolicyClause(Base):
    __tablename__ = "policy_clauses"

    policy_clause_record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_set_record_id: Mapped[str] = mapped_column(
        ForeignKey("policy_sets.policy_set_record_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_document_record_id: Mapped[str] = mapped_column(
        ForeignKey("policy_documents.policy_document_record_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clause_id: Mapped[str] = mapped_column(String(128), nullable=False)
    section: Mapped[str] = mapped_column(String(512), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    control_code: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    rule_parameters: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("policy_set_record_id", "clause_id"),)


class PolicyIndex(Base):
    __tablename__ = "policy_indexes"

    policy_index_version: Mapped[str] = mapped_column(String(128), primary_key=True)
    policy_set_record_id: Mapped[str] = mapped_column(
        ForeignKey("policy_sets.policy_set_record_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    collection_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    preprocessing_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    error_code: Mapped[str | None] = mapped_column(String(128))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint(
            "policy_set_record_id",
            "collection_sha256",
            "provider",
            "embedding_model",
            "embedding_dimension",
            "preprocessing_version",
            name="uq_policy_index_build_identity",
        ),
    )


class PolicyClauseEmbedding(Base):
    __tablename__ = "policy_clause_embeddings"

    policy_clause_embedding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_index_version: Mapped[str] = mapped_column(
        ForeignKey("policy_indexes.policy_index_version", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    policy_clause_record_id: Mapped[str] = mapped_column(
        ForeignKey("policy_clauses.policy_clause_record_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (UniqueConstraint("policy_index_version", "policy_clause_record_id"),)


class PolicyImportRun(Base):
    __tablename__ = "policy_import_runs"

    import_run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_set_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_set_version: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_index_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    replayed: Mapped[bool] = mapped_column(nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RetrievalTrace(Base):
    __tablename__ = "retrieval_traces"

    retrieval_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    task_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_set_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_index_version: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    rerank_model: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    filters: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    required_control_codes: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    covered_control_codes: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    missing_control_codes: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    candidates: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    citations: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    latency_ms: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    attempts: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class PolicyFileImport(Base):
    __tablename__ = "policy_file_imports"

    policy_import_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    media_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_metadata: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    policy_set_id: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_set_version: Mapped[str] = mapped_column(String(128), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(128), nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False)
    document_version: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    categories: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    regions: Mapped[list] = mapped_column(JSON_VALUE, nullable=False)
    policy_index_version: Mapped[str | None] = mapped_column(String(128))
    published_import_run_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class PolicyFileImportClause(Base):
    __tablename__ = "policy_file_import_clauses"

    policy_file_import_clause_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_import_id: Mapped[str] = mapped_column(
        ForeignKey("policy_file_imports.policy_import_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clause_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    control_code: Mapped[str | None] = mapped_column(String(128))
    rule_parameters: Mapped[dict] = mapped_column(JSON_VALUE, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    __table_args__ = (
        UniqueConstraint("policy_import_id", "clause_id"),
        UniqueConstraint("policy_import_id", "position"),
    )
