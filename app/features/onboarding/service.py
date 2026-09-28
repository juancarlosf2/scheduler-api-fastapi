"""Host setup and guide state."""

from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.features.bookings.models import Booking
from app.features.contacts.models import Contact
from app.features.event_types.models import EventType
from app.features.hosts.models import Host
from app.features.availability.models import Schedule
from app.core.model import utc_now
from app.core.errors import DomainError

from app.features.profiles.service import workspace_for
from app.features.onboarding.models import HostGuideState
from app.features.workflows.models import Workflow
from app.features.bookings.service import SchedulerService

STEPS = ("role", "calendar-usage", "availability", "meeting-location")
TASKS = ("get-to-know", "perfect-setup", "automate-follow-up", "copy-booking-link")
DISMISSALS = ("get-started-panel", "first-event-helper")


def setup_status(session: Session, host: Host) -> dict:
    workspace = workspace_for(session, host.id)
    has_availability = (
        session.scalar(select(Schedule.id).where(Schedule.host_id == host.id))
        is not None
    )
    has_event = (
        session.scalar(
            select(EventType.id).where(
                EventType.host_id == host.id, EventType.status == "active"
            )
        )
        is not None
    )
    has_meetings = (
        session.scalar(select(Booking.id).where(Booking.host_id == host.id).limit(1))
        is not None
    )
    has_contacts = (
        session.scalar(select(Contact.id).where(Contact.host_id == host.id).limit(1))
        is not None
    )
    has_workflows = (
        session.scalar(select(Workflow.id).where(Workflow.host_id == host.id).limit(1))
        is not None
    )
    location = workspace.preferred_location_type
    meeting_ready = (
        location == "phone_host"
        and bool(workspace.phone_number)
        or location == "in_person"
        and bool(workspace.preferred_location_value)
    )
    states = (
        "complete" if workspace.role else "incomplete",
        "skipped" if workspace.calendar_connection_skipped else "incomplete",
        "complete" if has_availability else "incomplete",
        "complete" if meeting_ready else "incomplete",
    )
    return {
        "completed_at": workspace.completed_at,
        "current_step_id": workspace.current_step_id,
        "is_core_complete": all(state != "incomplete" for state in states),
        "preferences": {
            "role": workspace.role,
            "preferred_location_type": location,
            "preferred_location_value": workspace.preferred_location_value,
        },
        "readiness": {
            "calendar_connection_skipped": workspace.calendar_connection_skipped,
            "has_calendar_destination": False,
            "has_contacts": has_contacts,
            "has_default_availability_schedule": has_availability,
            "has_host_phone_number": bool(workspace.phone_number),
            "has_meetings": has_meetings,
            "has_profile_basics": bool(host.display_name and host.username),
            "has_public_event_type": has_event,
            "has_workflows": has_workflows,
            "host_phone_number": workspace.phone_number,
            "public_booking_url": f"/book/{host.username}" if has_event else None,
            "time_format": workspace.time_format,
        },
        "steps": [{"id": step, "state": state} for step, state in zip(STEPS, states)],
    }


def guide_status(session: Session, host: Host) -> dict:
    setup = setup_status(session, host)
    states = list(
        session.scalars(select(HostGuideState).where(HostGuideState.host_id == host.id))
    )
    completed = [
        item
        for item in TASKS
        if any(row.item_id == item and row.completed_at for row in states)
    ]
    dismissed = [
        item
        for item in DISMISSALS
        if any(row.item_id == item and row.dismissed_at for row in states)
    ]
    event = session.scalar(
        select(EventType)
        .where(EventType.host_id == host.id, EventType.status == "active")
        .order_by(EventType.created_at.asc())
        .limit(1)
    )
    complete = setup["completed_at"] is not None
    return {
        "completed_task_ids": completed,
        "dismissed_item_ids": dismissed,
        "first_event": {
            "id": event.id,
            "name": event.name,
            "duration_minutes": event.duration_minutes,
            "public_path": f"/book/{host.username}/{event.slug}",
        }
        if event
        else None,
        "is_host_setup_complete": complete,
        "should_show_first_event_helper": complete
        and event is not None
        and "first-event-helper" not in dismissed,
        "should_show_get_started": complete
        and not all(
            item in completed
            for item in ("get-to-know", "perfect-setup", "copy-booking-link")
        )
        and "get-started-panel" not in dismissed,
    }


def mark_guide_item(session: Session, host: Host, item_id: str, kind: str) -> dict:
    state = session.scalar(
        select(HostGuideState).where(
            HostGuideState.host_id == host.id, HostGuideState.item_id == item_id
        )
    )
    if state is None:
        state = HostGuideState(host_id=host.id, item_id=item_id)
        session.add(state)
    setattr(state, kind, utc_now())
    session.commit()
    return guide_status(session, host)


def complete_setup(session: Session, host: Host, service: SchedulerService) -> dict:
    status = setup_status(session, host)
    if not status["is_core_complete"]:
        raise DomainError(
            "Complete or skip the required setup steps before finishing onboarding"
        )
    workspace = workspace_for(session, host.id)
    event = session.scalar(
        select(EventType)
        .where(EventType.host_id == host.id, EventType.status == "active")
        .limit(1)
    )
    if event is None:
        from app.schemas import EventTypeInput

        names = {
            "sales": "Discovery Call",
            "recruiting": "Candidate Interview",
            "consulting": "Consultation",
            "education": "Advising Session",
            "finance": "Financial Review",
            "marketing": "Campaign Sync",
            "customer-success": "Customer Check-in",
            "other": "30 Minute Meeting",
        }
        event = service.create_event_type(
            host.id,
            EventTypeInput(
                name=names[workspace.role or "other"],
                status="active",
                location_type=workspace.preferred_location_type or "none",
                location_value=workspace.phone_number
                if workspace.preferred_location_type == "phone_host"
                else workspace.preferred_location_value,
            ),
        )
    host.is_public = True
    workspace.completed_at = workspace.completed_at or utc_now()
    session.commit()
    return setup_status(session, host)
