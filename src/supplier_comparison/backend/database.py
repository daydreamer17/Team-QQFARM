from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(database_url: str) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(database_url, pool_pre_ping=True)
    return engine, sessionmaker(engine, expire_on_commit=False)


def readiness_probe(engine: Engine) -> Callable[[], bool]:
    def check() -> bool:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    return check
