"""Durable booking email outbox and verified Resend event persistence.

Call ``enqueue_booking_emails`` in the same transaction as a booking change.
After that transaction commits, ``dispatch_booking_emails`` can send the rows.
An interrupted send can be retried with the same provider idempotency key.
Messages are informational: public action tokens are returned once by the
booking API and their plaintext values are never persisted in email snapshots.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model import Base
from app.core.model import new_id
from app.core.model import utc_now


class EmailDelivery(Base):
    """One recipient and lifecycle occurrence per booking, including a send snapshot."""

    __tablename__ = "email_deliveries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="email_delivery_key_unique"),
        UniqueConstraint("resend_message_id", name="email_delivery_message_unique"),
        CheckConstraint(
            "recipient_type IN ('host', 'invitee')", name="email_delivery_recipient"
        ),
        CheckConstraint(
            "lifecycle IN ('confirmed', 'rescheduled', 'canceled')",
            name="email_delivery_lifecycle",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'sent', 'delivered', 'delivery_delayed', 'failed', 'bounced', 'complained')",
            name="email_delivery_status",
        ),
        CheckConstraint("attempt_count >= 0", name="email_delivery_attempt_count"),
        Index("email_delivery_booking_idx", "booking_id"),
        Index("email_delivery_due_idx", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    booking_id: Mapped[str] = mapped_column(
        ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False
    )
    recipient_type: Mapped[str] = mapped_column(String(10), nullable=False)
    lifecycle: Mapped[str] = mapped_column(String(12), nullable=False)
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    text_body: Mapped[str] = mapped_column(Text, nullable=False)
    html_body: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(500), nullable=False)
    resend_message_id: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    last_event_id: Mapped[str | None] = mapped_column(String(200))
    last_event_type: Mapped[str | None] = mapped_column(String(100))
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class ResendWebhookEvent(Base):
    """Verified webhook IDs prevent duplicate state transitions."""

    __tablename__ = "resend_webhook_events"
    __table_args__ = (UniqueConstraint("event_id", name="resend_webhook_event_unique"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    resend_message_id: Mapped[str | None] = mapped_column(String(200))
    event_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
