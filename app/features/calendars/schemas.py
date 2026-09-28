"""Calendars HTTP schemas."""

from __future__ import annotations
from pydantic import BaseModel, Field


class CalendarCreateReconciliation(BaseModel):
    confirmed_absent: bool = False
    reconciled_event_id: str | None = Field(default=None, min_length=1, max_length=255)
