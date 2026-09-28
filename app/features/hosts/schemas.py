"""Hosts HTTP schemas."""

from __future__ import annotations
import re
from datetime import datetime
from pydantic import BaseModel, Field, field_validator
from app.core.timezones import valid_timezone


class HostRegistration(BaseModel):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    timezone: str = Field(default="UTC", min_length=1, max_length=120)

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return valid_timezone(value)


class HostView(BaseModel):
    id: str
    username: str
    display_name: str
    email: str
    timezone: str
    welcome_message: str | None
    is_public: bool
    created_at: datetime


class HostRegistrationResult(BaseModel):
    host: HostView
    api_key: str


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
