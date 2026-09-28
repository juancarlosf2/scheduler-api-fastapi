"""Response projection and service construction for HTTP routes."""

from __future__ import annotations
import os
from typing import Any
from fastapi import Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.core.dependencies import get_session
from app.features.calendars.providers.composio import (
    CalendarError,
    GoogleCalendarAdapter,
)
from app.features.bookings.service import SchedulerService

_PRIVATE_FIELDS = {
    "api_key_hash",
    "workos_user_id",
    "cancel_token_hash",
    "reschedule_token_hash",
}


def _public(value: Any) -> Any:
    """Convert service results without leaking stored credentials or token hashes."""
    if isinstance(value, BaseModel):
        return _public(value.model_dump())
    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if key not in _PRIVATE_FIELDS
        }
    if isinstance(value, (list, tuple)):
        return [_public(item) for item in value]
    if hasattr(value, "__mapper__"):
        return {
            column.key: _public(getattr(value, column.key))
            for column in value.__mapper__.column_attrs
            if column.key not in _PRIVATE_FIELDS
        }
    return value


def _response(value: Any) -> Any:
    return jsonable_encoder(_public(value))


def get_service(session: Session = Depends(get_session)) -> SchedulerService:
    adapter = None
    if os.getenv("COMPOSIO_API_KEY"):
        try:
            adapter = GoogleCalendarAdapter()
        except CalendarError:
            raise HTTPException(
                status_code=503, detail="Google Calendar is not configured"
            ) from None
    return SchedulerService(session, calendar_adapter=adapter)
