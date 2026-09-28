"""Availability HTTP schemas."""

from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field, field_validator, model_validator
from app.core.timezones import valid_timezone


class AvailabilityIntervalView(BaseModel):
    id: str | None = None
    weekday: int | None
    date: str | None
    start_minute: int
    end_minute: int
    is_available: bool


class AvailabilityView(BaseModel):
    id: str
    host_id: str
    name: str
    timezone: str
    intervals: list[AvailabilityIntervalView]


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
                raise ValueError(
                    "Date must be a real YYYY-MM-DD calendar date"
                ) from error
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


class Slot(BaseModel):
    start_at: datetime
    end_at: datetime
    local_date: str
