"""Event Types scheduling operations."""

from __future__ import annotations
import re
from sqlalchemy import select
from app.features.event_types.models import EventType
from app.features.event_types.models import InviteeField
from app.features.event_types.schemas import EventTypeInput
from app.core.errors import DomainError


def _slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80].strip("-")
    return result or "event"


class EventTypeMixin:
    def _event_for_host(self, host_id: str, event_id: str) -> EventType:
        event = self.session.scalar(
            select(EventType).where(
                EventType.id == event_id, EventType.host_id == host_id
            )
        )
        if event is None:
            raise DomainError("Event type not found", 404)
        return event

    def list_event_types(self, host_id: str) -> list[EventType]:
        self.get_host(host_id)
        return list(
            self.session.scalars(
                select(EventType)
                .where(EventType.host_id == host_id)
                .order_by(EventType.created_at.desc())
            )
        )

    def _available_slug(self, host_id: str, requested: str) -> str:
        base = _slug(requested)
        for attempt in range(100):
            candidate = base if attempt == 0 else f"{base[:75]}-{attempt + 1}"
            if (
                self.session.scalar(
                    select(EventType.id).where(
                        EventType.host_id == host_id, EventType.slug == candidate
                    )
                )
                is None
            ):
                return candidate
        raise DomainError("Could not find an available event slug", 409)

    def _apply_event_input(
        self, event: EventType, data: EventTypeInput, *, create: bool
    ) -> None:
        name = data.name.strip()
        if not name:
            raise DomainError("Event type name is required")
        slug = _slug(data.slug or name)
        if create:
            slug = self._available_slug(event.host_id, slug)
        elif self.session.scalar(
            select(EventType.id).where(
                EventType.host_id == event.host_id,
                EventType.slug == slug,
                EventType.id != event.id,
            )
        ):
            raise DomainError("An event type with this slug already exists", 409)
        event.name = name
        event.slug = slug
        event.color = data.color.strip() or "#2563eb"
        event.description = (
            data.description.strip() or None if data.description else None
        )
        event.duration_minutes = data.duration_minutes
        event.location_type = (
            "google_meet" if data.location_type == "video" else data.location_type
        )
        event.location_value = (
            data.location_value.strip() or None if data.location_value else None
        )
        event.booking_window_days = data.booking_window_days
        event.minimum_notice_minutes = data.minimum_notice_minutes
        event.slot_interval_minutes = data.slot_interval_minutes
        event.confirmation_message = (
            data.confirmation_message.strip() or None
            if data.confirmation_message
            else None
        )
        event.status = data.status

    def create_event_type(self, host_id: str, data: EventTypeInput) -> EventType:
        self.get_host(host_id)
        schedule = self._schedule(host_id)
        event = EventType(host_id=host_id, schedule_id=schedule.id, name="", slug="")
        self._apply_event_input(event, data, create=True)
        event.invitee_fields = [
            InviteeField(label="Name", field_type="text", is_required=True, position=0),
            InviteeField(
                label="Email", field_type="email", is_required=True, position=1
            ),
            InviteeField(
                label="Preparation notes",
                field_type="textarea",
                is_required=False,
                position=2,
            ),
        ]
        if event.status == "active":
            self._assert_publishable(event)
            self.get_host(host_id).is_public = True
        self.session.add(event)
        self._commit()
        return event

    def create_draft(self, host_id: str) -> EventType:
        return self.create_event_type(host_id, EventTypeInput(name="New Meeting"))

    def update_event_type(
        self, host_id: str, event_id: str, data: EventTypeInput
    ) -> EventType:
        event = self._event_for_host(host_id, event_id)
        self._apply_event_input(event, data, create=False)
        if event.status == "active":
            self._assert_publishable(event)
            self.get_host(host_id).is_public = True
        self._commit()
        return event

    def _assert_publishable(self, event: EventType) -> None:
        if not event.name.strip() or not event.slug.strip() or not event.schedule_id:
            raise DomainError(
                "Name, slug, and availability are required before publishing"
            )
        if event.location_type == "none":
            raise DomainError("Add a location before publishing")
        if event.location_type == "google_meet":
            connection = self._calendar_connection(event.host_id)
            if (
                connection is None
                or connection.status.lower() != "active"
                or self._destination(connection) is None
            ):
                raise DomainError(
                    "Connect Google Calendar and select a destination calendar before publishing Google Meet event types"
                )
        if (
            event.location_type in {"phone_host", "in_person", "custom"}
            and not (event.location_value or "").strip()
        ):
            raise DomainError("Add location details before publishing")

    def publish_event_type(self, host_id: str, event_id: str) -> EventType:
        event = self._event_for_host(host_id, event_id)
        self._assert_publishable(event)
        event.status = "active"
        self.get_host(host_id).is_public = True
        self._commit()
        return event

    def set_event_status(self, host_id: str, event_id: str, status: str) -> EventType:
        if status not in {"draft", "active"}:
            raise DomainError("Invalid event type status")
        event = self._event_for_host(host_id, event_id)
        if status == "active":
            return self.publish_event_type(host_id, event_id)
        event.status = "draft"
        self._commit()
        return event

    def duplicate_event_type(self, host_id: str, event_id: str) -> EventType:
        existing = self._event_for_host(host_id, event_id)
        data = EventTypeInput(
            name=f"{existing.name} Copy"[:120],
            slug=f"{existing.slug}-copy",
            color=existing.color,
            description=existing.description,
            duration_minutes=existing.duration_minutes,
            location_type=existing.location_type,
            location_value=existing.location_value,
            booking_window_days=existing.booking_window_days,
            minimum_notice_minutes=existing.minimum_notice_minutes,
            slot_interval_minutes=existing.slot_interval_minutes,
            confirmation_message=existing.confirmation_message,
        )
        return self.create_event_type(host_id, data)

    def delete_event_type(self, host_id: str, event_id: str) -> None:
        event = self._event_for_host(host_id, event_id)
        self.session.delete(event)
        self._commit()
