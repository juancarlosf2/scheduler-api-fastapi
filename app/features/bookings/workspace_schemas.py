"""Bookings host workspace HTTP schemas."""

from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel


class MeetingView(BaseModel):
    id: str
    event_type_id: str
    event_type_name: str
    event_type_slug: str
    event_type_description: str | None
    event_type_location_type: str
    event_type_location_value: str | None
    host_timezone: str
    profile_display_name: str
    profile_username: str
    start_at: datetime
    end_at: datetime
    status: str
    invitee_name: str
    invitee_email: str
    invitee_phone: str | None
    invitee_timezone: str
    external_event_id: str | None
    external_sync_status: str
    external_sync_error: str | None
    meeting_join_url: str | None
