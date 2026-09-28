"""Scheduling operations shared by the FastAPI route layer."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .availability import AvailabilityError, BusyInterval, generate_slots, validate_intervals
from .integrations.calendar import CalendarConnectionExpired, CalendarConnectionRequiresReconnect, CalendarError, GoogleCalendarAdapter
from .models import AvailabilityInterval, Booking, BookingAnswer, CalendarConnection, Contact, EventType, ExternalCalendar, Host, InviteeField, Schedule
from .notifications import enqueue_booking_emails
from .schemas import AvailabilityInput, BookingActionResult, BookingInput, BookingView, EventTypeInput, HostCreate, IntervalInput, RescheduleInput, Slot, valid_timezone


class DomainError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _action_tokens() -> tuple[str, str]:
    cancel_token = secrets.token_urlsafe(32)
    reschedule_token = secrets.token_urlsafe(32)
    while reschedule_token == cancel_token:
        reschedule_token = secrets.token_urlsafe(32)
    return cancel_token, reschedule_token


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80].strip("-")
    return result or "event"


class SchedulerService:
    def __init__(
        self,
        session: Session,
        busy_provider: Callable[[EventType, datetime, datetime], list[BusyInterval]] | None = None,
        calendar_adapter: GoogleCalendarAdapter | None = None,
    ) -> None:
        self.session = session
        self.busy_provider = busy_provider
        self.calendar_adapter = calendar_adapter

    def _commit(self) -> None:
        try:
            self.session.commit()
        except IntegrityError as error:
            self.session.rollback()
            raise DomainError("A conflicting record already exists", 409) from error

    def _calendar_connection(self, host_id: str) -> CalendarConnection | None:
        return self.session.scalar(
            select(CalendarConnection).options(selectinload(CalendarConnection.calendars))
            .where(CalendarConnection.host_id == host_id, CalendarConnection.provider == "google")
        )

    @staticmethod
    def _destination(connection: CalendarConnection) -> ExternalCalendar | None:
        return next((calendar for calendar in connection.calendars if calendar.add_events), None)

    def _calendar_failure(self, connection: CalendarConnection, error: Exception) -> DomainError:
        if isinstance(error, (CalendarConnectionExpired, CalendarConnectionRequiresReconnect)):
            connection.status = "expired"
            self._commit()
            return DomainError("Reconnect Google Calendar to check availability", 503)
        return DomainError(str(error) if isinstance(error, CalendarError) else "Google Calendar action failed", 502)

    def _external_busy(
        self, event: EventType, range_start: datetime, range_end: datetime,
        exclude_booking_id: str | None,
    ) -> list[BusyInterval]:
        connection = self._calendar_connection(event.host_id)
        if connection is None:
            return []
        checked = [calendar for calendar in connection.calendars if calendar.check_conflicts]
        if not checked:
            return []
        if connection.status.lower() != "active":
            raise DomainError("Reconnect Google Calendar to check availability", 503)
        if self.calendar_adapter is None:
            raise DomainError("Google Calendar is not configured", 503)
        excluded_event_id = None
        if exclude_booking_id is not None:
            excluded_booking = self.session.get(Booking, exclude_booking_id)
            excluded_event_id = excluded_booking.external_event_id if excluded_booking else None
        busy: list[BusyInterval] = []
        try:
            for calendar in checked:
                entries = self.calendar_adapter.list_busy_intervals(
                    user_id=event.host_id, connected_account_id=connection.connected_account_id,
                    calendar_id=calendar.provider_calendar_id, time_min=range_start, time_max=range_end,
                )
                for entry in entries:
                    if entry.get("event_id") == excluded_event_id and excluded_event_id is not None:
                        continue
                    start_at, end_at = entry.get("start_at"), entry.get("end_at")
                    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
                        raise CalendarError("Google Calendar returned invalid busy times")
                    if start_at.tzinfo is None or end_at.tzinfo is None or end_at <= start_at:
                        raise CalendarError("Google Calendar returned invalid busy times")
                    busy.append(BusyInterval(_utc(start_at), _utc(end_at)))
        except Exception as error:
            raise self._calendar_failure(connection, error) from error
        return busy

    @staticmethod
    def _calendar_event_details(event: EventType, booking: Booking) -> dict:
        return {
            "start_at": _utc(booking.start_at), "end_at": _utc(booking.end_at),
            "timezone_name": event.schedule.timezone,
            "summary": event.name, "attendees": [booking.invitee_email],
            "description": event.description,
            "location": event.location_value if event.location_type in {"in_person", "custom"} else None,
        }

    def _sync_booking_calendar(self, booking: Booking, event: EventType, *, patch: bool = False) -> None:
        """Run only after the local booking commit; persist an attempted write first."""
        self._lock_host(booking.host_id)
        self.session.refresh(booking)
        operation = booking.external_sync_operation
        if operation not in {"create", "patch"}:
            self.session.rollback()
            return
        if operation == "create" and booking.external_sync_status != "pending":
            self.session.rollback()
            return
        connection = self._calendar_connection(event.host_id)
        calendar_id = booking.external_calendar_id
        if connection is None or not calendar_id:
            booking.external_sync_status = "pending" if operation == "create" else "failed"
            booking.external_sync_error = "Calendar event destination is no longer configured"
            self._commit()
            return
        if connection.status.lower() != "active" or self.calendar_adapter is None:
            booking.external_sync_status = "pending" if operation == "create" else "failed"
            booking.external_sync_error = "Reconnect Google Calendar" if connection.status.lower() != "active" else "Google Calendar is not configured"
            self._commit()
            return
        booking.external_sync_status = "inflight"
        booking.external_sync_error = None
        self._commit()
        details = self._calendar_event_details(event, booking)
        sent_start, sent_end = booking.start_at, booking.end_at
        try:
            if operation == "patch":
                result = self.calendar_adapter.patch_event(
                    user_id=event.host_id, connected_account_id=connection.connected_account_id,
                    calendar_id=calendar_id, event_id=booking.external_event_id, **details,
                )
            else:
                result = self.calendar_adapter.create_event(
                    user_id=event.host_id, connected_account_id=connection.connected_account_id,
                    calendar_id=calendar_id, conference_provider="google_meet" if event.location_type == "google_meet" else None,
                    **details,
                )
        except Exception as error:
            self._lock_host(booking.host_id)
            self.session.refresh(booking)
            if isinstance(error, (CalendarConnectionExpired, CalendarConnectionRequiresReconnect)):
                connection.status = "expired"
            if booking.external_sync_operation == operation:
                booking.external_sync_status = "failed"
                booking.external_sync_error = str(error) if isinstance(error, CalendarError) else "Google Calendar action failed"
            self._commit()
            return
        self._lock_host(booking.host_id)
        self.session.refresh(booking)
        if (
            operation == "patch" and booking.status == "scheduled"
            and booking.external_event_id == result["event_id"]
            and (booking.start_at != sent_start or booking.end_at != sent_end)
        ):
            booking.external_sync_status = "pending"
            booking.external_sync_operation = "patch"
            booking.external_sync_error = None
            self._commit()
            self._sync_booking_calendar(booking, event, patch=True)
            return
        if booking.external_sync_operation != operation:
            self.session.rollback()
            return
        booking.external_event_id = result["event_id"]
        booking.meeting_join_url = result.get("meeting_join_url")
        booking.external_sync_error = None
        if booking.status == "canceled":
            booking.external_sync_status = "pending"
            booking.external_sync_operation = "delete"
        elif booking.start_at != sent_start or booking.end_at != sent_end:
            booking.external_sync_status = "pending"
            booking.external_sync_operation = "patch"
        else:
            booking.external_sync_status = "synced"
            booking.external_sync_operation = None
        next_operation = booking.external_sync_operation
        self._commit()
        if next_operation == "delete":
            self._delete_booking_calendar(booking)
        elif next_operation == "patch":
            self._sync_booking_calendar(booking, event, patch=True)

    def _delete_booking_calendar(self, booking: Booking) -> None:
        self._lock_host(booking.host_id)
        self.session.refresh(booking)
        if not booking.external_event_id:
            self.session.rollback()
            return
        connection = self._calendar_connection(booking.host_id)
        if connection is None or connection.status.lower() != "active" or self.calendar_adapter is None:
            booking.external_sync_status = "failed"
            booking.external_sync_error = "Reconnect Google Calendar before deleting the event"
            self._commit()
            return
        calendar_id = booking.external_calendar_id
        if not calendar_id:
            booking.external_sync_status = "failed"
            booking.external_sync_error = "Calendar event destination is unknown"
            self._commit()
            return
        booking.external_sync_status = "inflight"
        booking.external_sync_error = None
        self._commit()
        try:
            self.calendar_adapter.delete_event(
                user_id=booking.host_id, connected_account_id=connection.connected_account_id,
                calendar_id=calendar_id, event_id=booking.external_event_id,
            )
        except Exception as error:
            self._lock_host(booking.host_id)
            self.session.refresh(booking)
            if isinstance(error, (CalendarConnectionExpired, CalendarConnectionRequiresReconnect)):
                connection.status = "expired"
            if booking.external_sync_operation == "delete":
                booking.external_sync_status = "failed"
                booking.external_sync_error = str(error) if isinstance(error, CalendarError) else "Google Calendar action failed"
            self._commit()
            return
        self._lock_host(booking.host_id)
        self.session.refresh(booking)
        if booking.external_sync_operation != "delete":
            self.session.rollback()
            return
        booking.external_event_id = None
        booking.external_calendar_id = None
        booking.meeting_join_url = None
        booking.external_sync_status = "synced"
        booking.external_sync_operation = None
        booking.external_sync_error = None
        self._commit()

    def get_host(self, host_id: str) -> Host:
        host = self.session.get(Host, host_id)
        if host is None:
            raise DomainError("Host not found", 404)
        return host

    def create_host(self, data: HostCreate) -> Host:
        host = Host(
            username=data.username,
            display_name=data.display_name.strip(),
            email=data.email.strip().lower(),
            timezone=data.timezone,
            api_key_hash=data.api_key_hash,
        )
        schedule = Schedule(host=host, name="Default hours", timezone=data.timezone)
        self.session.add(schedule)
        self._commit()
        return host

    def _schedule(self, host_id: str) -> Schedule:
        self.get_host(host_id)
        schedule = self.session.scalar(
            select(Schedule).options(selectinload(Schedule.intervals)).where(Schedule.host_id == host_id)
        )
        if schedule is None:
            raise DomainError("Availability schedule not found", 404)
        return schedule

    def get_availability(self, host_id: str) -> dict:
        schedule = self._schedule(host_id)
        return {
            "id": schedule.id, "host_id": schedule.host_id, "name": schedule.name,
            "timezone": schedule.timezone,
            "intervals": [
                {"id": i.id, "weekday": i.weekday, "date": i.date,
                 "start_minute": i.start_minute, "end_minute": i.end_minute,
                 "is_available": i.is_available} for i in schedule.intervals
            ],
        }

    def update_availability(self, host_id: str, data: AvailabilityInput) -> dict:
        self.get_host(host_id)
        try:
            validate_intervals(data.intervals)
        except AvailabilityError as error:
            raise DomainError(str(error)) from error
        if len(data.intervals) > 64:
            raise DomainError("Too many availability intervals")
        available_weekdays = [i.weekday for i in data.intervals if i.weekday is not None and i.is_available]
        if len(available_weekdays) != len(set(available_weekdays)):
            raise DomainError("Availability editor supports one available interval per weekday")
        schedule = self._schedule(host_id)
        schedule.name = data.name.strip()
        schedule.timezone = data.timezone
        schedule.intervals = [AvailabilityInterval(**interval.model_dump()) for interval in data.intervals]
        self._commit()
        return self.get_availability(host_id)

    def _event_for_host(self, host_id: str, event_id: str) -> EventType:
        event = self.session.scalar(select(EventType).where(EventType.id == event_id, EventType.host_id == host_id))
        if event is None:
            raise DomainError("Event type not found", 404)
        return event

    def list_event_types(self, host_id: str) -> list[EventType]:
        self.get_host(host_id)
        return list(self.session.scalars(select(EventType).where(EventType.host_id == host_id).order_by(EventType.created_at.desc())))

    def _available_slug(self, host_id: str, requested: str) -> str:
        base = _slug(requested)
        for attempt in range(100):
            candidate = base if attempt == 0 else f"{base[:75]}-{attempt + 1}"
            if self.session.scalar(select(EventType.id).where(EventType.host_id == host_id, EventType.slug == candidate)) is None:
                return candidate
        raise DomainError("Could not find an available event slug", 409)

    def _apply_event_input(self, event: EventType, data: EventTypeInput, *, create: bool) -> None:
        name = data.name.strip()
        if not name:
            raise DomainError("Event type name is required")
        slug = _slug(data.slug or name)
        if create:
            slug = self._available_slug(event.host_id, slug)
        elif self.session.scalar(select(EventType.id).where(EventType.host_id == event.host_id, EventType.slug == slug, EventType.id != event.id)):
            raise DomainError("An event type with this slug already exists", 409)
        event.name = name
        event.slug = slug
        event.color = data.color.strip() or "#2563eb"
        event.description = data.description.strip() or None if data.description else None
        event.duration_minutes = data.duration_minutes
        event.location_type = "google_meet" if data.location_type == "video" else data.location_type
        event.location_value = data.location_value.strip() or None if data.location_value else None
        event.booking_window_days = data.booking_window_days
        event.minimum_notice_minutes = data.minimum_notice_minutes
        event.slot_interval_minutes = data.slot_interval_minutes
        event.confirmation_message = data.confirmation_message.strip() or None if data.confirmation_message else None
        event.status = data.status

    def create_event_type(self, host_id: str, data: EventTypeInput) -> EventType:
        self.get_host(host_id)
        schedule = self._schedule(host_id)
        event = EventType(host_id=host_id, schedule_id=schedule.id, name="", slug="")
        self._apply_event_input(event, data, create=True)
        event.invitee_fields = [
            InviteeField(label="Name", field_type="text", is_required=True, position=0),
            InviteeField(label="Email", field_type="email", is_required=True, position=1),
            InviteeField(label="Preparation notes", field_type="textarea", is_required=False, position=2),
        ]
        if event.status == "active":
            self._assert_publishable(event)
            self.get_host(host_id).is_public = True
        self.session.add(event)
        self._commit()
        return event

    def create_draft(self, host_id: str) -> EventType:
        return self.create_event_type(host_id, EventTypeInput(name="New Meeting"))

    def update_event_type(self, host_id: str, event_id: str, data: EventTypeInput) -> EventType:
        event = self._event_for_host(host_id, event_id)
        self._apply_event_input(event, data, create=False)
        if event.status == "active":
            self._assert_publishable(event)
            self.get_host(host_id).is_public = True
        self._commit()
        return event

    def _assert_publishable(self, event: EventType) -> None:
        if not event.name.strip() or not event.slug.strip() or not event.schedule_id:
            raise DomainError("Name, slug, and availability are required before publishing")
        if event.location_type == "none":
            raise DomainError("Add a location before publishing")
        if event.location_type == "google_meet":
            connection = self._calendar_connection(event.host_id)
            if connection is None or connection.status.lower() != "active" or self._destination(connection) is None:
                raise DomainError("Connect Google Calendar and select a destination calendar before publishing Google Meet event types")
        if event.location_type in {"phone_host", "in_person", "custom"} and not (event.location_value or "").strip():
            raise DomainError("Add location details before publishing")

    def publish_event_type(self, host_id: str, event_id: str) -> EventType:
        event = self._event_for_host(host_id, event_id)
        self._assert_publishable(event)
        event.status = "active"
        self.get_host(host_id).is_public = True
        self._commit()
        return event

    def set_event_status(self, host_id: str, event_id: str, status: str) -> EventType:
        if status not in {"draft", "active"}:
            raise DomainError("Invalid event type status")
        event = self._event_for_host(host_id, event_id)
        if status == "active":
            return self.publish_event_type(host_id, event_id)
        event.status = "draft"
        self._commit()
        return event

    def duplicate_event_type(self, host_id: str, event_id: str) -> EventType:
        existing = self._event_for_host(host_id, event_id)
        data = EventTypeInput(
            name=f"{existing.name} Copy"[:120], slug=f"{existing.slug}-copy",
            color=existing.color, description=existing.description, duration_minutes=existing.duration_minutes,
            location_type=existing.location_type, location_value=existing.location_value,
            booking_window_days=existing.booking_window_days, minimum_notice_minutes=existing.minimum_notice_minutes,
            slot_interval_minutes=existing.slot_interval_minutes, confirmation_message=existing.confirmation_message,
        )
        return self.create_event_type(host_id, data)

    def delete_event_type(self, host_id: str, event_id: str) -> None:
        event = self._event_for_host(host_id, event_id)
        self.session.delete(event)
        self._commit()

    def get_public_profile(self, username: str) -> dict:
        host = self.session.scalar(select(Host).where(Host.username == username, Host.is_public.is_(True)))
        if host is None:
            raise DomainError("Public profile not found", 404)
        events = self.session.scalars(select(EventType).where(EventType.host_id == host.id, EventType.status == "active"))
        return {
            "username": host.username,
            "display_name": host.display_name,
            "welcome_message": host.welcome_message,
            "event_types": [
                {"id": event.id, "name": event.name, "slug": event.slug, "color": event.color,
                 "description": event.description, "duration_minutes": event.duration_minutes,
                 "location_type": event.location_type} for event in events
            ],
        }

    def _public_event(self, username: str, slug: str) -> EventType:
        event = self.session.scalar(
            select(EventType).join(Host).options(selectinload(EventType.invitee_fields), selectinload(EventType.schedule).selectinload(Schedule.intervals))
            .where(Host.username == username, Host.is_public.is_(True), EventType.slug == slug, EventType.status == "active")
        )
        if event is None:
            raise DomainError("This booking link is not available", 404)
        return event

    def get_public_event(self, username: str, slug: str) -> dict:
        event = self._public_event(username, slug)
        host = self.get_host(event.host_id)
        return {
            "id": event.id, "name": event.name, "slug": event.slug, "description": event.description,
            "duration_minutes": event.duration_minutes, "location_type": event.location_type,
            "location_value": event.location_value, "booking_window_days": event.booking_window_days,
            "minimum_notice_minutes": event.minimum_notice_minutes, "slot_interval_minutes": event.slot_interval_minutes,
            "confirmation_message": event.confirmation_message,
            "profile": {"username": host.username, "display_name": host.display_name},
            "schedule": {"timezone": event.schedule.timezone, "intervals": [
                {"weekday": i.weekday, "date": i.date, "start_minute": i.start_minute,
                 "end_minute": i.end_minute, "is_available": i.is_available} for i in event.schedule.intervals]},
            "invitee_fields": [{"id": f.id, "label": f.label, "field_type": f.field_type,
                                "is_required": f.is_required, "position": f.position} for f in event.invitee_fields],
        }

    def _slots_for_event(self, event: EventType, start_date: date, end_date: date, now: datetime, exclude_booking_id: str | None = None) -> list[Slot]:
        if (end_date - start_date).days > 366:
            raise DomainError("Date range is too large")
        # Query a wide UTC envelope because the requested days are local to the schedule.
        range_start = datetime.combine(start_date - timedelta(days=1), datetime.min.time(), timezone.utc)
        range_end = datetime.combine(end_date + timedelta(days=2), datetime.min.time(), timezone.utc)
        stmt = select(Booking).where(Booking.host_id == event.host_id, Booking.status == "scheduled", Booking.start_at < range_end, Booking.end_at > range_start)
        if exclude_booking_id:
            stmt = stmt.where(Booking.id != exclude_booking_id)
        busy = [BusyInterval(_utc(b.start_at), _utc(b.end_at)) for b in self.session.scalars(stmt)]
        busy.extend(self._external_busy(event, range_start, range_end, exclude_booking_id))
        if self.busy_provider is not None:
            busy.extend(self.busy_provider(event, range_start, range_end))
        intervals = [IntervalInput(weekday=i.weekday, date=i.date, start_minute=i.start_minute,
                                   end_minute=i.end_minute, is_available=i.is_available) for i in event.schedule.intervals]
        try:
            return generate_slots(
                now=now, range_start_date=start_date, range_end_date=end_date,
                schedule_timezone=event.schedule.timezone, duration_minutes=event.duration_minutes,
                slot_interval_minutes=event.slot_interval_minutes,
                minimum_notice_minutes=max(120, event.minimum_notice_minutes),
                booking_window_days=event.booking_window_days, intervals=intervals, busy_intervals=busy,
            )
        except AvailabilityError as error:
            raise DomainError(str(error)) from error

    def get_slots(self, username: str, slug: str, start_date: date, end_date: date, now: datetime | None = None) -> list[Slot]:
        event = self._public_event(username, slug)
        return self._slots_for_event(event, start_date, end_date, now or datetime.now(timezone.utc))

    def _lock_host(self, host_id: str) -> None:
        # Serialize reservations for one host so overlapping event types cannot double book.
        if self.session.bind.dialect.name == "postgresql":
            self.session.execute(select(Host.id).where(Host.id == host_id).with_for_update())
        elif self.session.bind.dialect.name == "sqlite":
            # SQLite's write lock is acquired before reading the candidate slot.
            self.session.connection().exec_driver_sql("BEGIN IMMEDIATE")

    def _match_slot(self, event: EventType, start_at: datetime, now: datetime, exclude_booking_id: str | None = None) -> Slot:
        day = start_at.astimezone(timezone.utc).date()
        slots = self._slots_for_event(event, day - timedelta(days=1), day + timedelta(days=1), now, exclude_booking_id)
        for slot in slots:
            if slot.start_at == start_at:
                return slot
        raise DomainError("This time is no longer available", 409)

    def _booking_answers(self, event: EventType, data: BookingInput) -> list[BookingAnswer]:
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
        missing = [f.label for f in fields.values() if f.is_required and f.label not in {"Name", "Email"} and f.id not in seen]
        if missing:
            raise DomainError("Required booking answers are missing")
        if data.prep_notes:
            notes = data.prep_notes.strip()
            if len(notes) > 2000:
                raise DomainError("Preparation notes are too long")
            field = next((f for f in fields.values() if f.label == "Preparation notes"), None)
            if field is not None and field.id not in seen:
                answers.append(BookingAnswer(field_id=field.id, value=notes))
        return answers

    def create_booking(self, username: str, slug: str, data: BookingInput, now: datetime | None = None) -> BookingActionResult:
        event = self._public_event(username, slug)
        name = data.invitee_name.strip()
        email = data.invitee_email.strip().lower()
        if not name or len(name) > 200:
            raise DomainError("Enter your name")
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 320:
            raise DomainError("Enter a valid email")
        phone = data.invitee_phone.strip() if data.invitee_phone else None
        if event.location_type == "phone_invitee" and (not phone or len(re.sub(r"\D", "", phone)) < 7):
            raise DomainError("Enter a valid phone number")
        start = _utc(data.start_at)
        answers = self._booking_answers(event, data)
        cancel_token, reschedule_token = _action_tokens()
        try:
            self._lock_host(event.host_id)
            slot = self._match_slot(event, start, now or datetime.now(timezone.utc))
            connection = self._calendar_connection(event.host_id)
            destination = self._destination(connection) if connection is not None else None
            booking = Booking(
                event_type_id=event.id, host_id=event.host_id, invitee_name=name, invitee_email=email,
                invitee_phone=phone, invitee_timezone=data.invitee_timezone,
                start_at=slot.start_at, end_at=slot.end_at, status="scheduled",
                cancel_token_hash=_token_hash(cancel_token), reschedule_token_hash=_token_hash(reschedule_token),
                external_calendar_id=destination.provider_calendar_id if destination else None,
                external_sync_status="pending" if destination else "not_configured",
                external_sync_operation="create" if destination else None, answers=answers,
            )
            self.session.add(booking)
            contact = self.session.scalar(select(Contact).where(Contact.host_id == event.host_id, Contact.email == email))
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
        return BookingActionResult(booking=BookingView.model_validate(booking), cancel_token=cancel_token, reschedule_token=reschedule_token)

    def _action_booking(self, booking_id: str, token: str, kind: str) -> Booking:
        if not token:
            raise DomainError("Booking action link is invalid or expired", 404)
        booking = self.session.get(Booking, booking_id)
        if booking is None or getattr(booking, f"{kind}_token_hash") != _token_hash(token):
            raise DomainError("Booking action link is invalid or expired", 404)
        return booking

    def get_booking(self, booking_id: str, token: str | None = None) -> BookingView:
        if token is None:
            raise DomainError("Booking action link is invalid or expired", 404)
        hashed = _token_hash(token)
        booking = self.session.get(Booking, booking_id)
        if booking is None or hashed not in {booking.cancel_token_hash, booking.reschedule_token_hash}:
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
            elif booking.external_sync_operation == "create" and booking.external_sync_status == "pending":
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
        booking = self.session.scalar(select(Booking).where(Booking.id == booking_id, Booking.host_id == host_id))
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
            elif booking.external_sync_operation == "create" and booking.external_sync_status == "pending":
                booking.external_sync_status = "synced"
                booking.external_sync_operation = None
                booking.external_calendar_id = None
            self._commit()
        except Exception:
            self.session.rollback()
            raise
        self._delete_booking_calendar(booking)
        return BookingView.model_validate(booking)

    def reschedule_booking(self, booking_id: str, token: str, data: RescheduleInput, now: datetime | None = None) -> BookingActionResult:
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
            event = self.session.scalar(select(EventType).options(selectinload(EventType.schedule).selectinload(Schedule.intervals)).where(EventType.id == booking.event_type_id))
            if event is None or event.status != "active":
                raise DomainError("This booking link is not available", 404)
            slot = self._match_slot(event, start, now or datetime.now(timezone.utc), exclude_booking_id=booking.id)
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
                destination = self._destination(connection) if connection is not None else None
                booking.external_calendar_id = destination.provider_calendar_id if destination else None
                booking.external_sync_status = "pending" if destination else "not_configured"
                booking.external_sync_operation = "create" if destination else None
                booking.external_sync_error = None
            enqueue_booking_emails(self.session, booking.id, "rescheduled", start_at=slot.start_at)
            self._commit()
        except Exception:
            self.session.rollback()
            raise
        self._sync_booking_calendar(booking, event, patch=True)
        return BookingActionResult(booking=BookingView.model_validate(booking), cancel_token=cancel_token, reschedule_token=reschedule_token)

    def retry_calendar_sync(self, host_id: str, booking_id: str) -> BookingView:
        booking = self.session.scalar(select(Booking).where(Booking.id == booking_id, Booking.host_id == host_id))
        if booking is None:
            raise DomainError("Booking not found", 404)
        if booking.external_sync_status not in {"pending", "inflight", "failed"}:
            raise DomainError("Booking calendar sync is not pending or failed")
        if booking.external_sync_operation == "create" and booking.external_sync_status != "pending":
            raise DomainError("Check Google Calendar for this booking, then reconcile the calendar create outcome", 409)
        if booking.status == "canceled":
            self._delete_booking_calendar(booking)
        else:
            event = self.session.scalar(
                select(EventType).options(selectinload(EventType.schedule)).where(EventType.id == booking.event_type_id)
            )
            if event is None:
                raise DomainError("Event type not found", 404)
            self._sync_booking_calendar(booking, event, patch=bool(booking.external_event_id))
        return BookingView.model_validate(booking)

    def reconcile_calendar_create(
        self, host_id: str, booking_id: str, *, confirmed_absent: bool = False,
        reconciled_event_id: str | None = None,
    ) -> BookingView:
        if confirmed_absent == bool(reconciled_event_id):
            raise DomainError("Confirm the event is absent or provide its Google Calendar event ID")
        booking = self.session.scalar(select(Booking).where(Booking.id == booking_id, Booking.host_id == host_id))
        if booking is None:
            raise DomainError("Booking not found", 404)
        self._lock_host(host_id)
        self.session.refresh(booking)
        if booking.external_sync_operation != "create" or booking.external_sync_status not in {"inflight", "failed"}:
            self.session.rollback()
            raise DomainError("Booking calendar create does not need reconciliation", 409)
        if confirmed_absent and booking.external_sync_status == "inflight":
            self.session.rollback()
            raise DomainError("Calendar create may still be running; quiesce workers and repair the pending operation before retrying", 409)
        if reconciled_event_id is not None:
            reconciled_event_id = reconciled_event_id.strip()
            if not reconciled_event_id or len(reconciled_event_id) > 200:
                self.session.rollback()
                raise DomainError("Google Calendar event ID is invalid")
            booking.external_event_id = reconciled_event_id
            booking.external_sync_operation = "delete" if booking.status == "canceled" else "patch"
            booking.external_sync_status = "pending"
            booking.external_sync_error = None
            self._commit()
            if booking.status == "canceled":
                self._delete_booking_calendar(booking)
            else:
                event = self.session.scalar(select(EventType).options(selectinload(EventType.schedule)).where(EventType.id == booking.event_type_id))
                if event is None:
                    raise DomainError("Event type not found", 404)
                self._sync_booking_calendar(booking, event, patch=True)
        elif booking.status == "canceled":
            booking.external_sync_status = "synced"
            booking.external_sync_operation = None
            booking.external_calendar_id = None
            booking.external_sync_error = None
            self._commit()
        else:
            booking.external_sync_status = "pending"
            booking.external_sync_error = None
            self._commit()
            event = self.session.scalar(select(EventType).options(selectinload(EventType.schedule)).where(EventType.id == booking.event_type_id))
            if event is None:
                raise DomainError("Event type not found", 404)
            self._sync_booking_calendar(booking, event)
        return BookingView.model_validate(booking)

    def list_meetings(self, host_id: str) -> list[Booking]:
        self.get_host(host_id)
        return list(self.session.scalars(select(Booking).where(Booking.host_id == host_id).order_by(Booking.start_at.desc())))

    def list_contacts(self, host_id: str) -> list[Contact]:
        self.get_host(host_id)
        return list(self.session.scalars(select(Contact).where(Contact.host_id == host_id).order_by(Contact.name)))
