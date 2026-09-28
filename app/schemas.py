"""Validated request and response shapes used by the scheduling service."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def valid_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Timezone is invalid") from error
    return value


class HostCreate(BaseModel):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    timezone: str = "UTC"
    api_key_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return valid_timezone(value)

    @field_validator("email")
    @classmethod
    def check_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("Enter a valid email")
        return normalized


class IntervalInput(BaseModel):
    weekday: int | None = Field(default=None, ge=0, le=6)
    date: str | None = None
    start_minute: int = Field(ge=0, lt=1440)
    end_minute: int = Field(gt=0, le=1440)
    is_available: bool = True

    @model_validator(mode="after")
    def check_interval(self) -> IntervalInput:
        if (self.weekday is None) == (self.date is None):
            raise ValueError("Availability intervals must target a weekday or a date")
        if self.date is not None:
            from datetime import date
            try:
                if date.fromisoformat(self.date).isoformat() != self.date:
                    raise ValueError
            except ValueError as error:
                raise ValueError("Date must be a real YYYY-MM-DD calendar date") from error
        if self.start_minute >= self.end_minute:
            raise ValueError("Availability interval times are invalid")
        return self


class AvailabilityInput(BaseModel):
    name: str = Field(default="Default hours", min_length=1)
    timezone: str
    intervals: list[IntervalInput]

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return valid_timezone(value)


class EventTypeInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = None
    color: str = "#2563eb"
    description: str | None = None
    duration_minutes: int = Field(default=30, ge=5, le=480)
    location_type: Literal["none", "google_meet", "phone_invitee", "phone_host", "in_person", "custom", "video"] = "none"
    location_value: str | None = None
    booking_window_days: int = Field(default=60, ge=1, le=365)
    minimum_notice_minutes: int = Field(default=1440, ge=0)
    slot_interval_minutes: int = Field(default=30, ge=5)
    confirmation_message: str | None = None
    status: Literal["draft", "active"] = "draft"


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


class Slot(BaseModel):
    start_at: datetime
    end_at: datetime
    local_date: str


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
