"""Bookings HTTP schemas."""

from __future__ import annotations
import re
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.core.timezones import valid_timezone
from app.features.availability.schemas import AvailabilityIntervalView


class BookingAction(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class BookingReschedule(BookingAction):
    start_at: datetime
    invitee_timezone: str = Field(min_length=1, max_length=120)

    @field_validator("invitee_timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return valid_timezone(value)

    @field_validator("start_at")
    @classmethod
    def check_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start_at must include a timezone offset")
        return value


class PublicEventSummary(BaseModel):
    id: str
    name: str
    slug: str
    color: str
    description: str | None
    duration_minutes: int
    location_type: str


class PublicProfileView(BaseModel):
    username: str
    display_name: str
    welcome_message: str | None
    event_types: list[PublicEventSummary]


class PublicEventHostView(BaseModel):
    username: str
    display_name: str


class PublicScheduleView(BaseModel):
    timezone: str
    intervals: list[AvailabilityIntervalView]


class InviteeFieldView(BaseModel):
    id: str
    label: str
    field_type: str
    is_required: bool
    position: int


class PublicEventView(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None
    duration_minutes: int
    location_type: str
    location_value: str | None
    booking_window_days: int
    minimum_notice_minutes: int
    slot_interval_minutes: int
    confirmation_message: str | None
    profile: PublicEventHostView
    schedule: PublicScheduleView
    invitee_fields: list[InviteeFieldView]


class BookingAnswerInput(BaseModel):
    field_id: str = Field(min_length=1, max_length=36)
    value: str = Field(max_length=2000)


class BookingInput(BaseModel):
    start_at: datetime
    invitee_name: str = Field(min_length=1, max_length=200)
    invitee_email: str = Field(min_length=3, max_length=320)
    invitee_phone: str | None = Field(default=None, max_length=80)
    invitee_timezone: str
    answers: list[BookingAnswerInput] = Field(default_factory=list, max_length=64)
    prep_notes: str | None = Field(default=None, max_length=2000)

    @field_validator("invitee_timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return valid_timezone(value)

    @field_validator("invitee_email")
    @classmethod
    def check_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Enter a valid email")
        return normalized

    @field_validator("start_at")
    @classmethod
    def check_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start_at must include a timezone offset")
        return value


class RescheduleInput(BaseModel):
    start_at: datetime
    invitee_timezone: str

    @field_validator("invitee_timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return valid_timezone(value)

    @field_validator("start_at")
    @classmethod
    def check_start(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("start_at must include a timezone offset")
        return value


class BookingView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type_id: str
    host_id: str
    invitee_name: str
    invitee_email: str
    invitee_phone: str | None
    invitee_timezone: str
    start_at: datetime
    end_at: datetime
    status: str
    external_sync_status: str
    meeting_join_url: str | None


class BookingActionResult(BaseModel):
    booking: BookingView
    cancel_token: str
    reschedule_token: str
