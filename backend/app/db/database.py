from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker


def create_database_engine(database_url: str) -> Engine:
    connect_args = {"check_same_thread": False, "timeout": 10} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def database_healthy(session_factory: sessionmaker[Session]) -> bool:
    with session_factory() as session:
        session.execute(text("SELECT 1"))
    return True


def get_session() -> Generator[Session, None, None]:
    """Dependency placeholder; application routes use request app state."""
    raise RuntimeError("Session dependency must be supplied by the application")
    yield  # pragma: no cover
