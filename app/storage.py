"""Database initialization. SQLite works locally; PostgreSQL URLs use psycopg 3."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def _driver_url(database_url: str) -> str:
    """Accept PostgreSQL URLs copied from providers with the installed driver."""
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://"):]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://"):]
    return database_url


def make_engine(url: str | None = None) -> Engine:
    database_url = _driver_url(url or os.getenv("DATABASE_URL", "sqlite:///./scheduler.db"))
    sqlite = database_url.startswith("sqlite:")
    engine = create_engine(
        database_url,
        connect_args={"check_same_thread": False} if sqlite else {},
        pool_pre_ping=not sqlite,
    )
    if sqlite:
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(dbapi_connection, connection_record) -> None:  # noqa: ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def create_schema(engine: Engine) -> None:
    # Import companion models before SQLAlchemy creates the metadata tables.
    from . import workspace  # noqa: F401
    from . import notifications  # noqa: F401

    Base.metadata.create_all(engine)
