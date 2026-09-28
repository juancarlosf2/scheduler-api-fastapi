"""Compatibility exports for feature-owned SQLAlchemy tables."""
from app.core.model import Base, new_id, utc_now
from app.features.hosts.models import Host
from app.features.calendars.models import CalendarConnection, ExternalCalendar
from app.features.availability.models import Schedule, AvailabilityInterval
from app.features.event_types.models import EventType, InviteeField
from app.features.bookings.models import Booking, BookingAnswer
from app.features.contacts.models import Contact

__all__ = ['Base', 'new_id', 'utc_now', 'Host', 'CalendarConnection', 'ExternalCalendar', 'Schedule', 'AvailabilityInterval', 'EventType', 'InviteeField', 'Booking', 'BookingAnswer', 'Contact']
