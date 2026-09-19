"""add persisted one-shot decision intents

Revision ID: a7c3e91d2f84
Revises: f42d9c71a6b0
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a7c3e91d2f84"
down_revision = "f42d9c71a6b0"
branch_labels = None
depends_on = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "decision_intents",
        sa.Column("decision_intent_id", sa.String(length=64), primary_key=True),
        sa.Column(
            "task_id",
            sa.String(length=64),
            sa.ForeignKey("tasks.task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("base_task_revision", sa.Integer(), nullable=False),
        sa.Column("base_result_id", sa.String(length=64), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="PROCESSING"),
        sa.Column("parsed_changes", JSON_VALUE),
        sa.Column("confirmation_text", sa.Text()),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model_id", sa.String(length=255)),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "decision_scenario_id",
            sa.String(length=64),
            sa.ForeignKey("decision_scenarios.decision_scenario_id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_intents_task_id", "decision_intents", ["task_id"])
    op.create_index("ix_decision_intents_actor_id", "decision_intents", ["actor_id"])
    op.create_index("ix_decision_intents_base_result_id", "decision_intents", ["base_result_id"])
    op.create_index(
        "ix_decision_intents_decision_scenario_id",
        "decision_intents",
        ["decision_scenario_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_intents_decision_scenario_id", table_name="decision_intents")
    op.drop_index("ix_decision_intents_base_result_id", table_name="decision_intents")
    op.drop_index("ix_decision_intents_actor_id", table_name="decision_intents")
    op.drop_index("ix_decision_intents_task_id", table_name="decision_intents")
    op.drop_table("decision_intents")
