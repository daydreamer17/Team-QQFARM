"""add user-facing task names

Revision ID: 0f4e8b6a9c21
Revises: f6a1b2c3d4e5
"""

from datetime import datetime
import json
import unicodedata

from alembic import op
import sqlalchemy as sa


revision = "0f4e8b6a9c21"
down_revision = "f6a1b2c3d4e5"
branch_labels = None
depends_on = None


def _normalized(value: str) -> tuple[str, str]:
    display = " ".join(unicodedata.normalize("NFKC", value).split())
    return display, display.casefold()


def upgrade() -> None:
    op.add_column("tasks", sa.Column("task_name", sa.String(length=128), nullable=True))
    op.add_column("tasks", sa.Column("task_name_key", sa.String(length=128), nullable=True))

    connection = op.get_bind()
    tasks = sa.table(
        "tasks",
        sa.column("task_id", sa.String()),
        sa.column("owner_id", sa.String()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("task_name", sa.String()),
        sa.column("task_name_key", sa.String()),
    )
    requirements = sa.table(
        "requirements",
        sa.column("task_id", sa.String()),
        sa.column("requirement_version", sa.Integer()),
        sa.column("payload", sa.JSON()),
    )
    used: dict[str, set[str]] = {}
    for row in connection.execute(sa.select(tasks)).mappings():
        payload_row = connection.execute(
            sa.select(requirements.c.payload)
            .where(requirements.c.task_id == row["task_id"])
            .order_by(requirements.c.requirement_version.desc())
            .limit(1)
        ).first()
        payload = payload_row[0] if payload_row is not None else {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        part_number = (payload or {}).get("manufacturer_part_number") or "采购任务"
        created_at = row["created_at"]
        created_date = (
            created_at.date().isoformat()
            if isinstance(created_at, datetime)
            else str(created_at or "")[:10]
        )
        base = f"{part_number} · {created_date}"
        display, key = _normalized(base)
        owner_keys = used.setdefault(row["owner_id"], set())
        suffix = 2
        while key in owner_keys:
            display, key = _normalized(f"{base} · {suffix:02d}")
            suffix += 1
        owner_keys.add(key)
        connection.execute(
            tasks.update()
            .where(tasks.c.task_id == row["task_id"])
            .values(task_name=display, task_name_key=key)
        )

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.alter_column("task_name", existing_type=sa.String(length=128), nullable=False)
        batch_op.alter_column("task_name_key", existing_type=sa.String(length=128), nullable=False)
        batch_op.create_unique_constraint(
            "uq_tasks_owner_task_name_key", ["owner_id", "task_name_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint("uq_tasks_owner_task_name_key", type_="unique")
        batch_op.drop_column("task_name_key")
        batch_op.drop_column("task_name")
