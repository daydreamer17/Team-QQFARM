"""enable pgvector extension

Revision ID: b31f0f8c2a6e
Revises: ad0606803e8d
Create Date: 2026-09-15 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b31f0f8c2a6e"
down_revision: Union[str, None] = "ad0606803e8d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    # The extension is shared database infrastructure. Later revisions may own
    # vector columns, so a schema downgrade must not remove it implicitly.
    pass
