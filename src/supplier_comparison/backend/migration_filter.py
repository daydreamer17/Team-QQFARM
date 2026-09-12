from __future__ import annotations


LANGGRAPH_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)


def include_alembic_object(object_, name, type_, reflected, compare_to) -> bool:
    del compare_to
    if not reflected:
        return True
    table_name = name if type_ == "table" else getattr(getattr(object_, "table", None), "name", None)
    return table_name not in LANGGRAPH_TABLES
