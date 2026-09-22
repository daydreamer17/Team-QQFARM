"""add immutable task supplier-history bindings

Revision ID: d52f7b19c3a4
Revises: c4e8a91f2d73
"""

from alembic import op
import sqlalchemy as sa


revision = "d52f7b19c3a4"
down_revision = "c4e8a91f2d73"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_history_bindings",
        sa.Column("history_binding_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=64),
            sa.ForeignKey("tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_revision", sa.Integer(), nullable=False),
        sa.Column("binding_status", sa.String(length=32), nullable=False),
        sa.Column("dataset_id", sa.String(length=128), nullable=False),
        sa.Column("dataset_version", sa.String(length=128), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("rating_method_version", sa.String(length=128), nullable=False),
        sa.Column("identity_matcher_version", sa.String(length=128), nullable=False),
        sa.Column("alias_allowlist_sha256", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("as_of_date", sa.String(length=10), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "task_revision"),
    )
    op.create_index(
        "ix_task_history_bindings_task_id", "task_history_bindings", ["task_id"]
    )
    with op.batch_alter_table("graph_runs") as batch_op:
        batch_op.add_column(sa.Column("history_binding_id", sa.String(length=64)))
        batch_op.create_foreign_key(
            "fk_graph_runs_history_binding_id",
            "task_history_bindings",
            ["history_binding_id"],
            ["history_binding_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_graph_runs_history_binding_id", ["history_binding_id"])
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.add_column(sa.Column("history_binding_id", sa.String(length=64)))
        batch_op.create_foreign_key(
            "fk_jobs_history_binding_id",
            "task_history_bindings",
            ["history_binding_id"],
            ["history_binding_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_jobs_history_binding_id", ["history_binding_id"])


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_index("ix_jobs_history_binding_id")
        batch_op.drop_constraint("fk_jobs_history_binding_id", type_="foreignkey")
        batch_op.drop_column("history_binding_id")
    with op.batch_alter_table("graph_runs") as batch_op:
        batch_op.drop_index("ix_graph_runs_history_binding_id")
        batch_op.drop_constraint("fk_graph_runs_history_binding_id", type_="foreignkey")
        batch_op.drop_column("history_binding_id")
    op.drop_index("ix_task_history_bindings_task_id", table_name="task_history_bindings")
    op.drop_table("task_history_bindings")
