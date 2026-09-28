"""Calendars scheduling operations."""

from __future__ import annotations
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.features.availability.slots import BusyInterval
from app.features.calendars.providers.composio import (
    CalendarConnectionExpired,
    CalendarConnectionRequiresReconnect,
    CalendarError,
)
from app.features.bookings.models import Booking
from app.features.calendars.models import CalendarConnection
from app.features.event_types.models import EventType
from app.features.calendars.models import ExternalCalendar
from app.features.bookings.schemas import BookingView
from app.core.errors import DomainError
from app.features.bookings.tokens import _utc


class CalendarBookingMixin:
    def _calendar_connection(self, host_id: str) -> CalendarConnection | None:
        return self.session.scalar(
            select(CalendarConnection)
            .options(selectinload(CalendarConnection.calendars))
            .where(
                CalendarConnection.host_id == host_id,
                CalendarConnection.provider == "google",
            )
        )

    @staticmethod
    def _destination(connection: CalendarConnection) -> ExternalCalendar | None:
        return next(
            (calendar for calendar in connection.calendars if calendar.add_events), None
        )

    def _calendar_failure(
        self, connection: CalendarConnection, error: Exception
    ) -> DomainError:
        if isinstance(
            error, (CalendarConnectionExpired, CalendarConnectionRequiresReconnect)
        ):
            connection.status = "expired"
            self._commit()
            return DomainError("Reconnect Google Calendar to check availability", 503)
        return DomainError(
            str(error)
            if isinstance(error, CalendarError)
            else "Google Calendar action failed",
            502,
        )

    def _external_busy(
        self,
        event: EventType,
        range_start: datetime,
        range_end: datetime,
        exclude_booking_id: str | None,
    ) -> list[BusyInterval]:
        connection = self._calendar_connection(event.host_id)
        if connection is None:
            return []
        checked = [
            calendar for calendar in connection.calendars if calendar.check_conflicts
        ]
        if not checked:
            return []
        if connection.status.lower() != "active":
            raise DomainError("Reconnect Google Calendar to check availability", 503)
        if self.calendar_adapter is None:
            raise DomainError("Google Calendar is not configured", 503)
        excluded_event_id = None
        if exclude_booking_id is not None:
            excluded_booking = self.session.get(Booking, exclude_booking_id)
            excluded_event_id = (
                excluded_booking.external_event_id if excluded_booking else None
            )
        busy: list[BusyInterval] = []
        try:
            for calendar in checked:
                entries = self.calendar_adapter.list_busy_intervals(
                    user_id=event.host_id,
                    connected_account_id=connection.connected_account_id,
                    calendar_id=calendar.provider_calendar_id,
                    time_min=range_start,
                    time_max=range_end,
                )
                for entry in entries:
                    if (
                        entry.get("event_id") == excluded_event_id
                        and excluded_event_id is not None
                    ):
                        continue
                    start_at, end_at = entry.get("start_at"), entry.get("end_at")
                    if not isinstance(start_at, datetime) or not isinstance(
                        end_at, datetime
                    ):
                        raise CalendarError(
                            "Google Calendar returned invalid busy times"
                        )
                    if (
                        start_at.tzinfo is None
                        or end_at.tzinfo is None
                        or end_at <= start_at
                    ):
                        raise CalendarError(
                            "Google Calendar returned invalid busy times"
                        )
                    busy.append(BusyInterval(_utc(start_at), _utc(end_at)))
        except Exception as error:
            raise self._calendar_failure(connection, error) from error
        return busy

    @staticmethod
    def _calendar_event_details(event: EventType, booking: Booking) -> dict:
        return {
            "start_at": _utc(booking.start_at),
            "end_at": _utc(booking.end_at),
            "timezone_name": event.schedule.timezone,
            "summary": event.name,
            "attendees": [booking.invitee_email],
            "description": event.description,
            "location": event.location_value
            if event.location_type in {"in_person", "custom"}
            else None,
        }

    def _sync_booking_calendar(
        self, booking: Booking, event: EventType, *, patch: bool = False
    ) -> None:
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
            booking.external_sync_status = (
                "pending" if operation == "create" else "failed"
            )
            booking.external_sync_error = (
                "Calendar event destination is no longer configured"
            )
            self._commit()
            return
        if connection.status.lower() != "active" or self.calendar_adapter is None:
            booking.external_sync_status = (
                "pending" if operation == "create" else "failed"
            )
            booking.external_sync_error = (
                "Reconnect Google Calendar"
                if connection.status.lower() != "active"
                else "Google Calendar is not configured"
            )
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
                    user_id=event.host_id,
                    connected_account_id=connection.connected_account_id,
                    calendar_id=calendar_id,
                    event_id=booking.external_event_id,
                    **details,
                )
            else:
                result = self.calendar_adapter.create_event(
                    user_id=event.host_id,
                    connected_account_id=connection.connected_account_id,
                    calendar_id=calendar_id,
                    conference_provider="google_meet"
                    if event.location_type == "google_meet"
                    else None,
                    **details,
                )
        except Exception as error:
            self._lock_host(booking.host_id)
            self.session.refresh(booking)
            if isinstance(
                error, (CalendarConnectionExpired, CalendarConnectionRequiresReconnect)
            ):
                connection.status = "expired"
            if booking.external_sync_operation == operation:
                booking.external_sync_status = "failed"
                booking.external_sync_error = (
                    str(error)
                    if isinstance(error, CalendarError)
                    else "Google Calendar action failed"
                )
            self._commit()
            return
        self._lock_host(booking.host_id)
        self.session.refresh(booking)
        if (
            operation == "patch"
            and booking.status == "scheduled"
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
        if (
            connection is None
            or connection.status.lower() != "active"
            or self.calendar_adapter is None
        ):
            booking.external_sync_status = "failed"
            booking.external_sync_error = (
                "Reconnect Google Calendar before deleting the event"
            )
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
                user_id=booking.host_id,
                connected_account_id=connection.connected_account_id,
                calendar_id=calendar_id,
                event_id=booking.external_event_id,
            )
        except Exception as error:
            self._lock_host(booking.host_id)
            self.session.refresh(booking)
            if isinstance(
                error, (CalendarConnectionExpired, CalendarConnectionRequiresReconnect)
            ):
                connection.status = "expired"
            if booking.external_sync_operation == "delete":
                booking.external_sync_status = "failed"
                booking.external_sync_error = (
                    str(error)
                    if isinstance(error, CalendarError)
                    else "Google Calendar action failed"
                )
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

    def retry_calendar_sync(self, host_id: str, booking_id: str) -> BookingView:
        booking = self.session.scalar(
            select(Booking).where(Booking.id == booking_id, Booking.host_id == host_id)
        )
        if booking is None:
            raise DomainError("Booking not found", 404)
        if booking.external_sync_status not in {"pending", "inflight", "failed"}:
            raise DomainError("Booking calendar sync is not pending or failed")
        if (
            booking.external_sync_operation == "create"
            and booking.external_sync_status != "pending"
        ):
            raise DomainError(
                "Check Google Calendar for this booking, then reconcile the calendar create outcome",
                409,
            )
        if booking.status == "canceled":
            self._delete_booking_calendar(booking)
        else:
            event = self.session.scalar(
                select(EventType)
                .options(selectinload(EventType.schedule))
                .where(EventType.id == booking.event_type_id)
            )
            if event is None:
                raise DomainError("Event type not found", 404)
            self._sync_booking_calendar(
                booking, event, patch=bool(booking.external_event_id)
            )
        return BookingView.model_validate(booking)

    def reconcile_calendar_create(
        self,
        host_id: str,
        booking_id: str,
        *,
        confirmed_absent: bool = False,
        reconciled_event_id: str | None = None,
    ) -> BookingView:
        if confirmed_absent == bool(reconciled_event_id):
            raise DomainError(
                "Confirm the event is absent or provide its Google Calendar event ID"
            )
        booking = self.session.scalar(
            select(Booking).where(Booking.id == booking_id, Booking.host_id == host_id)
        )
        if booking is None:
            raise DomainError("Booking not found", 404)
        self._lock_host(host_id)
        self.session.refresh(booking)
        if (
            booking.external_sync_operation != "create"
            or booking.external_sync_status not in {"inflight", "failed"}
        ):
            self.session.rollback()
            raise DomainError(
                "Booking calendar create does not need reconciliation", 409
            )
        if confirmed_absent and booking.external_sync_status == "inflight":
            self.session.rollback()
            raise DomainError(
                "Calendar create may still be running; quiesce workers and repair the pending operation before retrying",
                409,
            )
        if reconciled_event_id is not None:
            reconciled_event_id = reconciled_event_id.strip()
            if not reconciled_event_id or len(reconciled_event_id) > 200:
                self.session.rollback()
                raise DomainError("Google Calendar event ID is invalid")
            booking.external_event_id = reconciled_event_id
            booking.external_sync_operation = (
                "delete" if booking.status == "canceled" else "patch"
            )
            booking.external_sync_status = "pending"
            booking.external_sync_error = None
            self._commit()
            if booking.status == "canceled":
                self._delete_booking_calendar(booking)
            else:
                event = self.session.scalar(
                    select(EventType)
                    .options(selectinload(EventType.schedule))
                    .where(EventType.id == booking.event_type_id)
                )
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
            event = self.session.scalar(
                select(EventType)
                .options(selectinload(EventType.schedule))
                .where(EventType.id == booking.event_type_id)
            )
            if event is None:
                raise DomainError("Event type not found", 404)
            self._sync_booking_calendar(booking, event)
        return BookingView.model_validate(booking)
