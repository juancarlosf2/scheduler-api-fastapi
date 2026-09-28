"""Hosts SQLAlchemy tables."""

from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import Boolean, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.model import Base, new_id, utc_now

if TYPE_CHECKING:
    from app.features.availability.models import Schedule
    from app.features.calendars.models import CalendarConnection
    from app.features.event_types.models import EventType


class Host(Base):
    __tablename__ = "hosts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    workos_user_id: Mapped[str | None] = mapped_column(String(200), unique=True)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    api_key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    welcome_message: Mapped[str | None] = mapped_column(Text)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )

    schedule: Mapped[Schedule | None] = relationship(
        back_populates="host", cascade="all, delete-orphan", uselist=False
    )
    event_types: Mapped[list[EventType]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )
    calendar_connections: Mapped[list[CalendarConnection]] = relationship(
        back_populates="host", cascade="all, delete-orphan"
    )
