"""Availability scheduling operations."""

from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from app.features.availability.slots import (
    AvailabilityError,
    BusyInterval,
    generate_slots,
    validate_intervals,
)
from app.features.availability.models import AvailabilityInterval
from app.features.bookings.models import Booking
from app.features.event_types.models import EventType
from app.features.availability.models import Schedule
from app.features.availability.schemas import AvailabilityInput
from app.features.availability.schemas import IntervalInput
from app.features.availability.schemas import Slot
from app.core.errors import DomainError
from app.features.bookings.tokens import _utc


class AvailabilityMixin:
    def _schedule(self, host_id: str) -> Schedule:
        self.get_host(host_id)
        schedule = self.session.scalar(
            select(Schedule)
            .options(selectinload(Schedule.intervals))
            .where(Schedule.host_id == host_id)
        )
        if schedule is None:
            raise DomainError("Availability schedule not found", 404)
        return schedule

    def get_availability(self, host_id: str) -> dict:
        schedule = self._schedule(host_id)
        return {
            "id": schedule.id,
            "host_id": schedule.host_id,
            "name": schedule.name,
            "timezone": schedule.timezone,
            "intervals": [
                {
                    "id": i.id,
                    "weekday": i.weekday,
                    "date": i.date,
                    "start_minute": i.start_minute,
                    "end_minute": i.end_minute,
                    "is_available": i.is_available,
                }
                for i in schedule.intervals
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
        available_weekdays = [
            i.weekday
            for i in data.intervals
            if i.weekday is not None and i.is_available
        ]
        if len(available_weekdays) != len(set(available_weekdays)):
            raise DomainError(
                "Availability editor supports one available interval per weekday"
            )
        schedule = self._schedule(host_id)
        schedule.name = data.name.strip()
        schedule.timezone = data.timezone
        schedule.intervals = [
            AvailabilityInterval(**interval.model_dump()) for interval in data.intervals
        ]
        self._commit()
        return self.get_availability(host_id)

    def _slots_for_event(
        self,
        event: EventType,
        start_date: date,
        end_date: date,
        now: datetime,
        exclude_booking_id: str | None = None,
    ) -> list[Slot]:
        if (end_date - start_date).days > 366:
            raise DomainError("Date range is too large")
        # Query a wide UTC envelope because the requested days are local to the schedule.
        range_start = datetime.combine(
            start_date - timedelta(days=1), datetime.min.time(), timezone.utc
        )
        range_end = datetime.combine(
            end_date + timedelta(days=2), datetime.min.time(), timezone.utc
        )
        stmt = select(Booking).where(
            Booking.host_id == event.host_id,
            Booking.status == "scheduled",
            Booking.start_at < range_end,
            Booking.end_at > range_start,
        )
        if exclude_booking_id:
            stmt = stmt.where(Booking.id != exclude_booking_id)
        busy = [
            BusyInterval(_utc(b.start_at), _utc(b.end_at))
            for b in self.session.scalars(stmt)
        ]
        busy.extend(
            self._external_busy(event, range_start, range_end, exclude_booking_id)
        )
        if self.busy_provider is not None:
            busy.extend(self.busy_provider(event, range_start, range_end))
        intervals = [
            IntervalInput(
                weekday=i.weekday,
                date=i.date,
                start_minute=i.start_minute,
                end_minute=i.end_minute,
                is_available=i.is_available,
            )
            for i in event.schedule.intervals
        ]
        try:
            return generate_slots(
                now=now,
                range_start_date=start_date,
                range_end_date=end_date,
                schedule_timezone=event.schedule.timezone,
                duration_minutes=event.duration_minutes,
                slot_interval_minutes=event.slot_interval_minutes,
                minimum_notice_minutes=max(120, event.minimum_notice_minutes),
                booking_window_days=event.booking_window_days,
                intervals=intervals,
                busy_intervals=busy,
            )
        except AvailabilityError as error:
            raise DomainError(str(error)) from error

    def get_slots(
        self,
        username: str,
        slug: str,
        start_date: date,
        end_date: date,
        now: datetime | None = None,
    ) -> list[Slot]:
        event = self._public_event(username, slug)
        return self._slots_for_event(
            event, start_date, end_date, now or datetime.now(timezone.utc)
        )
