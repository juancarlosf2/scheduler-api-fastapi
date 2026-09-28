"""Authenticated calendar settings endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, HttpUrl
from sqlalchemy.orm import Session

from app.calendar import CalendarService
from app.dependencies import get_current_host, get_session
from app.integrations.calendar import CalendarError, GoogleCalendarAdapter
from app.models import Host
from app.service import DomainError


router = APIRouter(prefix="/v1/hosts/me/calendar", tags=["calendar"])


class ConnectLinkInput(BaseModel):
    callback_url: HttpUrl | None = None


class ConnectLinkView(BaseModel):
    redirect_url: str | None
    status: str | None


class CalendarPreferencesInput(BaseModel):
    add_events: bool
    check_conflicts: bool


class ExternalCalendarView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    calendar_connection_id: str
    provider_calendar_id: str
    name: str
    time_zone: str | None
    is_primary: bool
    check_conflicts: bool
    add_events: bool
    created_at: datetime
    updated_at: datetime


class CalendarSettingsView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    host_id: str
    provider: str
    toolkit_slug: str
    connected_account_id: str
    provider_account_email: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    calendars: list[ExternalCalendarView]


def get_calendar_adapter() -> GoogleCalendarAdapter:
    try:
        return GoogleCalendarAdapter()
    except CalendarError as error:
        raise DomainError(str(error), 503) from error


def get_calendar_service(
    session: Session = Depends(get_session),
) -> CalendarService:
    return CalendarService(session)


@router.get("", response_model=CalendarSettingsView | None)
def get_calendar_settings(
    host: Host = Depends(get_current_host), service: CalendarService = Depends(get_calendar_service)
):
    return service.settings(host.id)


@router.post("/connect-link", response_model=ConnectLinkView)
def create_google_calendar_connect_link(
    payload: ConnectLinkInput,
    host: Host = Depends(get_current_host),
    service: CalendarService = Depends(get_calendar_service),
    adapter: GoogleCalendarAdapter = Depends(get_calendar_adapter),
):
    return service.create_connect_link(host.id, adapter, str(payload.callback_url) if payload.callback_url else None)


@router.post("/sync", response_model=CalendarSettingsView)
def sync_google_calendars(
    host: Host = Depends(get_current_host),
    service: CalendarService = Depends(get_calendar_service),
    adapter: GoogleCalendarAdapter = Depends(get_calendar_adapter),
):
    return service.sync(host.id, adapter)


@router.put("/calendars/{calendar_id}/preferences", response_model=ExternalCalendarView)
def update_external_calendar_preferences(
    calendar_id: str,
    payload: CalendarPreferencesInput,
    host: Host = Depends(get_current_host),
    service: CalendarService = Depends(get_calendar_service),
):
    return service.update_preferences(
        host.id, calendar_id, check_conflicts=payload.check_conflicts, add_events=payload.add_events
    )
