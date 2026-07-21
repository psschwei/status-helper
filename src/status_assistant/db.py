"""Database engine and session management.

SQLite via SQLModel. The engine is built lazily from settings so tests can point it at an
in-memory database. ``create_db_and_tables`` is called on app startup; there is no Alembic
yet because the schema is a disposable cache — delete the file and re-sync.
"""

from collections.abc import Iterator

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

# Import models so they are registered on SQLModel.metadata before create_all runs.
from status_assistant import models  # noqa: F401
from status_assistant.config import get_settings

_engine: Engine | None = None


def get_engine() -> Engine:
    """Return the process-wide engine, creating it from settings on first use."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(settings.database_url)
    return _engine


def create_db_and_tables() -> None:
    """Create all tables that don't yet exist. Safe to call repeatedly."""
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a database session."""
    with Session(get_engine()) as session:
        yield session
