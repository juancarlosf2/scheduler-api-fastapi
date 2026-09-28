"""Composed scheduling service; cross-feature booking transaction boundary."""

from __future__ import annotations
from datetime import datetime
from typing import Callable
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.features.availability.slots import BusyInterval
from app.features.calendars.ports import CalendarPort
from app.features.event_types.models import EventType
from app.core.errors import DomainError
from app.features.calendars.booking_operations import CalendarBookingMixin
from app.features.hosts.service import HostMixin
from app.features.availability.service import AvailabilityMixin
from app.features.event_types.service import EventTypeMixin
from app.features.profiles.service import PublicProfileMixin
from app.features.bookings.operations import BookingMixin
from app.features.contacts.service import ContactMixin


class SchedulerService(
    CalendarBookingMixin,
    HostMixin,
    AvailabilityMixin,
    EventTypeMixin,
    PublicProfileMixin,
    BookingMixin,
    ContactMixin,
):
    def __init__(
        self,
        session: Session,
        busy_provider: Callable[[EventType, datetime, datetime], list[BusyInterval]]
        | None = None,
        calendar_adapter: CalendarPort | None = None,
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
