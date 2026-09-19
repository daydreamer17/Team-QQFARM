"""complete frontend backend features

Revision ID: d8e91a2bc4f0
Revises: a4c7e2f91b03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "d8e91a2bc4f0"
down_revision = "a4c7e2f91b03"
branch_labels = None
depends_on = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("tasks", sa.Column("abandoned_at", sa.DateTime(timezone=True)))
    op.add_column("task_revisions", sa.Column("details", JSON_VALUE))
    op.add_column("requirements", sa.Column("source_artifact_id", sa.String(length=64)))

    op.create_table(
        "requirement_drafts",
        sa.Column("requirement_draft_id", sa.String(length=64), primary_key=True),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="UPLOADED"),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False, unique=True),
        sa.Column("parsed_payload", JSON_VALUE),
        sa.Column("candidates", JSON_VALUE),
        sa.Column("calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_calls", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model_id", sa.String(length=255)),
        sa.Column("environment", sa.String(length=32)),
        sa.Column("prompt_version", sa.String(length=64)),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("submitted_task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_requirement_drafts_actor_id", "requirement_drafts", ["actor_id"])
    op.create_index("ix_requirement_drafts_submitted_task_id", "requirement_drafts", ["submitted_task_id"])

    op.create_table(
        "summary_reports",
        sa.Column("summary_id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_revision", sa.Integer(), nullable=False),
        sa.Column("result_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("facts", JSON_VALUE, nullable=False),
        sa.Column("narrative", JSON_VALUE),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model_id", sa.String(length=255)),
        sa.Column("environment", sa.String(length=32)),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("calls_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_calls", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "input_sha256", "prompt_version", "model_id"),
    )
    op.create_index("ix_summary_reports_task_id", "summary_reports", ["task_id"])
    op.create_index("ix_summary_reports_result_id", "summary_reports", ["result_id"])

    op.create_table(
        "document_access_events",
        sa.Column("access_event_id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("document_id", sa.String(length=64), sa.ForeignKey("documents.document_id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("request_id", sa.String(length=128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_document_access_events_task_id", "document_access_events", ["task_id"])
    op.create_index("ix_document_access_events_document_id", "document_access_events", ["document_id"])

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column("task_id", existing_type=sa.String(length=64), nullable=True)
        batch_op.add_column(sa.Column("requirement_draft_id", sa.String(length=64)))
        batch_op.add_column(sa.Column("summary_id", sa.String(length=64)))
        batch_op.create_foreign_key("fk_jobs_requirement_draft_id", "requirement_drafts", ["requirement_draft_id"], ["requirement_draft_id"], ondelete="CASCADE")
        batch_op.create_foreign_key("fk_jobs_summary_id", "summary_reports", ["summary_id"], ["summary_id"], ondelete="CASCADE")
        batch_op.create_index("ix_jobs_requirement_draft_id", ["requirement_draft_id"])
        batch_op.create_index("ix_jobs_summary_id", ["summary_id"])


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM jobs WHERE requirement_draft_id IS NOT NULL OR summary_id IS NOT NULL"))
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_summary_id")
        batch_op.drop_index("ix_jobs_requirement_draft_id")
        batch_op.drop_constraint("fk_jobs_summary_id", type_="foreignkey")
        batch_op.drop_constraint("fk_jobs_requirement_draft_id", type_="foreignkey")
        batch_op.drop_column("summary_id")
        batch_op.drop_column("requirement_draft_id")
        batch_op.alter_column("task_id", existing_type=sa.String(length=64), nullable=False)
    op.drop_index("ix_document_access_events_document_id", table_name="document_access_events")
    op.drop_index("ix_document_access_events_task_id", table_name="document_access_events")
    op.drop_table("document_access_events")
    op.drop_index("ix_summary_reports_result_id", table_name="summary_reports")
    op.drop_index("ix_summary_reports_task_id", table_name="summary_reports")
    op.drop_table("summary_reports")
    op.drop_index("ix_requirement_drafts_submitted_task_id", table_name="requirement_drafts")
    op.drop_index("ix_requirement_drafts_actor_id", table_name="requirement_drafts")
    op.drop_table("requirement_drafts")
    op.drop_column("requirements", "source_artifact_id")
    op.drop_column("task_revisions", "details")
    op.drop_column("tasks", "abandoned_at")
