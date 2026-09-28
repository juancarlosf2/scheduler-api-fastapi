"""Durable booking email outbox and verified Resend event persistence.

Call ``enqueue_booking_emails`` in the same transaction as a booking change.
After that transaction commits, ``dispatch_booking_emails`` can send the rows.
An interrupted send can be retried with the same provider idempotency key.
Messages are informational: public action tokens are returned once by the
booking API and their plaintext values are never persisted in email snapshots.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, Mapping

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.features.notifications.providers.resend import (
    EmailDeliveryError,
    ResendEmailAdapter,
    booking_email_idempotency_key,
)
from app.features.notifications.ports import EmailSender
from app.features.bookings.models import Booking
from app.features.event_types.models import EventType
from app.features.hosts.models import Host
from app.core.model import new_id
from app.core.model import utc_now
from app.features.notifications.models import EmailDelivery, ResendWebhookEvent


LIFECYCLES = {"confirmed", "rescheduled", "canceled"}
RECIPIENTS = {"host", "invitee"}
EVENT_STATUSES = {
    "email.sent": "sent",
    "email.delivered": "delivered",
    "email.delivery_delayed": "delivery_delayed",
    "email.bounced": "bounced",
    "email.complained": "complained",
    "email.failed": "failed",
}
PROCESSING_TIMEOUT = timedelta(minutes=5)


def _utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _render(
    booking: Booking, host: Host, event: EventType, lifecycle: str, recipient: str
) -> tuple[str, str, str]:
    action = {
        "confirmed": "confirmed",
        "rescheduled": "rescheduled",
        "canceled": "canceled",
    }[lifecycle]
    subject = f"Booking {action}: {event.name}"
    participant = booking.invitee_name if recipient == "host" else host.display_name
    heading = (
        f"Your booking was {action}."
        if recipient == "invitee"
        else f"A booking with {booking.invitee_name} was {action}."
    )
    start = _utc(booking.start_at).strftime("%Y-%m-%d %H:%M UTC")
    end = _utc(booking.end_at).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        heading,
        f"Event: {event.name}",
        f"With: {participant}",
        f"Time: {start} to {end}",
    ]
    if booking.meeting_join_url and lifecycle != "canceled":
        lines.append(f"Join: {booking.meeting_join_url}")
    body = "\n".join(lines)
    html = "<p>" + "<br>".join(escape(line) for line in lines) + "</p>"
    return subject, body, html


def enqueue_booking_emails(
    session: Session,
    booking_id: str,
    lifecycle: str,
    *,
    start_at: datetime | None = None,
) -> list[EmailDelivery]:
    """Insert host and invitee messages without committing the booking transaction.

    The booking's rotated reschedule token hash identifies its occurrence.
    Repeating an occurrence leaves existing status and rendered content intact.
    """
    if lifecycle not in LIFECYCLES:
        raise ValueError("Invalid booking email lifecycle")
    session.flush()
    booking = session.get(Booking, booking_id)
    if booking is None:
        raise ValueError("Booking not found")
    if lifecycle == "rescheduled" and start_at is None:
        start_at = booking.start_at
    host = session.get(Host, booking.host_id)
    event = session.get(EventType, booking.event_type_id)
    if host is None or event is None:
        raise ValueError("Booking owner or event type not found")
    dialect = session.get_bind().dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise RuntimeError("Email outbox requires SQLite or PostgreSQL")
    insert = sqlite_insert if dialect == "sqlite" else pg_insert
    result = []
    for recipient, to_email in (
        ("host", host.email),
        ("invitee", booking.invitee_email),
    ):
        key = booking_email_idempotency_key(
            booking_id,
            lifecycle,
            recipient,
            start_at,
            occurrence_id=booking.reschedule_token_hash
            if lifecycle == "rescheduled"
            else None,
        )
        subject, body, html = _render(booking, host, event, lifecycle, recipient)
        statement = (
            insert(EmailDelivery)
            .values(
                id=new_id(),
                booking_id=booking_id,
                recipient_type=recipient,
                lifecycle=lifecycle,
                to_email=to_email.strip().lower(),
                subject=subject,
                text_body=body,
                html_body=html,
                idempotency_key=key,
                status="queued",
                attempt_count=0,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
        )
        session.execute(statement)
        result.append(
            session.scalar(
                select(EmailDelivery).where(EmailDelivery.idempotency_key == key)
            )
        )
    return result


def refresh_queued_booking_emails(
    session: Session, booking_id: str
) -> list[EmailDelivery]:
    """Refresh the current occurrence after calendar sync supplies a meeting link.

    Earlier queued occurrences retain their original slot and link snapshots.
    A provider attempt always uses its original content and idempotency key.
    The caller owns the transaction commit.
    """
    session.flush()
    booking = session.get(Booking, booking_id)
    if booking is None:
        raise ValueError("Booking not found")
    host = session.get(Host, booking.host_id)
    event = session.get(EventType, booking.event_type_id)
    if host is None or event is None:
        raise ValueError("Booking owner or event type not found")
    lifecycle = "canceled" if booking.status == "canceled" else "confirmed"
    start_at = _utc(booking.start_at)
    if lifecycle == "confirmed":
        reschedule_key = booking_email_idempotency_key(
            booking_id,
            "rescheduled",
            "invitee",
            start_at,
            occurrence_id=booking.reschedule_token_hash,
        )
        if session.scalar(
            select(EmailDelivery.id).where(
                EmailDelivery.idempotency_key == reschedule_key
            )
        ):
            lifecycle = "rescheduled"
    keys = [
        booking_email_idempotency_key(
            booking_id,
            lifecycle,
            recipient,
            start_at,
            occurrence_id=booking.reschedule_token_hash
            if lifecycle == "rescheduled"
            else None,
        )
        for recipient in RECIPIENTS
    ]
    deliveries = list(
        session.scalars(
            select(EmailDelivery).where(
                EmailDelivery.booking_id == booking_id,
                EmailDelivery.status == "queued",
                EmailDelivery.idempotency_key.in_(keys),
            )
        )
    )
    for delivery in deliveries:
        delivery.subject, delivery.text_body, delivery.html_body = _render(
            booking,
            host,
            event,
            delivery.lifecycle,
            delivery.recipient_type,
        )
        delivery.updated_at = utc_now()
    return deliveries


def dispatch_delivery(
    session: Session,
    delivery_id: str,
    *,
    adapter: EmailSender | None = None,
    force: bool = False,
) -> EmailDelivery | None:
    """Claim and send one row. Provider retries retain the original idempotency key."""
    now = utc_now()
    eligible = [EmailDelivery.status == "queued"]
    if force:
        eligible.append(EmailDelivery.status == "failed")
    else:
        eligible.append(
            and_(
                EmailDelivery.status == "failed",
                or_(
                    EmailDelivery.next_attempt_at.is_(None),
                    EmailDelivery.next_attempt_at <= now,
                ),
            )
        )
    eligible.append(
        and_(EmailDelivery.status == "processing", EmailDelivery.next_attempt_at <= now)
    )
    claimed_id = session.scalar(
        update(EmailDelivery)
        .where(EmailDelivery.id == delivery_id, or_(*eligible))
        .values(
            status="processing",
            attempt_count=EmailDelivery.attempt_count + 1,
            next_attempt_at=now + PROCESSING_TIMEOUT,
            last_error=None,
            last_event_type="resend.send.processing",
            last_event_at=now,
            updated_at=now,
        )
        .returning(EmailDelivery.id)
        .execution_options(synchronize_session=False)
    )
    if claimed_id is None:
        return session.get(EmailDelivery, delivery_id)
    session.commit()  # Persist the claim before the external call.
    delivery = session.get(EmailDelivery, delivery_id)
    session.refresh(delivery)
    if adapter is None:
        try:
            adapter = ResendEmailAdapter()
        except EmailDeliveryError:
            _mark_send_failed(session, delivery)
            return delivery
    try:
        message_id = adapter.send(
            to=delivery.to_email,
            subject=delivery.subject,
            text=delivery.text_body,
            html=delivery.html_body,
            idempotency_key=delivery.idempotency_key,
            tags=[{"name": "booking_id", "value": delivery.booking_id}],
        )
    except Exception:
        _mark_send_failed(session, delivery)
        return delivery
    delivery.status = "sent"
    delivery.resend_message_id = message_id
    delivery.sent_at = utc_now()
    delivery.failed_at = None
    delivery.next_attempt_at = None
    delivery.last_error = None
    delivery.last_event_type = "resend.send.accepted"
    delivery.last_event_at = delivery.sent_at
    pending_events = session.scalars(
        select(ResendWebhookEvent)
        .where(
            ResendWebhookEvent.resend_message_id == message_id,
        )
        .order_by(ResendWebhookEvent.event_created_at, ResendWebhookEvent.received_at)
    ).all()
    for pending in pending_events:
        _apply_provider_status(
            delivery,
            pending.event_id,
            pending.event_type,
            pending.event_created_at or pending.received_at,
        )
    session.commit()
    return delivery


def _mark_send_failed(session: Session, delivery: EmailDelivery) -> None:
    now = utc_now()
    delivery.status = "failed"
    delivery.failed_at = now
    delivery.last_error = "Email delivery failed"
    delivery.last_event_type = "resend.send.failed"
    delivery.last_event_at = now
    delivery.next_attempt_at = now + timedelta(
        minutes=min(60, 2 ** min(delivery.attempt_count - 1, 6))
    )
    session.commit()


def dispatch_booking_emails(
    session: Session, booking_id: str, *, adapter: EmailSender | None = None
) -> list[EmailDelivery]:
    """Dispatch queued or due failed rows for a booking after its transaction commits."""
    ids = list(
        session.scalars(
            select(EmailDelivery.id).where(EmailDelivery.booking_id == booking_id)
        )
    )
    return [
        delivery
        for row_id in ids
        if (delivery := dispatch_delivery(session, row_id, adapter=adapter)) is not None
    ]


def dispatch_due_booking_emails(
    session: Session,
    *,
    adapter: EmailSender | None = None,
    limit: int = 100,
) -> list[EmailDelivery]:
    """Recover queued, due failed, and stale processing rows across bookings."""
    if limit < 1:
        raise ValueError("Email delivery limit must be positive")
    now = utc_now()
    due = or_(
        EmailDelivery.next_attempt_at.is_(None), EmailDelivery.next_attempt_at <= now
    )
    ids = list(
        session.scalars(
            select(EmailDelivery.id)
            .where(
                or_(
                    and_(EmailDelivery.status == "queued", due),
                    and_(EmailDelivery.status == "failed", due),
                    and_(
                        EmailDelivery.status == "processing",
                        EmailDelivery.next_attempt_at <= now,
                    ),
                )
            )
            .order_by(EmailDelivery.created_at, EmailDelivery.id)
            .limit(limit)
        )
    )
    return [
        delivery
        for row_id in ids
        if (delivery := dispatch_delivery(session, row_id, adapter=adapter)) is not None
    ]


def main() -> None:
    """Run one bounded recovery pass with the configured database and Resend adapter."""
    from app.core.database import make_engine, make_session_factory

    engine = make_engine()
    try:
        with make_session_factory(engine)() as session:
            dispatch_due_booking_emails(session)
    finally:
        engine.dispose()


def _apply_provider_status(
    delivery: EmailDelivery, event_id: str, event_type: str, event_at: datetime
) -> None:
    status = EVENT_STATUSES.get(event_type)
    if status is None:
        return
    if (
        delivery.last_event_id is not None
        and delivery.last_event_at is not None
        and _utc(delivery.last_event_at) > event_at
    ):
        return
    delivery.status = status
    delivery.last_event_id = event_id
    delivery.last_event_type = event_type
    delivery.last_event_at = event_at
    if status == "delivered":
        delivery.delivered_at = event_at
        delivery.sent_at = delivery.sent_at or event_at
        delivery.failed_at = None
        delivery.last_error = None
    elif status == "sent":
        delivery.sent_at = event_at
        delivery.failed_at = None
        delivery.last_error = None
    elif status in {"bounced", "complained", "failed"}:
        delivery.failed_at = event_at
        delivery.last_error = f"Resend reported {status}"
    elif status == "delivery_delayed":
        delivery.last_error = "Resend reported delivery_delayed"


def process_verified_webhook(
    session: Session, event_id: str, event: Mapping[str, Any]
) -> bool:
    """Apply an already signature-verified event, returning false for a duplicate."""
    event_type = event.get("type")
    if not isinstance(event_type, str):
        raise ValueError("Invalid email webhook")
    data = event.get("data")
    data = data if isinstance(data, Mapping) else {}
    message_id = next(
        (
            data[key]
            for key in ("email_id", "emailId", "id")
            if isinstance(data.get(key), str) and data[key].strip()
        ),
        None,
    )
    created = event.get("created_at")
    try:
        event_at = (
            _utc(datetime.fromisoformat(created.replace("Z", "+00:00")))
            if isinstance(created, str)
            else utc_now()
        )
    except ValueError:
        event_at = utc_now()
    dialect = session.get_bind().dialect.name
    insert = (
        sqlite_insert
        if dialect == "sqlite"
        else pg_insert
        if dialect == "postgresql"
        else None
    )
    if insert is None:
        raise RuntimeError("Email webhook storage requires SQLite or PostgreSQL")
    result = session.execute(
        insert(ResendWebhookEvent)
        .values(
            id=new_id(),
            event_id=event_id,
            event_type=event_type,
            resend_message_id=message_id,
            event_created_at=event_at,
            received_at=utc_now(),
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    if result.rowcount == 0:
        return False
    if message_id:
        delivery = session.scalar(
            select(EmailDelivery).where(EmailDelivery.resend_message_id == message_id)
        )
        if delivery:
            _apply_provider_status(delivery, event_id, event_type, event_at)
    webhook = session.scalar(
        select(ResendWebhookEvent).where(ResendWebhookEvent.event_id == event_id)
    )
    webhook.processed_at = utc_now()
    session.commit()
    return True


if __name__ == "__main__":
    main()
