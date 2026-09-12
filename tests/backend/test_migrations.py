from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


BUSINESS_TABLES = {
    "tasks",
    "task_revisions",
    "requirements",
    "quotes",
    "documents",
    "workflow_artifacts",
    "graph_runs",
    "document_executions",
    "issues",
    "jobs",
    "idempotency_records",
}


def test_initial_migration_supports_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    database_path = tmp_path / "migration.sqlite3"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{database_path.as_posix()}")

    command.upgrade(config, "head")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    assert BUSINESS_TABLES.issubset(set(inspect(engine).get_table_names()))

    command.downgrade(config, "base")
    assert BUSINESS_TABLES.isdisjoint(set(inspect(engine).get_table_names()))

    command.upgrade(config, "head")
    assert BUSINESS_TABLES.issubset(set(inspect(engine).get_table_names()))
