"""Bookings host workspace endpoints."""

from __future__ import annotations
import os
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_host, get_session
from app.core.errors import DomainError
from app.features.calendars.providers.composio import (
    CalendarError,
    GoogleCalendarAdapter,
)
from app.features.notifications.lifecycle import deliver_booking_notifications
from app.features.bookings.models import Booking
from app.features.event_types.models import EventType
from app.features.hosts.models import Host
from app.features.bookings.service import SchedulerService
from app.features.bookings.host_workspace import (
    meeting_rows,
    meeting_view,
    meetings_csv,
)
from app.features.bookings.workspace_schemas import MeetingView

router = APIRouter(prefix="/v1/hosts/me", tags=["host workspace"])


def _meeting_filters(
    event_type_id: UUID | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    status: str | None = None,
) -> dict:
    if range_start and (range_start.tzinfo is None or range_start.utcoffset() is None):
        raise DomainError("range_start must include a timezone offset")
    if range_end and (range_end.tzinfo is None or range_end.utcoffset() is None):
        raise DomainError("range_end must include a timezone offset")
    if range_start and range_end and range_start > range_end:
        raise DomainError("range_start must be before range_end")
    return {
        "event_type_id": str(event_type_id) if event_type_id else None,
        "range_start": range_start,
        "range_end": range_end,
        "status": status,
    }


@router.get("/meetings", response_model=list[MeetingView])
def list_meetings(
    event_type_id: UUID | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    status: str | None = None,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    filters = _meeting_filters(event_type_id, range_start, range_end, status)
    return [
        meeting_view(booking, event, host)
        for booking, event in meeting_rows(session, host.id, **filters)
    ]


@router.get(
    "/meetings.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_meetings(
    event_type_id: UUID | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    status: str | None = None,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    filters = _meeting_filters(event_type_id, range_start, range_end, status)
    return Response(
        meetings_csv(meeting_rows(session, host.id, **filters)),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="meetings.csv"'},
    )


@router.post("/meetings/{booking_id}/cancel", response_model=MeetingView)
def cancel_meeting(
    booking_id: UUID,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    adapter = None
    if os.getenv("COMPOSIO_API_KEY"):
        try:
            adapter = GoogleCalendarAdapter()
        except CalendarError:
            raise DomainError("Google Calendar is not configured", 503) from None
    service = SchedulerService(session, calendar_adapter=adapter)
    service.cancel_booking_as_host(host.id, str(booking_id))
    deliver_booking_notifications(session, str(booking_id))
    booking, event = session.execute(
        select(Booking, EventType)
        .join(EventType, Booking.event_type_id == EventType.id)
        .where(Booking.id == str(booking_id), Booking.host_id == host.id)
    ).one()
    return meeting_view(booking, event, host)
