"""Host meeting list and CSV projection."""

from __future__ import annotations
import csv
import io
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.features.bookings.models import Booking
from app.features.event_types.models import EventType
from app.features.hosts.models import Host


def meeting_rows(
    session: Session,
    host_id: str,
    *,
    event_type_id: str | None = None,
    range_start: datetime | None = None,
    range_end: datetime | None = None,
    status: str | None = None,
) -> list[tuple[Booking, EventType]]:
    query = (
        select(Booking, EventType)
        .join(EventType, Booking.event_type_id == EventType.id)
        .where(Booking.host_id == host_id)
    )
    if event_type_id:
        query = query.where(Booking.event_type_id == event_type_id)
    if status:
        query = query.where(Booking.status == status)
    if range_start:
        query = query.where(Booking.start_at >= range_start)
    if range_end:
        query = query.where(Booking.start_at <= range_end)
    return list(session.execute(query.order_by(Booking.start_at.asc())).all())


def meeting_view(booking: Booking, event: EventType, host: Host) -> dict:
    return {
        "id": booking.id,
        "event_type_id": event.id,
        "event_type_name": event.name,
        "event_type_slug": event.slug,
        "event_type_description": event.description,
        "event_type_location_type": event.location_type,
        "event_type_location_value": event.location_value,
        "host_timezone": host.timezone,
        "profile_display_name": host.display_name,
        "profile_username": host.username,
        "start_at": booking.start_at,
        "end_at": booking.end_at,
        "status": booking.status,
        "invitee_name": booking.invitee_name,
        "invitee_email": booking.invitee_email,
        "invitee_phone": booking.invitee_phone,
        "invitee_timezone": booking.invitee_timezone,
        "external_event_id": booking.external_event_id,
        "external_sync_status": booking.external_sync_status,
        "external_sync_error": booking.external_sync_error,
        "meeting_join_url": booking.meeting_join_url,
    }


def _csv_safe(value: str) -> str:
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def meetings_csv(rows: list[tuple[Booking, EventType]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        (
            "Start",
            "End",
            "Status",
            "Event Type",
            "Invitee Name",
            "Invitee Email",
            "Calendar Sync",
        )
    )
    for booking, event in rows:
        writer.writerow(
            [
                _csv_safe(value)
                for value in (
                    booking.start_at.isoformat(),
                    booking.end_at.isoformat(),
                    booking.status,
                    event.name,
                    booking.invitee_name,
                    booking.invitee_email,
                    booking.external_sync_status,
                )
            ]
        )
    return output.getvalue().removesuffix("\n")
