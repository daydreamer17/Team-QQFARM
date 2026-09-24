"""add policy clause classification analysis

Revision ID: f6a1b2c3d4e5
Revises: d52f7b19c3a4
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "f6a1b2c3d4e5"
down_revision = "d52f7b19c3a4"
branch_labels = None
depends_on = None
JSON_VALUE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "policy_file_import_clauses",
        sa.Column("classification", JSON_VALUE, nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE policy_file_import_clauses "
            "SET classification = '{\"status\":\"AUTO_ACCEPTED\","
            "\"base_status\":\"AUTO_ACCEPTED\",\"method\":\"LEGACY\","
            "\"reason_codes\":[],\"conflicts_with\":[]}' "
            "WHERE control_code IS NOT NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE policy_file_import_clauses "
            "SET classification = '{\"status\":\"ADMIN_REVIEW\","
            "\"base_status\":\"ADMIN_REVIEW\",\"method\":\"LEGACY\","
            "\"reason_codes\":[\"UNRECOGNIZED_CONTROL\"],"
            "\"conflicts_with\":[]}' WHERE classification IS NULL"
        )
    )
    with op.batch_alter_table("policy_file_import_clauses") as batch_op:
        batch_op.alter_column(
            "classification", existing_type=JSON_VALUE, nullable=False
        )


def downgrade() -> None:
    with op.batch_alter_table("policy_file_import_clauses") as batch_op:
        batch_op.drop_column("classification")
