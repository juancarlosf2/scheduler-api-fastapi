"""Database initialization. SQLite works locally; PostgreSQL URLs use psycopg 3."""

from __future__ import annotations

import os

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.core.model import Base


def _driver_url(database_url: str) -> str:
    """Use the installed driver for standard PostgreSQL connection URLs."""
    if database_url.startswith("postgres://"):
        return "postgresql+psycopg://" + database_url[len("postgres://") :]
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
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
    from app.features.hosts import models as host_models  # noqa: F401
    from app.features.calendars import models as calendar_models  # noqa: F401
    from app.features.availability import models as availability_models  # noqa: F401
    from app.features.event_types import models as event_type_models  # noqa: F401
    from app.features.bookings import models as booking_models  # noqa: F401
    from app.features.contacts import models as contact_models  # noqa: F401
    from app.features.profiles import models as profile_models  # noqa: F401
    from app.features.onboarding import models as onboarding_models  # noqa: F401
    from app.features.workflows import models as workflow_models  # noqa: F401
    from app.features.notifications import models as notification_models  # noqa: F401

    Base.metadata.create_all(engine)
