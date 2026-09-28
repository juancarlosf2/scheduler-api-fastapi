"""Compatibility imports; implementation lives in features.calendars.connection_api."""
from app.features.calendars.connection_api import router, get_calendar_adapter, get_calendar_service, get_calendar_settings, create_google_calendar_connect_link, sync_google_calendars, update_external_calendar_preferences, ConnectLinkInput, ConnectLinkView, CalendarPreferencesInput, ExternalCalendarView, CalendarSettingsView

__all__ = ['router', 'get_calendar_adapter', 'get_calendar_service', 'get_calendar_settings', 'create_google_calendar_connect_link', 'sync_google_calendars', 'update_external_calendar_preferences', 'ConnectLinkInput', 'ConnectLinkView', 'CalendarPreferencesInput', 'ExternalCalendarView', 'CalendarSettingsView']
