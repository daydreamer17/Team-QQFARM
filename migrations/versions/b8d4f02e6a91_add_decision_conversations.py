"""add asynchronous decision conversations and replayable events

Revision ID: b8d4f02e6a91
Revises: a7c3e91d2f84
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "b8d4f02e6a91"
down_revision = "a7c3e91d2f84"
branch_labels = None
depends_on = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "decision_conversations",
        sa.Column("conversation_id", sa.String(length=64), primary_key=True),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("base_task_revision", sa.Integer(), nullable=False),
        sa.Column("base_result_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_decision_conversations_task_id", "decision_conversations", ["task_id"])
    op.create_index("ix_decision_conversations_actor_id", "decision_conversations", ["actor_id"])
    op.create_index("ix_decision_conversations_base_result_id", "decision_conversations", ["base_result_id"])

    op.create_table(
        "decision_messages",
        sa.Column("message_id", sa.String(length=64), primary_key=True),
        sa.Column("conversation_id", sa.String(length=64), sa.ForeignKey("decision_conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(length=64), sa.ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("reference_ids", JSON_VALUE),
        sa.Column("proposed_changes", JSON_VALUE),
        sa.Column("decision_intent_id", sa.String(length=64), sa.ForeignKey("decision_intents.decision_intent_id", ondelete="SET NULL")),
        sa.Column("reply_to_message_id", sa.String(length=64)),
        sa.Column("provider", sa.String(length=64)),
        sa.Column("model_id", sa.String(length=255)),
        sa.Column("prompt_version", sa.String(length=64)),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "sequence"),
    )
    op.create_index("ix_decision_messages_conversation_id", "decision_messages", ["conversation_id"])
    op.create_index("ix_decision_messages_task_id", "decision_messages", ["task_id"])
    op.create_index("ix_decision_messages_decision_intent_id", "decision_messages", ["decision_intent_id"])

    op.create_table(
        "decision_conversation_events",
        sa.Column("event_id", sa.String(length=64), primary_key=True),
        sa.Column("conversation_id", sa.String(length=64), sa.ForeignKey("decision_conversations.conversation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("conversation_id", "sequence"),
    )
    op.create_index("ix_decision_conversation_events_conversation_id", "decision_conversation_events", ["conversation_id"])

    op.add_column("jobs", sa.Column("conversation_id", sa.String(length=64)))
    op.add_column("jobs", sa.Column("conversation_message_id", sa.String(length=64)))
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_jobs_conversation", "jobs", "decision_conversations",
            ["conversation_id"], ["conversation_id"], ondelete="CASCADE",
        )
        op.create_foreign_key(
            "fk_jobs_conversation_message", "jobs", "decision_messages",
            ["conversation_message_id"], ["message_id"], ondelete="CASCADE",
        )
    op.create_index("ix_jobs_conversation_id", "jobs", ["conversation_id"])
    op.create_index("ix_jobs_conversation_message_id", "jobs", ["conversation_message_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_conversation_message_id", table_name="jobs")
    op.drop_index("ix_jobs_conversation_id", table_name="jobs")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_jobs_conversation_message", "jobs", type_="foreignkey")
        op.drop_constraint("fk_jobs_conversation", "jobs", type_="foreignkey")
    op.drop_column("jobs", "conversation_message_id")
    op.drop_column("jobs", "conversation_id")
    op.drop_index("ix_decision_conversation_events_conversation_id", table_name="decision_conversation_events")
    op.drop_table("decision_conversation_events")
    op.drop_index("ix_decision_messages_decision_intent_id", table_name="decision_messages")
    op.drop_index("ix_decision_messages_task_id", table_name="decision_messages")
    op.drop_index("ix_decision_messages_conversation_id", table_name="decision_messages")
    op.drop_table("decision_messages")
    op.drop_index("ix_decision_conversations_base_result_id", table_name="decision_conversations")
    op.drop_index("ix_decision_conversations_actor_id", table_name="decision_conversations")
    op.drop_index("ix_decision_conversations_task_id", table_name="decision_conversations")
    op.drop_table("decision_conversations")
