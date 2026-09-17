"""add quote draft review flow

Revision ID: a4c7e2f91b03
Revises: f3b5d7e9a1c2
"""

from alembic import op
import sqlalchemy as sa


revision = "a4c7e2f91b03"
down_revision = "f3b5d7e9a1c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "quote_drafts",
        sa.Column("quote_draft_id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("base_task_revision", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="UPLOADED"),
        sa.Column("proposed_quote_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("proposed_document_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("supplier_id", sa.String(length=128), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False, unique=True),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("parsed_artifact_id", sa.String(length=64)),
        sa.Column("batch_artifact_id", sa.String(length=64)),
        sa.Column("review_artifact_id", sa.String(length=64)),
        sa.Column("calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_calls", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model_id", sa.String(length=255)),
        sa.Column("environment", sa.String(length=32)),
        sa.Column("prompt_version", sa.String(length=64)),
        sa.Column("dictionary_sha256", sa.String(length=64)),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_quote_drafts_task_id", "quote_drafts", ["task_id"])
    op.create_index("ix_quote_drafts_actor_id", "quote_drafts", ["actor_id"])
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column(
            "graph_run_id", existing_type=sa.String(length=64), nullable=True
        )
        batch_op.add_column(sa.Column("quote_draft_id", sa.String(length=64)))
        batch_op.create_foreign_key(
            "fk_jobs_quote_draft_id",
            "quote_drafts",
            ["quote_draft_id"],
            ["quote_draft_id"],
            ondelete="CASCADE",
        )
        batch_op.create_index("ix_jobs_quote_draft_id", ["quote_draft_id"])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM jobs WHERE quote_draft_id IS NOT NULL"))
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_quote_draft_id")
        batch_op.drop_constraint("fk_jobs_quote_draft_id", type_="foreignkey")
        batch_op.drop_column("quote_draft_id")
        batch_op.alter_column(
            "graph_run_id", existing_type=sa.String(length=64), nullable=False
        )
    op.drop_index("ix_quote_drafts_actor_id", table_name="quote_drafts")
    op.drop_index("ix_quote_drafts_task_id", table_name="quote_drafts")
    op.drop_table("quote_drafts")
