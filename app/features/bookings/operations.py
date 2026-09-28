"""Bookings scheduling operations."""

from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.features.bookings.models import Booking
from app.features.bookings.models import BookingAnswer
from app.features.contacts.models import Contact
from app.features.event_types.models import EventType
from app.features.hosts.models import Host
from app.features.availability.models import Schedule
from app.features.notifications.service import enqueue_booking_emails
from app.features.bookings.schemas import BookingActionResult
from app.features.bookings.schemas import BookingInput
from app.features.bookings.schemas import BookingView
from app.features.bookings.schemas import RescheduleInput
from app.features.availability.schemas import Slot
from app.core.errors import DomainError
from app.features.bookings.tokens import _utc, _token_hash, _action_tokens


class BookingMixin:
    def _lock_host(self, host_id: str) -> None:
        # Serialize reservations for one host so overlapping event types cannot double book.
        if self.session.bind.dialect.name == "postgresql":
            self.session.execute(
                select(Host.id).where(Host.id == host_id).with_for_update()
            )
        elif self.session.bind.dialect.name == "sqlite":
            # SQLite's write lock is acquired before reading the candidate slot.
            self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    def _match_slot(
        self,
        event: EventType,
        start_at: datetime,
        now: datetime,
        exclude_booking_id: str | None = None,
    ) -> Slot:
        day = start_at.astimezone(timezone.utc).date()
        slots = self._slots_for_event(
            event,
            day - timedelta(days=1),
            day + timedelta(days=1),
            now,
            exclude_booking_id,
        )
        for slot in slots:
            if slot.start_at == start_at:
                return slot
        raise DomainError("This time is no longer available", 409)

    def _booking_answers(
        self, event: EventType, data: BookingInput
    ) -> list[BookingAnswer]:
        fields = {field.id: field for field in event.invitee_fields}
        seen: set[str] = set()
        answers: list[BookingAnswer] = []
        for answer in data.answers:
            if answer.field_id in seen or answer.field_id not in fields:
                raise DomainError("Booking answers are invalid")
            if fields[answer.field_id].label in {"Name", "Email"}:
                raise DomainError("Invalid booking answer")
            seen.add(answer.field_id)
            value = answer.value.strip()
            if len(value) > 2000:
                raise DomainError("Booking answer is too long")
            if not value:
                continue
            answers.append(BookingAnswer(field_id=answer.field_id, value=value))
        # Name and email are first-class booking fields. Other required fields must be answered.
        missing = [
            f.label
            for f in fields.values()
            if f.is_required and f.label not in {"Name", "Email"} and f.id not in seen
        ]
        if missing:
            raise DomainError("Required booking answers are missing")
        if data.prep_notes:
            notes = data.prep_notes.strip()
            if len(notes) > 2000:
                raise DomainError("Preparation notes are too long")
            field = next(
                (f for f in fields.values() if f.label == "Preparation notes"), None
            )
            if field is not None and field.id not in seen:
                answers.append(BookingAnswer(field_id=field.id, value=notes))
        return answers

    def create_booking(
        self, username: str, slug: str, data: BookingInput, now: datetime | None = None
    ) -> BookingActionResult:
        event = self._public_event(username, slug)
        name = data.invitee_name.strip()
        email = data.invitee_email.strip().lower()
        if not name or len(name) > 200:
            raise DomainError("Enter your name")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 320:
            raise DomainError("Enter a valid email")
        phone = data.invitee_phone.strip() if data.invitee_phone else None
        if event.location_type == "phone_invitee" and (
            not phone or len(re.sub(r"\D", "", phone)) < 7
        ):
            raise DomainError("Enter a valid phone number")
        start = _utc(data.start_at)
        answers = self._booking_answers(event, data)
        cancel_token, reschedule_token = _action_tokens()
        try:
            self._lock_host(event.host_id)
            slot = self._match_slot(event, start, now or datetime.now(timezone.utc))
            connection = self._calendar_connection(event.host_id)
            destination = (
                self._destination(connection) if connection is not None else None
            )
            booking = Booking(
                event_type_id=event.id,
                host_id=event.host_id,
                invitee_name=name,
                invitee_email=email,
                invitee_phone=phone,
                invitee_timezone=data.invitee_timezone,
                start_at=slot.start_at,
                end_at=slot.end_at,
                status="scheduled",
                cancel_token_hash=_token_hash(cancel_token),
                reschedule_token_hash=_token_hash(reschedule_token),
                external_calendar_id=destination.provider_calendar_id
                if destination
                else None,
                external_sync_status="pending" if destination else "not_configured",
                external_sync_operation="create" if destination else None,
                answers=answers,
            )
            self.session.add(booking)
            contact = self.session.scalar(
                select(Contact).where(
                    Contact.host_id == event.host_id, Contact.email == email
                )
            )
            if contact is None:
                self.session.add(Contact(host_id=event.host_id, name=name, email=email))
            else:
                contact.name = name
            self.session.flush()
            enqueue_booking_emails(self.session, booking.id, "confirmed")
            self._commit()
        except Exception:
            self.session.rollback()
            raise
        self._sync_booking_calendar(booking, event)
        return BookingActionResult(
            booking=BookingView.model_validate(booking),
            cancel_token=cancel_token,
            reschedule_token=reschedule_token,
        )

    def _action_booking(self, booking_id: str, token: str, kind: str) -> Booking:
        if not token:
            raise DomainError("Booking action link is invalid or expired", 404)
        booking = self.session.get(Booking, booking_id)
        if booking is None or getattr(booking, f"{kind}_token_hash") != _token_hash(
            token
        ):
            raise DomainError("Booking action link is invalid or expired", 404)
        return booking

    def get_booking(self, booking_id: str, token: str | None = None) -> BookingView:
        if token is None:
            raise DomainError("Booking action link is invalid or expired", 404)
        hashed = _token_hash(token)
        booking = self.session.get(Booking, booking_id)
        if booking is None or hashed not in {
            booking.cancel_token_hash,
            booking.reschedule_token_hash,
        }:
            raise DomainError("Booking action link is invalid or expired", 404)
        return BookingView.model_validate(booking)

    def cancel_booking(self, booking_id: str, token: str) -> BookingView:
        booking = self._action_booking(booking_id, token, "cancel")
        try:
            self._lock_host(booking.host_id)
            self.session.refresh(booking)
            if booking.cancel_token_hash != _token_hash(token):
                raise DomainError("Booking action link is invalid or expired", 404)
            if booking.status != "canceled":
                booking.status = "canceled"
                enqueue_booking_emails(self.session, booking.id, "canceled")
            if booking.external_event_id:
                booking.external_sync_status = "pending"
                booking.external_sync_operation = "delete"
                booking.external_sync_error = None
            elif (
                booking.external_sync_operation == "create"
                and booking.external_sync_status == "pending"
            ):
                booking.external_sync_status = "synced"
                booking.external_sync_operation = None
                booking.external_calendar_id = None
            self._commit()
        except Exception:
            self.session.rollback()
            raise
        self._delete_booking_calendar(booking)
        return BookingView.model_validate(booking)

    def cancel_booking_as_host(self, host_id: str, booking_id: str) -> BookingView:
        booking = self.session.scalar(
            select(Booking).where(Booking.id == booking_id, Booking.host_id == host_id)
        )
        if booking is None:
            raise DomainError("Booking not found", 404)
        try:
            self._lock_host(host_id)
            self.session.refresh(booking)
            if booking.status != "canceled":
                booking.status = "canceled"
                enqueue_booking_emails(self.session, booking.id, "canceled")
            if booking.external_event_id:
                booking.external_sync_status = "pending"
                booking.external_sync_operation = "delete"
                booking.external_sync_error = None
            elif (
                booking.external_sync_operation == "create"
                and booking.external_sync_status == "pending"
            ):
                booking.external_sync_status = "synced"
                booking.external_sync_operation = None
                booking.external_calendar_id = None
            self._commit()
        except Exception:
            self.session.rollback()
            raise
        self._delete_booking_calendar(booking)
        return BookingView.model_validate(booking)

    def reschedule_booking(
        self,
        booking_id: str,
        token: str,
        data: RescheduleInput,
        now: datetime | None = None,
    ) -> BookingActionResult:
        booking = self._action_booking(booking_id, token, "reschedule")
        if booking.status == "canceled":
            raise DomainError("Canceled bookings cannot be rescheduled")
        start = _utc(data.start_at)
        cancel_token, reschedule_token = _action_tokens()
        try:
            self._lock_host(booking.host_id)
            self.session.refresh(booking)
            if booking.reschedule_token_hash != _token_hash(token):
                raise DomainError("Booking action link is invalid or expired", 404)
            if booking.status == "canceled":
                raise DomainError("Canceled bookings cannot be rescheduled")
            event = self.session.scalar(
                select(EventType)
                .options(
                    selectinload(EventType.schedule).selectinload(Schedule.intervals)
                )
                .where(EventType.id == booking.event_type_id)
            )
            if event is None or event.status != "active":
                raise DomainError("This booking link is not available", 404)
            slot = self._match_slot(
                event,
                start,
                now or datetime.now(timezone.utc),
                exclude_booking_id=booking.id,
            )
            booking.start_at = slot.start_at
            booking.end_at = slot.end_at
            booking.invitee_timezone = data.invitee_timezone
            booking.cancel_token_hash = _token_hash(cancel_token)
            booking.reschedule_token_hash = _token_hash(reschedule_token)
            if booking.external_event_id:
                booking.external_sync_status = "pending"
                booking.external_sync_operation = "patch"
                booking.external_sync_error = None
            elif booking.external_sync_operation != "create":
                connection = self._calendar_connection(booking.host_id)
                destination = (
                    self._destination(connection) if connection is not None else None
                )
                booking.external_calendar_id = (
                    destination.provider_calendar_id if destination else None
                )
                booking.external_sync_status = (
                    "pending" if destination else "not_configured"
                )
                booking.external_sync_operation = "create" if destination else None
                booking.external_sync_error = None
            enqueue_booking_emails(
                self.session, booking.id, "rescheduled", start_at=slot.start_at
            )
            self._commit()
        except Exception:
            self.session.rollback()
            raise
        self._sync_booking_calendar(booking, event, patch=True)
        return BookingActionResult(
            booking=BookingView.model_validate(booking),
            cancel_token=cancel_token,
            reschedule_token=reschedule_token,
        )

    def list_meetings(self, host_id: str) -> list[Booking]:
        self.get_host(host_id)
        return list(
            self.session.scalars(
                select(Booking)
                .where(Booking.host_id == host_id)
                .order_by(Booking.start_at.desc())
            )
        )
