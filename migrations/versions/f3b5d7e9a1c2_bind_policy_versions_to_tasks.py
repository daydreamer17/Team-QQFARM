"""Bind published policy versions to procurement tasks.

Revision ID: f3b5d7e9a1c2
Revises: e2a4c6d8f0b1
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f3b5d7e9a1c2"
down_revision: str | None = "e2a4c6d8f0b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("policy_set_version", sa.String(128)))
    op.add_column("tasks", sa.Column("policy_index_version", sa.String(128)))
    op.add_column("tasks", sa.Column("policy_category", sa.String(128)))
    op.add_column("tasks", sa.Column("policy_region", sa.String(64)))


def downgrade() -> None:
    op.drop_column("tasks", "policy_region")
    op.drop_column("tasks", "policy_category")
    op.drop_column("tasks", "policy_index_version")
    op.drop_column("tasks", "policy_set_version")
