"""Persistence for the independent scheduling API.

The schema is intentionally separate from JT Calendar Scheduler's Drizzle schema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    schedule: Mapped[Schedule | None] = relationship(back_populates="host", cascade="all, delete-orphan", uselist=False)
    event_types: Mapped[list[EventType]] = relationship(back_populates="host", cascade="all, delete-orphan")
    calendar_connections: Mapped[list[CalendarConnection]] = relationship(back_populates="host", cascade="all, delete-orphan")


class CalendarConnection(Base):
    """One provider account per host; credentials remain with the provider."""

    __tablename__ = "calendar_connections"
    __table_args__ = (UniqueConstraint("host_id", "provider", name="calendar_host_provider_unique"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="google")
    toolkit_slug: Mapped[str] = mapped_column(String(80), nullable=False, default="googlecalendar")
    connected_account_id: Mapped[str] = mapped_column(String(200), nullable=False)
    provider_account_email: Mapped[str | None] = mapped_column(String(320))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="initiated")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    host: Mapped[Host] = relationship(back_populates="calendar_connections")
    calendars: Mapped[list[ExternalCalendar]] = relationship(back_populates="connection", cascade="all, delete-orphan")


class ExternalCalendar(Base):
    __tablename__ = "external_calendars"
    __table_args__ = (
        UniqueConstraint("calendar_connection_id", "provider_calendar_id", name="calendar_provider_id_unique"),
        Index(
            "calendar_one_destination_unique", "calendar_connection_id", unique=True,
            sqlite_where=text("add_events = 1"), postgresql_where=text("add_events = true"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    calendar_connection_id: Mapped[str] = mapped_column(ForeignKey("calendar_connections.id", ondelete="CASCADE"), nullable=False)
    provider_calendar_id: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    time_zone: Mapped[str | None] = mapped_column(String(100))
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    check_conflicts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    add_events: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    connection: Mapped[CalendarConnection] = relationship(back_populates="calendars")


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="Default hours")
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC")
    host: Mapped[Host] = relationship(back_populates="schedule")
    intervals: Mapped[list[AvailabilityInterval]] = relationship(back_populates="schedule", cascade="all, delete-orphan")


class AvailabilityInterval(Base):
    __tablename__ = "availability_intervals"
    __table_args__ = (
        CheckConstraint("(weekday IS NULL) != (date IS NULL)", name="interval_scope"),
        CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="interval_weekday"),
        CheckConstraint("start_minute >= 0 AND end_minute <= 1440 AND start_minute < end_minute", name="interval_minutes"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id", ondelete="CASCADE"), nullable=False)
    weekday: Mapped[int | None] = mapped_column(Integer)
    date: Mapped[str | None] = mapped_column(String(10))
    start_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    end_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    is_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schedule: Mapped[Schedule] = relationship(back_populates="intervals")


class EventType(Base):
    __tablename__ = "event_types"
    __table_args__ = (UniqueConstraint("host_id", "slug", name="event_host_slug_unique"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    schedule_id: Mapped[str] = mapped_column(ForeignKey("schedules.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    color: Mapped[str] = mapped_column(String(40), nullable=False, default="#2563eb")
    description: Mapped[str | None] = mapped_column(Text)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    location_type: Mapped[str] = mapped_column(String(30), nullable=False, default="none")
    location_value: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="draft")
    booking_window_days: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    minimum_notice_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=1440)
    slot_interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    confirmation_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    host: Mapped[Host] = relationship(back_populates="event_types")
    schedule: Mapped[Schedule] = relationship()
    invitee_fields: Mapped[list[InviteeField]] = relationship(cascade="all, delete-orphan")


class InviteeField(Base):
    __tablename__ = "invitee_fields"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type_id: Mapped[str] = mapped_column(ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    field_type: Mapped[str] = mapped_column(String(30), nullable=False, default="text")
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Booking(Base):
    __tablename__ = "bookings"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="booking_time_order"),
        Index("booking_host_time", "host_id", "start_at", "end_at", "status"),
        Index("booking_event_start_scheduled", "event_type_id", "start_at", unique=True, sqlite_where=text("status = 'scheduled'"), postgresql_where=text("status = 'scheduled'")),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_type_id: Mapped[str] = mapped_column(ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    invitee_name: Mapped[str] = mapped_column(String(200), nullable=False)
    invitee_email: Mapped[str] = mapped_column(String(320), nullable=False)
    invitee_phone: Mapped[str | None] = mapped_column(String(80))
    invitee_timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="scheduled")
    cancel_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    reschedule_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    external_event_id: Mapped[str | None] = mapped_column(String(200))
    external_calendar_id: Mapped[str | None] = mapped_column(String(300))
    meeting_join_url: Mapped[str | None] = mapped_column(Text)
    external_sync_status: Mapped[str] = mapped_column(String(20), nullable=False, default="not_configured")
    external_sync_operation: Mapped[str | None] = mapped_column(String(10))
    external_sync_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
    event_type: Mapped[EventType] = relationship()
    answers: Mapped[list[BookingAnswer]] = relationship(cascade="all, delete-orphan")


class BookingAnswer(Base):
    __tablename__ = "booking_answers"
    __table_args__ = (UniqueConstraint("booking_id", "field_id", name="booking_field_unique"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    booking_id: Mapped[str] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    field_id: Mapped[str] = mapped_column(ForeignKey("invitee_fields.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("host_id", "email", name="contact_host_email_unique"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
