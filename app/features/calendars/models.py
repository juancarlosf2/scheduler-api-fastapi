"""Calendars SQLAlchemy tables."""

from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.model import Base, new_id, utc_now

if TYPE_CHECKING:
    from app.features.hosts.models import Host


class CalendarConnection(Base):
    """One provider account per host; credentials remain with the provider."""

    __tablename__ = "calendar_connections"
    __table_args__ = (
        UniqueConstraint("host_id", "provider", name="calendar_host_provider_unique"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="google")
    toolkit_slug: Mapped[str] = mapped_column(
        String(80), nullable=False, default="googlecalendar"
    )
    connected_account_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_account_email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="initiated")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    host: Mapped[Host] = relationship(back_populates="calendar_connections")
    calendars: Mapped[list[ExternalCalendar]] = relationship(
        back_populates="connection", cascade="all, delete-orphan"
    )


class ExternalCalendar(Base):
    __tablename__ = "external_calendars"
    __table_args__ = (
        UniqueConstraint(
            "calendar_connection_id",
            "provider_calendar_id",
            name="calendar_provider_id_unique",
        ),
        Index(
            "calendar_one_destination_unique",
            "calendar_connection_id",
            unique=True,
            sqlite_where=text("add_events = 1"),
            postgresql_where=text("add_events = true"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    calendar_connection_id: Mapped[str] = mapped_column(
        ForeignKey("calendar_connections.id", ondelete="CASCADE"), nullable=False
    )
    provider_calendar_id: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    time_zone: Mapped[str | None] = mapped_column(String(100))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    check_conflicts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    add_events: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    connection: Mapped[CalendarConnection] = relationship(back_populates="calendars")
