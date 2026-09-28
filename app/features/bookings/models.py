"""Bookings SQLAlchemy tables."""

from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.model import Base, new_id, utc_now

if TYPE_CHECKING:
    from app.features.event_types.models import EventType


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="booking_time_order"),
        Index("booking_host_time", "host_id", "start_at", "end_at", "status"),
        Index(
            "booking_event_start_scheduled",
            "event_type_id",
            "start_at",
            unique=True,
            sqlite_where=text("status = 'scheduled'"),
            postgresql_where=text("status = 'scheduled'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type_id: Mapped[str] = mapped_column(
        ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False
    )
    host_id: Mapped[str] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False
    )
    invitee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    invitee_email: Mapped[str] = mapped_column(String(320), nullable=False)
    invitee_phone: Mapped[str | None] = mapped_column(String(80))
    invitee_timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="scheduled")
    cancel_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    reschedule_token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    external_event_id: Mapped[str | None] = mapped_column(String(200))
    external_calendar_id: Mapped[str | None] = mapped_column(String(300))
    meeting_join_url: Mapped[str | None] = mapped_column(Text)
    external_sync_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="not_configured"
    )
    external_sync_operation: Mapped[str | None] = mapped_column(String(10))
    external_sync_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    event_type: Mapped[EventType] = relationship()
    answers: Mapped[list[BookingAnswer]] = relationship(cascade="all, delete-orphan")


class BookingAnswer(Base):
    __tablename__ = "booking_answers"
    __table_args__ = (
        UniqueConstraint("booking_id", "field_id", name="booking_field_unique"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    booking_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    field_id: Mapped[str] = mapped_column(
        ForeignKey("invitee_fields.id", ondelete="CASCADE"), nullable=False
    )
    value: Mapped[str] = mapped_column(Text, nullable=False)
