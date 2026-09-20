"""add quote replacement target

Revision ID: c4e8a91f2d73
Revises: b8d4f02e6a91
"""

from alembic import op
import sqlalchemy as sa


revision = "c4e8a91f2d73"
down_revision = "b8d4f02e6a91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("quote_drafts") as batch_op:
        batch_op.add_column(sa.Column("replacement_quote_id", sa.String(length=64)))
        batch_op.create_foreign_key(
            "fk_quote_drafts_replacement_quote_id",
            "quotes",
            ["replacement_quote_id"],
            ["quote_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_quote_drafts_replacement_quote_id", ["replacement_quote_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("quote_drafts") as batch_op:
        batch_op.drop_index("ix_quote_drafts_replacement_quote_id")
        batch_op.drop_constraint(
            "fk_quote_drafts_replacement_quote_id", type_="foreignkey"
        )
        batch_op.drop_column("replacement_quote_id")
