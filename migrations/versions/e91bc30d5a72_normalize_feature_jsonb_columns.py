"""normalize feature JSON columns to JSONB

Revision ID: e91bc30d5a72
Revises: d8e91a2bc4f0
"""

from alembic import op


revision = "e91bc30d5a72"
down_revision = "d8e91a2bc4f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column in (
        ("requirement_drafts", "parsed_payload"),
        ("requirement_drafts", "candidates"),
        ("summary_reports", "facts"),
        ("summary_reports", "narrative"),
        ("task_revisions", "details"),
    ):
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE JSONB USING "{column}"::jsonb')


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for table, column in (
        ("requirement_drafts", "parsed_payload"),
        ("requirement_drafts", "candidates"),
        ("summary_reports", "facts"),
        ("summary_reports", "narrative"),
        ("task_revisions", "details"),
    ):
        op.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE JSON USING "{column}"::json')
