"""Profiles host workspace HTTP schemas."""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from app.core.timezones import valid_timezone


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
