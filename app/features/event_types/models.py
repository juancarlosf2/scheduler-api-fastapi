"""Event Types SQLAlchemy tables."""

from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.model import Base, new_id, utc_now

if TYPE_CHECKING:
    from app.features.availability.models import Schedule
    from app.features.hosts.models import Host


class EventType(Base):
    __tablename__ = "event_types"
    __table_args__ = (
        UniqueConstraint("host_id", "slug", name="event_host_slug_unique"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False
    )
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey("schedules.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(40), nullable=False, default="#2563eb")
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    location_type: Mapped[str] = mapped_column(
        String(30), nullable=False, default="none"
    )
    location_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    booking_window_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=60
    )
    minimum_notice_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1440
    )
    slot_interval_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30
    )
    confirmation_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    host: Mapped[Host] = relationship(back_populates="event_types")
    schedule: Mapped[Schedule] = relationship()
    invitee_fields: Mapped[list[InviteeField]] = relationship(
        cascade="all, delete-orphan"
    )


class InviteeField(Base):
    __tablename__ = "invitee_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type_id: Mapped[str] = mapped_column(
        ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    field_type: Mapped[str] = mapped_column(String(30), nullable=False, default="text")
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
