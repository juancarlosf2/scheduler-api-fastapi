"""Onboarding host workspace HTTP schemas."""

from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class StepInput(BaseModel):
    current_step_id: Literal[
        "role", "calendar-usage", "availability", "meeting-location"
    ]


class SkipStepInput(BaseModel):
    step_id: Literal["role", "calendar-usage", "availability", "meeting-location"]


class RoleInput(BaseModel):
    role: Literal[
        "sales",
        "recruiting",
        "customer-success",
        "marketing",
        "consulting",
        "education",
        "finance",
        "other",
    ]


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
