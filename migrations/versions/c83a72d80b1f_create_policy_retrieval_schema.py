"""create policy retrieval schema

Revision ID: c83a72d80b1f
Revises: b31f0f8c2a6e
Create Date: 2026-09-16 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql


revision: str = "c83a72d80b1f"
down_revision: Union[str, None] = "b31f0f8c2a6e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "policy_sets",
        sa.Column("policy_set_record_id", sa.String(64), primary_key=True),
        sa.Column("policy_set_id", sa.String(128), nullable=False),
        sa.Column("policy_set_version", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("manifest_path", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_set_id", "policy_set_version"),
    )
    op.create_table(
        "policy_documents",
        sa.Column("policy_document_record_id", sa.String(64), primary_key=True),
        sa.Column(
            "policy_set_record_id",
            sa.String(64),
            sa.ForeignKey("policy_sets.policy_set_record_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("categories", JSON_VALUE, nullable=False),
        sa.Column("regions", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_set_record_id", "document_id"),
    )
    op.create_index("ix_policy_documents_policy_set_record_id", "policy_documents", ["policy_set_record_id"])
    op.create_table(
        "policy_clauses",
        sa.Column("policy_clause_record_id", sa.String(64), primary_key=True),
        sa.Column(
            "policy_set_record_id",
            sa.String(64),
            sa.ForeignKey("policy_sets.policy_set_record_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_document_record_id",
            sa.String(64),
            sa.ForeignKey("policy_documents.policy_document_record_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("clause_id", sa.String(128), nullable=False),
        sa.Column("section", sa.String(512), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("control_code", sa.String(128), nullable=False),
        sa.Column("rule_parameters", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_set_record_id", "clause_id"),
    )
    op.create_index("ix_policy_clauses_policy_set_record_id", "policy_clauses", ["policy_set_record_id"])
    op.create_index("ix_policy_clauses_policy_document_record_id", "policy_clauses", ["policy_document_record_id"])
    op.create_index("ix_policy_clauses_control_code", "policy_clauses", ["control_code"])
    op.create_table(
        "policy_indexes",
        sa.Column("policy_index_version", sa.String(128), primary_key=True),
        sa.Column(
            "policy_set_record_id",
            sa.String(64),
            sa.ForeignKey("policy_sets.policy_set_record_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("collection_sha256", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("preprocessing_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "policy_set_record_id",
            "collection_sha256",
            "provider",
            "embedding_model",
            "embedding_dimension",
            "preprocessing_version",
            name="uq_policy_index_build_identity",
        ),
    )
    op.create_index("ix_policy_indexes_policy_set_record_id", "policy_indexes", ["policy_set_record_id"])
    op.create_table(
        "policy_clause_embeddings",
        sa.Column("policy_clause_embedding_id", sa.String(64), primary_key=True),
        sa.Column(
            "policy_index_version",
            sa.String(128),
            sa.ForeignKey("policy_indexes.policy_index_version", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "policy_clause_record_id",
            sa.String(64),
            sa.ForeignKey("policy_clauses.policy_clause_record_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_index_version", "policy_clause_record_id"),
    )
    op.create_index("ix_policy_clause_embeddings_policy_index_version", "policy_clause_embeddings", ["policy_index_version"])
    op.create_index("ix_policy_clause_embeddings_policy_clause_record_id", "policy_clause_embeddings", ["policy_clause_record_id"])
    op.create_table(
        "policy_import_runs",
        sa.Column("import_run_id", sa.String(64), primary_key=True),
        sa.Column("policy_set_id", sa.String(128), nullable=False),
        sa.Column("policy_set_version", sa.String(128), nullable=False),
        sa.Column("manifest_sha256", sa.String(64), nullable=False),
        sa.Column("policy_index_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("replayed", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "retrieval_traces",
        sa.Column("retrieval_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(64), nullable=False),
        sa.Column("task_revision", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.String(64), nullable=False),
        sa.Column("policy_set_version", sa.String(128), nullable=False),
        sa.Column("policy_index_version", sa.String(128), nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("rerank_model", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("filters", JSON_VALUE, nullable=False),
        sa.Column("required_control_codes", JSON_VALUE, nullable=False),
        sa.Column("covered_control_codes", JSON_VALUE, nullable=False),
        sa.Column("missing_control_codes", JSON_VALUE, nullable=False),
        sa.Column("candidates", JSON_VALUE, nullable=False),
        sa.Column("citations", JSON_VALUE, nullable=False),
        sa.Column("latency_ms", JSON_VALUE, nullable=False),
        sa.Column("attempts", JSON_VALUE, nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_retrieval_traces_task_id", "retrieval_traces", ["task_id"])
    op.create_index("ix_retrieval_traces_policy_index_version", "retrieval_traces", ["policy_index_version"])


def downgrade() -> None:
    op.drop_index("ix_retrieval_traces_policy_index_version", table_name="retrieval_traces")
    op.drop_index("ix_retrieval_traces_task_id", table_name="retrieval_traces")
    op.drop_table("retrieval_traces")
    op.drop_table("policy_import_runs")
    op.drop_index("ix_policy_clause_embeddings_policy_clause_record_id", table_name="policy_clause_embeddings")
    op.drop_index("ix_policy_clause_embeddings_policy_index_version", table_name="policy_clause_embeddings")
    op.drop_table("policy_clause_embeddings")
    op.drop_index("ix_policy_indexes_policy_set_record_id", table_name="policy_indexes")
    op.drop_table("policy_indexes")
    op.drop_index("ix_policy_clauses_control_code", table_name="policy_clauses")
    op.drop_index("ix_policy_clauses_policy_document_record_id", table_name="policy_clauses")
    op.drop_index("ix_policy_clauses_policy_set_record_id", table_name="policy_clauses")
    op.drop_table("policy_clauses")
    op.drop_index("ix_policy_documents_policy_set_record_id", table_name="policy_documents")
    op.drop_table("policy_documents")
    op.drop_table("policy_sets")
