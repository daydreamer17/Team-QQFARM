from supplier_comparison.backend.migration_filter import include_alembic_object


def test_langgraph_checkpoint_tables_are_outside_alembic_ownership() -> None:
    for table_name in (
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    ):
        assert include_alembic_object(None, table_name, "table", True, None) is False

    assert include_alembic_object(None, "tasks", "table", True, None) is True
    assert include_alembic_object(None, "tasks", "table", False, None) is True
