"""Authenticated host workspace routes.

The Bearer key selects the host; request bodies never accept a host ID.
"""

from __future__ import annotations

from datetime import datetime
import os
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.dependencies import get_current_host, get_session
from app.integrations.calendar import CalendarError, GoogleCalendarAdapter
from app.lifecycle import deliver_booking_notifications
from app.models import Booking, Contact, EventType, Host
from app.schemas import valid_timezone
from app.service import DomainError, SchedulerService
from app.workspace import (
    DISMISSALS, TASKS, Workflow, complete_setup,
    guide_status, mark_guide_item, meeting_rows, meeting_view, meetings_csv,
    profile_settings, setup_status, update_contact_notes, update_profile,
    workspace_for,
)


router = APIRouter(prefix="/v1/hosts/me", tags=["host workspace"])


class ProfileSettingsInput(BaseModel):
    country_code: Literal["DO", "US", "CA", "GB"]
    date_format: Literal["DD/MM/YYYY", "MM/DD/YYYY", "YYYY-MM-DD"]
    language: Literal["en"]
    name: str = Field(min_length=1, max_length=120)
    phone_number: str = Field(max_length=40)
    time_format: Literal["24h", "12h"]
    timezone: str = Field(min_length=1, max_length=120)
    welcome_message: str = Field(max_length=500)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name is required")
        return value

    @field_validator("timezone")
    @classmethod
    def timezone_is_valid(cls, value: str) -> str:
        return valid_timezone(value)


class ProfileSettingsView(ProfileSettingsInput):
    email: str
    username: str


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


class ContactNotesInput(BaseModel):
    notes: str | None = Field(max_length=2000)


class ContactView(BaseModel):
    id: str
    host_id: str
    name: str
    email: str
    notes: str | None


class StepInput(BaseModel):
    current_step_id: Literal["role", "calendar-usage", "availability", "meeting-location"]


class SkipStepInput(BaseModel):
    step_id: Literal["role", "calendar-usage", "availability", "meeting-location"]


class RoleInput(BaseModel):
    role: Literal["sales", "recruiting", "customer-success", "marketing", "consulting", "education", "finance", "other"]


class LocationInput(BaseModel):
    phone_number: str | None = Field(default=None, max_length=40)
    preferred_location_type: Literal["google_meet", "phone_host", "in_person"]
    preferred_location_value: str | None = Field(default=None, max_length=240)
    time_format: Literal["24h", "12h"]


class GuideItemInput(BaseModel):
    item_id: str


class SetupStepView(BaseModel):
    id: str
    state: Literal["complete", "incomplete", "skipped"]


class SetupPreferencesView(BaseModel):
    role: str | None
    preferred_location_type: str | None
    preferred_location_value: str | None


class SetupReadinessView(BaseModel):
    calendar_connection_skipped: bool
    has_calendar_destination: bool
    has_contacts: bool
    has_default_availability_schedule: bool
    has_host_phone_number: bool
    has_meetings: bool
    has_profile_basics: bool
    has_public_event_type: bool
    has_workflows: bool
    host_phone_number: str | None
    public_booking_url: str | None
    time_format: Literal["24h", "12h"]


class SetupView(BaseModel):
    completed_at: datetime | None
    current_step_id: str
    is_core_complete: bool
    preferences: SetupPreferencesView
    readiness: SetupReadinessView
    steps: list[SetupStepView]


class FirstEventView(BaseModel):
    id: str
    name: str
    duration_minutes: int
    public_path: str


class GuideView(BaseModel):
    completed_task_ids: list[str]
    dismissed_item_ids: list[str]
    first_event: FirstEventView | None
    is_host_setup_complete: bool
    should_show_first_event_helper: bool
    should_show_get_started: bool


class WorkflowView(BaseModel):
    id: str
    event_type_id: str
    trigger: Literal["before_event_start", "after_event_end"]
    offset_minutes: int
    channel: str
    subject: str
    body: str
    status: Literal["active", "inactive"]
    created_at: datetime
    updated_at: datetime


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
    return {"event_type_id": str(event_type_id) if event_type_id else None,
            "range_start": range_start, "range_end": range_end, "status": status}


@router.get("/profile-settings", response_model=ProfileSettingsView)
def get_profile(host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    return profile_settings(host, workspace_for(session, host.id))


@router.put("/profile-settings", response_model=ProfileSettingsView)
def put_profile(payload: ProfileSettingsInput, host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    return update_profile(session, host, payload.model_dump())


@router.get("/meetings", response_model=list[MeetingView])
def list_meetings(event_type_id: UUID | None = None, range_start: datetime | None = None,
                  range_end: datetime | None = None, status: str | None = None,
                  host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    filters = _meeting_filters(event_type_id, range_start, range_end, status)
    return [meeting_view(booking, event, host) for booking, event in meeting_rows(session, host.id, **filters)]


@router.get("/meetings.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def export_meetings(event_type_id: UUID | None = None, range_start: datetime | None = None,
                    range_end: datetime | None = None, status: str | None = None,
                    host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    filters = _meeting_filters(event_type_id, range_start, range_end, status)
    return Response(meetings_csv(meeting_rows(session, host.id, **filters)), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="meetings.csv"'})


@router.post("/meetings/{booking_id}/cancel", response_model=MeetingView)
def cancel_meeting(booking_id: UUID, host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
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
        select(Booking, EventType).join(EventType, Booking.event_type_id == EventType.id)
        .where(Booking.id == str(booking_id), Booking.host_id == host.id)
    ).one()
    return meeting_view(booking, event, host)


@router.put("/contacts/{contact_id}/notes", response_model=ContactView)
def put_contact_notes(contact_id: UUID, payload: ContactNotesInput, host: Host = Depends(get_current_host),
                      session: Session = Depends(get_session)):
    return update_contact_notes(session, host.id, str(contact_id), payload.notes)


@router.get("/contacts", response_model=list[ContactView])
def list_contacts(host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    return SchedulerService(session).list_contacts(host.id)


@router.get("/onboarding", response_model=SetupView)
def get_setup(host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    return setup_status(session, host)


@router.put("/onboarding/step", response_model=SetupView)
def put_step(payload: StepInput, host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    workspace_for(session, host.id).current_step_id = payload.current_step_id
    session.commit()
    return setup_status(session, host)


@router.post("/onboarding/skip", response_model=SetupView)
def skip_step(payload: SkipStepInput, host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    if payload.step_id != "calendar-usage":
        raise DomainError("Only Calendar Usage can be skipped")
    workspace_for(session, host.id).calendar_connection_skipped = True
    session.commit()
    return setup_status(session, host)


@router.put("/onboarding/role", response_model=SetupView)
def put_role(payload: RoleInput, host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    workspace_for(session, host.id).role = payload.role
    session.commit()
    return setup_status(session, host)


@router.put("/onboarding/location", response_model=SetupView)
def put_location(payload: LocationInput, host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    workspace = workspace_for(session, host.id)
    if payload.preferred_location_type == "phone_host":
        phone = (payload.phone_number or "").strip()
        if len([digit for digit in phone if digit.isdigit()]) < 7:
            raise DomainError("Enter a valid phone number")
        workspace.phone_number = phone
        workspace.preferred_location_value = None
    elif payload.preferred_location_type == "in_person":
        location = (payload.preferred_location_value or "").strip()
        if not location:
            raise DomainError("Add meeting location details")
        workspace.preferred_location_value = location
    else:
        workspace.preferred_location_value = "Google Meet"
    workspace.preferred_location_type = payload.preferred_location_type
    workspace.time_format = payload.time_format
    session.commit()
    return setup_status(session, host)


@router.post("/onboarding/complete", response_model=SetupView)
def finish_setup(host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    return complete_setup(session, host, SchedulerService(session))


@router.get("/onboarding/guide", response_model=GuideView)
def get_guide(host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    return guide_status(session, host)


@router.post("/onboarding/guide/tasks", response_model=GuideView)
def complete_guide_task(payload: GuideItemInput, host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    if payload.item_id not in TASKS:
        raise DomainError("Invalid guide task")
    return mark_guide_item(session, host, payload.item_id, "completed_at")


@router.post("/onboarding/guide/dismissals", response_model=GuideView)
def dismiss_guide_item(payload: GuideItemInput, host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    if payload.item_id not in DISMISSALS:
        raise DomainError("Invalid guide item")
    return mark_guide_item(session, host, payload.item_id, "dismissed_at")


@router.get("/workflows", response_model=list[WorkflowView])
def list_workflows(host: Host = Depends(get_current_host), session: Session = Depends(get_session)):
    return list(session.scalars(select(Workflow).where(Workflow.host_id == host.id).order_by(Workflow.created_at.desc())))
