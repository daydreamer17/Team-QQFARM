"""create policy file import schema

Revision ID: e2a4c6d8f0b1
Revises: c83a72d80b1f
Create Date: 2026-09-17 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e2a4c6d8f0b1"
down_revision: Union[str, None] = "c83a72d80b1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "policy_file_imports",
        sa.Column("policy_import_id", sa.String(64), primary_key=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False, unique=True),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.Column("extraction_metadata", JSON_VALUE, nullable=False),
        sa.Column("policy_set_id", sa.String(128), nullable=False),
        sa.Column("policy_set_version", sa.String(128), nullable=False),
        sa.Column("policy_id", sa.String(128), nullable=False),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True)),
        sa.Column("categories", JSON_VALUE, nullable=False),
        sa.Column("regions", JSON_VALUE, nullable=False),
        sa.Column("policy_index_version", sa.String(128)),
        sa.Column("published_import_run_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_policy_file_imports_actor_id", "policy_file_imports", ["actor_id"]
    )
    op.create_table(
        "policy_file_import_clauses",
        sa.Column("policy_file_import_clause_id", sa.String(64), primary_key=True),
        sa.Column(
            "policy_import_id",
            sa.String(64),
            sa.ForeignKey("policy_file_imports.policy_import_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("clause_id", sa.String(128), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("control_code", sa.String(128)),
        sa.Column("rule_parameters", JSON_VALUE, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("policy_import_id", "clause_id"),
        sa.UniqueConstraint("policy_import_id", "position"),
    )
    op.create_index(
        "ix_policy_file_import_clauses_policy_import_id",
        "policy_file_import_clauses",
        ["policy_import_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_policy_file_import_clauses_policy_import_id",
        table_name="policy_file_import_clauses",
    )
    op.drop_table("policy_file_import_clauses")
    op.drop_index("ix_policy_file_imports_actor_id", table_name="policy_file_imports")
    op.drop_table("policy_file_imports")
