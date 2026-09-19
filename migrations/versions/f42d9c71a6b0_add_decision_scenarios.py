"""add persistent decision scenarios and profiles

Revision ID: f42d9c71a6b0
Revises: e91bc30d5a72
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f42d9c71a6b0"
down_revision = "e91bc30d5a72"
branch_labels = None
depends_on = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "decision_scenarios",
        sa.Column("decision_scenario_id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("base_task_revision", sa.Integer(), nullable=False),
        sa.Column("base_result_id", sa.String(length=64), nullable=False),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="READY"),
        sa.Column("changes", JSON_VALUE, nullable=False),
        sa.Column("baseline", JSON_VALUE, nullable=False),
        sa.Column("simulated", JSON_VALUE, nullable=False),
        sa.Column("delta", JSON_VALUE, nullable=False),
        sa.Column("applied_task_revision", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_scenarios_task_id", "decision_scenarios", ["task_id"])
    op.create_index("ix_decision_scenarios_actor_id", "decision_scenarios", ["actor_id"])
    op.create_index("ix_decision_scenarios_base_result_id", "decision_scenarios", ["base_result_id"])

    op.create_table(
        "decision_profiles",
        sa.Column("decision_profile_id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_revision", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "source_scenario_id",
            sa.String(length=64),
            sa.ForeignKey("decision_scenarios.decision_scenario_id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "profile_version"),
    )
    op.create_index("ix_decision_profiles_task_id", "decision_profiles", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_decision_profiles_task_id", table_name="decision_profiles")
    op.drop_table("decision_profiles")
    op.drop_index("ix_decision_scenarios_base_result_id", table_name="decision_scenarios")
    op.drop_index("ix_decision_scenarios_actor_id", table_name="decision_scenarios")
    op.drop_index("ix_decision_scenarios_task_id", table_name="decision_scenarios")
    op.drop_table("decision_scenarios")
