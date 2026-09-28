"""Profiles scheduling operations."""

from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from app.features.event_types.models import EventType
from app.features.hosts.models import Host
from app.features.availability.models import Schedule
from app.features.profiles.models import HostWorkspace
from app.core.errors import DomainError


class PublicProfileMixin:
    def get_public_profile(self, username: str) -> dict:
        host = self.session.scalar(
            select(Host).where(Host.username == username, Host.is_public.is_(True))
        )
        if host is None:
            raise DomainError("Public profile not found", 404)
        events = self.session.scalars(
            select(EventType).where(
                EventType.host_id == host.id, EventType.status == "active"
            )
        )
        return {
            "username": host.username,
            "display_name": host.display_name,
            "welcome_message": host.welcome_message,
            "event_types": [
                {
                    "id": event.id,
                    "name": event.name,
                    "slug": event.slug,
                    "color": event.color,
                    "description": event.description,
                    "duration_minutes": event.duration_minutes,
                    "location_type": event.location_type,
                }
                for event in events
            ],
        }

    def _public_event(self, username: str, slug: str) -> EventType:
        event = self.session.scalar(
            select(EventType)
            .join(Host)
            .options(
                selectinload(EventType.invitee_fields),
                selectinload(EventType.schedule).selectinload(Schedule.intervals),
            )
            .where(
                Host.username == username,
                Host.is_public.is_(True),
                EventType.slug == slug,
                EventType.status == "active",
            )
        )
        if event is None:
            raise DomainError("This booking link is not available", 404)
        return event

    def get_public_event(self, username: str, slug: str) -> dict:
        event = self._public_event(username, slug)
        host = self.get_host(event.host_id)
        return {
            "id": event.id,
            "name": event.name,
            "slug": event.slug,
            "description": event.description,
            "duration_minutes": event.duration_minutes,
            "location_type": event.location_type,
            "location_value": event.location_value,
            "booking_window_days": event.booking_window_days,
            "minimum_notice_minutes": event.minimum_notice_minutes,
            "slot_interval_minutes": event.slot_interval_minutes,
            "confirmation_message": event.confirmation_message,
            "profile": {"username": host.username, "display_name": host.display_name},
            "schedule": {
                "timezone": event.schedule.timezone,
                "intervals": [
                    {
                        "weekday": i.weekday,
                        "date": i.date,
                        "start_minute": i.start_minute,
                        "end_minute": i.end_minute,
                        "is_available": i.is_available,
                    }
                    for i in event.schedule.intervals
                ],
            },
            "invitee_fields": [
                {
                    "id": f.id,
                    "label": f.label,
                    "field_type": f.field_type,
                    "is_required": f.is_required,
                    "position": f.position,
                }
                for f in event.invitee_fields
            ],
        }


def workspace_for(session: Session, host_id: str) -> HostWorkspace:
    workspace = session.get(HostWorkspace, host_id)
    if workspace is None:
        workspace = HostWorkspace(host_id=host_id)
        session.add(workspace)
        session.flush()
    return workspace


def profile_settings(host: Host, workspace: HostWorkspace) -> dict:
    return {
        "country_code": workspace.country_code,
        "date_format": workspace.date_format,
        "email": host.email,
        "language": workspace.language,
        "name": host.display_name,
        "phone_number": workspace.phone_number or "",
        "time_format": workspace.time_format,
        "timezone": host.timezone,
        "username": host.username,
        "welcome_message": host.welcome_message or "",
    }


def update_profile(session: Session, host: Host, values: dict) -> dict:
    workspace = workspace_for(session, host.id)
    host.display_name = values["name"].strip()
    host.timezone = values["timezone"]
    host.welcome_message = values["welcome_message"].strip() or None
    workspace.phone_number = values["phone_number"].strip() or None
    for field in ("country_code", "date_format", "language", "time_format"):
        setattr(workspace, field, values[field])
    schedule = session.scalar(select(Schedule).where(Schedule.host_id == host.id))
    if schedule is not None:
        schedule.timezone = host.timezone
    session.commit()
    return profile_settings(host, workspace)
