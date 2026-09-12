from supplier_comparison.backend.checkpoints import checkpoint_connection_string


def test_sqlalchemy_psycopg_url_is_converted_for_langgraph() -> None:
    assert checkpoint_connection_string(
        "postgresql+psycopg://user:password@postgres:5432/database"
    ) == "postgresql://user:password@postgres:5432/database"


def test_non_postgres_checkpoint_url_is_rejected() -> None:
    try:
        checkpoint_connection_string("sqlite:///local.db")
    except ValueError as exc:
        assert str(exc) == "LangGraph PostgresSaver requires a PostgreSQL URL"
    else:
        raise AssertionError("SQLite checkpoint URL was accepted")
