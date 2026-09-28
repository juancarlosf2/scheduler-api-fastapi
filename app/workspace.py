"""Host workspace persistence and operations.

Every query uses the authenticated host ID supplied by the route layer. These
tables contain optional workspace state and can be extended independently of
the core booking records.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models import Base, Booking, Contact, EventType, Host, Schedule, new_id, utc_now
from app.service import DomainError, SchedulerService


class HostWorkspace(Base):
    __tablename__ = "host_workspaces"

    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), primary_key=True)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False, default="DO")
    date_format: Mapped[str] = mapped_column(String(12), nullable=False, default="DD/MM/YYYY")
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="en")
    time_format: Mapped[str] = mapped_column(String(3), nullable=False, default="24h")
    phone_number: Mapped[str | None] = mapped_column(String(40))
    current_step_id: Mapped[str] = mapped_column(String(30), nullable=False, default="role")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    role: Mapped[str | None] = mapped_column(String(30))
    preferred_location_type: Mapped[str | None] = mapped_column(String(30))
    preferred_location_value: Mapped[str | None] = mapped_column(String(240))
    calendar_connection_skipped: Mapped[bool] = mapped_column(default=False, nullable=False)


class HostGuideState(Base):
    __tablename__ = "host_guide_states"
    __table_args__ = (UniqueConstraint("host_id", "item_id", name="guide_host_item_unique"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    item_id: Mapped[str] = mapped_column(String(40), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Workflow(Base):
    """Stored workflow definitions; this starter does not send notifications."""

    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint("trigger IN ('before_event_start', 'after_event_end')", name="workflow_trigger"),
        CheckConstraint("status IN ('active', 'inactive')", name="workflow_status"),
        CheckConstraint("offset_minutes >= 0", name="workflow_offset"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False)
    event_type_id: Mapped[str] = mapped_column(ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False)
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email")
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


STEPS = ("role", "calendar-usage", "availability", "meeting-location")
TASKS = ("get-to-know", "perfect-setup", "automate-follow-up", "copy-booking-link")
DISMISSALS = ("get-started-panel", "first-event-helper")


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


def meeting_rows(session: Session, host_id: str, *, event_type_id: str | None = None,
                 range_start: datetime | None = None, range_end: datetime | None = None,
                 status: str | None = None) -> list[tuple[Booking, EventType]]:
    query = select(Booking, EventType).join(EventType, Booking.event_type_id == EventType.id).where(Booking.host_id == host_id)
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
        "id": booking.id, "event_type_id": event.id, "event_type_name": event.name,
        "event_type_slug": event.slug, "event_type_description": event.description,
        "event_type_location_type": event.location_type,
        "event_type_location_value": event.location_value,
        "host_timezone": host.timezone, "profile_display_name": host.display_name,
        "profile_username": host.username, "start_at": booking.start_at, "end_at": booking.end_at,
        "status": booking.status, "invitee_name": booking.invitee_name,
        "invitee_email": booking.invitee_email, "invitee_phone": booking.invitee_phone,
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
    writer.writerow(("Start", "End", "Status", "Event Type", "Invitee Name", "Invitee Email", "Calendar Sync"))
    for booking, event in rows:
        writer.writerow([_csv_safe(value) for value in (
            booking.start_at.isoformat(), booking.end_at.isoformat(), booking.status,
            event.name, booking.invitee_name, booking.invitee_email, booking.external_sync_status,
        )])
    return output.getvalue().removesuffix("\n")


def update_contact_notes(session: Session, host_id: str, contact_id: str, notes: str | None) -> Contact:
    contact = session.scalar(select(Contact).where(Contact.id == contact_id, Contact.host_id == host_id))
    if contact is None:
        raise DomainError("Contact not found", 404)
    contact.notes = (notes or "").strip() or None
    session.commit()
    return contact


def setup_status(session: Session, host: Host) -> dict:
    workspace = workspace_for(session, host.id)
    has_availability = session.scalar(select(Schedule.id).where(Schedule.host_id == host.id)) is not None
    has_event = session.scalar(select(EventType.id).where(EventType.host_id == host.id, EventType.status == "active")) is not None
    has_meetings = session.scalar(select(Booking.id).where(Booking.host_id == host.id).limit(1)) is not None
    has_contacts = session.scalar(select(Contact.id).where(Contact.host_id == host.id).limit(1)) is not None
    has_workflows = session.scalar(select(Workflow.id).where(Workflow.host_id == host.id).limit(1)) is not None
    location = workspace.preferred_location_type
    meeting_ready = location == "phone_host" and bool(workspace.phone_number) or location == "in_person" and bool(workspace.preferred_location_value)
    states = ("complete" if workspace.role else "incomplete",
              "skipped" if workspace.calendar_connection_skipped else "incomplete",
              "complete" if has_availability else "incomplete",
              "complete" if meeting_ready else "incomplete")
    return {
        "completed_at": workspace.completed_at,
        "current_step_id": workspace.current_step_id,
        "is_core_complete": all(state != "incomplete" for state in states),
        "preferences": {"role": workspace.role, "preferred_location_type": location,
                        "preferred_location_value": workspace.preferred_location_value},
        "readiness": {
            "calendar_connection_skipped": workspace.calendar_connection_skipped,
            "has_calendar_destination": False, "has_contacts": has_contacts,
            "has_default_availability_schedule": has_availability,
            "has_host_phone_number": bool(workspace.phone_number), "has_meetings": has_meetings,
            "has_profile_basics": bool(host.display_name and host.username),
            "has_public_event_type": has_event, "has_workflows": has_workflows,
            "host_phone_number": workspace.phone_number,
            "public_booking_url": f"/book/{host.username}" if has_event else None,
            "time_format": workspace.time_format,
        },
        "steps": [{"id": step, "state": state} for step, state in zip(STEPS, states)],
    }


def guide_status(session: Session, host: Host) -> dict:
    setup = setup_status(session, host)
    states = list(session.scalars(select(HostGuideState).where(HostGuideState.host_id == host.id)))
    completed = [item for item in TASKS if any(row.item_id == item and row.completed_at for row in states)]
    dismissed = [item for item in DISMISSALS if any(row.item_id == item and row.dismissed_at for row in states)]
    event = session.scalar(select(EventType).where(EventType.host_id == host.id, EventType.status == "active")
                           .order_by(EventType.created_at.asc()).limit(1))
    complete = setup["completed_at"] is not None
    return {
        "completed_task_ids": completed, "dismissed_item_ids": dismissed,
        "first_event": {"id": event.id, "name": event.name, "duration_minutes": event.duration_minutes,
                        "public_path": f"/book/{host.username}/{event.slug}"} if event else None,
        "is_host_setup_complete": complete,
        "should_show_first_event_helper": complete and event is not None and "first-event-helper" not in dismissed,
        "should_show_get_started": complete and not all(item in completed for item in ("get-to-know", "perfect-setup", "copy-booking-link")) and "get-started-panel" not in dismissed,
    }


def mark_guide_item(session: Session, host: Host, item_id: str, kind: str) -> dict:
    state = session.scalar(select(HostGuideState).where(HostGuideState.host_id == host.id, HostGuideState.item_id == item_id))
    if state is None:
        state = HostGuideState(host_id=host.id, item_id=item_id)
        session.add(state)
    setattr(state, kind, utc_now())
    session.commit()
    return guide_status(session, host)


def complete_setup(session: Session, host: Host, service: SchedulerService) -> dict:
    status = setup_status(session, host)
    if not status["is_core_complete"]:
        raise DomainError("Complete or skip the required setup steps before finishing onboarding")
    workspace = workspace_for(session, host.id)
    event = session.scalar(select(EventType).where(EventType.host_id == host.id, EventType.status == "active").limit(1))
    if event is None:
        from app.schemas import EventTypeInput
        names = {"sales": "Discovery Call", "recruiting": "Candidate Interview", "consulting": "Consultation",
                 "education": "Advising Session", "finance": "Financial Review", "marketing": "Campaign Sync",
                 "customer-success": "Customer Check-in", "other": "30 Minute Meeting"}
        event = service.create_event_type(host.id, EventTypeInput(
            name=names[workspace.role or "other"], status="active",
            location_type=workspace.preferred_location_type or "none",
            location_value=workspace.phone_number if workspace.preferred_location_type == "phone_host" else workspace.preferred_location_value,
        ))
    host.is_public = True
    workspace.completed_at = workspace.completed_at or utc_now()
    session.commit()
    return setup_status(session, host)
