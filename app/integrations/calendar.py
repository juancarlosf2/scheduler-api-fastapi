"""Compatibility imports; implementation lives in features.calendars.providers.composio."""
from app.features.calendars.providers.composio import GOOGLE_CALENDAR_TOOLKIT_VERSION, CalendarError, CalendarConnectionExpired, CalendarConnectionRequiresReconnect, GoogleCalendarAdapter

__all__ = ['GOOGLE_CALENDAR_TOOLKIT_VERSION', 'CalendarError', 'CalendarConnectionExpired', 'CalendarConnectionRequiresReconnect', 'GoogleCalendarAdapter']
