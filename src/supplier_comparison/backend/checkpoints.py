from __future__ import annotations

from langgraph.checkpoint.postgres import PostgresSaver


def checkpoint_connection_string(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg://"):
        return "postgresql://" + database_url.removeprefix("postgresql+psycopg://")
    if database_url.startswith("postgresql://") or database_url.startswith("postgres://"):
        return database_url
    raise ValueError("LangGraph PostgresSaver requires a PostgreSQL URL")


def setup_checkpoints(database_url: str) -> None:
    connection_string = checkpoint_connection_string(database_url)
    with PostgresSaver.from_conn_string(connection_string) as saver:
        saver.setup()
