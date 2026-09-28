"""Event Types HTTP schemas."""

from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field


class EventStatusUpdate(BaseModel):
    status: Literal["draft", "active"]


class EventTypeView(BaseModel):
    id: str
    host_id: str
    schedule_id: str
    name: str
    slug: str
    color: str
    description: str | None
    duration_minutes: int
    location_type: str
    location_value: str | None
    status: Literal["draft", "active"]
    booking_window_days: int
    minimum_notice_minutes: int
    slot_interval_minutes: int
    confirmation_message: str | None
    created_at: datetime
    updated_at: datetime


class EventTypeInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = None
    color: str = "#2563eb"
    description: str | None = None
    duration_minutes: int = Field(default=30, ge=5, le=480)
    location_type: Literal[
        "none",
        "google_meet",
        "phone_invitee",
        "phone_host",
        "in_person",
        "custom",
        "video",
    ] = "none"
    location_value: str | None = None
    booking_window_days: int = Field(default=60, ge=1, le=365)
    minimum_notice_minutes: int = Field(default=1440, ge=0)
    slot_interval_minutes: int = Field(default=30, ge=5)
    confirmation_message: str | None = None
    status: Literal["draft", "active"] = "draft"
