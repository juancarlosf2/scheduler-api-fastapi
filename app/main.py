"""HTTP API for the standalone scheduler starter."""

import os
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import auth_mode, create_api_key, hash_api_key
from app.dependencies import get_current_host, get_session, session_factory
from app.integrations.calendar import CalendarError, GoogleCalendarAdapter
from app.lifecycle import deliver_booking_notifications
from app.models import Host
from app.schemas import (
    AvailabilityInput,
    BookingActionResult,
    BookingInput,
    BookingView,
    EventTypeInput,
    HostCreate,
    RescheduleInput,
    Slot,
    valid_timezone,
)
from app.service import DomainError, SchedulerService


app = FastAPI(
    title="Scheduling API Starter",
    description="Self-hostable scheduling API with host-owned event types and public booking.",
    version="0.1.0",
)


class HostRegistration(BaseModel):
    username: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    timezone: str = Field(default="UTC", min_length=1, max_length=120)

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        return valid_timezone(value)


class EventStatusUpdate(BaseModel):
    status: Literal["draft", "active"]


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


class CalendarCreateReconciliation(BaseModel):
    confirmed_absent: bool = False
    reconciled_event_id: str | None = Field(default=None, min_length=1, max_length=255)


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


class ContactView(BaseModel):
    id: str
    host_id: str
    name: str
    email: str
    notes: str | None


_PRIVATE_FIELDS = {"api_key_hash", "workos_user_id", "cancel_token_hash", "reschedule_token_hash"}


def _public(value: Any) -> Any:
    """Convert service results without leaking stored credentials or token hashes."""
    if isinstance(value, BaseModel):
        return _public(value.model_dump())
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items() if key not in _PRIVATE_FIELDS}
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
            raise HTTPException(status_code=503, detail="Google Calendar is not configured") from None
    return SchedulerService(session, calendar_adapter=adapter)


@app.exception_handler(DomainError)
async def domain_error_handler(_request, error: DomainError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=error.status_code, content={"detail": str(error)})


@app.get("/health", tags=["health"])
def health():
    try:
        with session_factory() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/v1/hosts", response_model=HostRegistrationResult, status_code=status.HTTP_201_CREATED, tags=["hosts"])
def register_host(payload: HostRegistration, service: SchedulerService = Depends(get_service)):
    try:
        mode = auth_mode()
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Authentication is not configured") from None
    if mode != "local":
        raise HTTPException(status_code=403, detail="Host registration is managed by WorkOS")
    api_key = create_api_key()
    host = service.create_host(
        HostCreate(**payload.model_dump(), api_key_hash=hash_api_key(api_key))
    )
    return {"host": _response(host), "api_key": api_key}


@app.get("/v1/hosts/me", response_model=HostView, tags=["hosts"])
def get_my_host(host: Host = Depends(get_current_host)):
    return _response(host)


@app.post("/v1/hosts/me/meetings/{booking_id}/retry-calendar-sync", response_model=BookingView, tags=["meetings"])
def retry_calendar_sync(
    booking_id: str,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.retry_calendar_sync(host.id, booking_id))


@app.post("/v1/hosts/me/meetings/{booking_id}/reconcile-calendar-create", response_model=BookingView, tags=["meetings"])
def reconcile_calendar_create(
    booking_id: str,
    payload: CalendarCreateReconciliation,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    """Resolve an ambiguous create only after checking the provider calendar."""
    return _response(service.reconcile_calendar_create(
        host.id, booking_id,
        confirmed_absent=payload.confirmed_absent,
        reconciled_event_id=payload.reconciled_event_id,
    ))


@app.get("/v1/hosts/me/availability", response_model=AvailabilityView, tags=["availability"])
def get_availability(
    host: Host = Depends(get_current_host), service: SchedulerService = Depends(get_service)
):
    return _response(service.get_availability(host.id))


@app.put("/v1/hosts/me/availability", response_model=AvailabilityView, tags=["availability"])
def update_availability(
    payload: AvailabilityInput,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.update_availability(host.id, payload))


@app.get("/v1/hosts/me/event-types", response_model=list[EventTypeView], tags=["event types"])
def list_event_types(
    host: Host = Depends(get_current_host), service: SchedulerService = Depends(get_service)
):
    return _response(service.list_event_types(host.id))


@app.post("/v1/hosts/me/event-types", response_model=EventTypeView, status_code=status.HTTP_201_CREATED, tags=["event types"])
def create_event_type(
    payload: EventTypeInput,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.create_event_type(host.id, payload))


@app.post("/v1/hosts/me/event-types/drafts", response_model=EventTypeView, status_code=status.HTTP_201_CREATED, tags=["event types"])
def create_event_type_draft(
    host: Host = Depends(get_current_host), service: SchedulerService = Depends(get_service)
):
    return _response(service.create_draft(host.id))


@app.put("/v1/hosts/me/event-types/{event_id}", response_model=EventTypeView, tags=["event types"])
def update_event_type(
    event_id: str,
    payload: EventTypeInput,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.update_event_type(host.id, event_id, payload))


@app.post("/v1/hosts/me/event-types/{event_id}/publish", response_model=EventTypeView, tags=["event types"])
def publish_event_type(
    event_id: str,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.publish_event_type(host.id, event_id))


@app.patch("/v1/hosts/me/event-types/{event_id}/status", response_model=EventTypeView, tags=["event types"])
def set_event_status(
    event_id: str,
    payload: EventStatusUpdate,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.set_event_status(host.id, event_id, payload.status))


@app.post("/v1/hosts/me/event-types/{event_id}/duplicate", response_model=EventTypeView, tags=["event types"])
def duplicate_event_type(
    event_id: str,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.duplicate_event_type(host.id, event_id))


@app.delete("/v1/hosts/me/event-types/{event_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["event types"])
def delete_event_type(
    event_id: str,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    service.delete_event_type(host.id, event_id)


@app.get("/v1/public/{username}", response_model=PublicProfileView, tags=["public booking"])
def public_profile(username: str, service: SchedulerService = Depends(get_service)):
    return _response(service.get_public_profile(username))


@app.get("/v1/public/{username}/events/{slug}", response_model=PublicEventView, tags=["public booking"])
def public_event(username: str, slug: str, service: SchedulerService = Depends(get_service)):
    return _response(service.get_public_event(username, slug))


@app.get("/v1/public/{username}/events/{slug}/slots", response_model=list[Slot], tags=["public booking"])
def public_slots(
    username: str,
    slug: str,
    range_start_date: date = Query(),
    range_end_date: date = Query(),
    service: SchedulerService = Depends(get_service),
):
    if range_start_date > range_end_date:
        raise HTTPException(status_code=422, detail="Date range end must be on or after start")
    return _response(service.get_slots(username, slug, range_start_date, range_end_date))


@app.post("/v1/public/{username}/events/{slug}/bookings", response_model=BookingActionResult, status_code=status.HTTP_201_CREATED, tags=["public booking"])
def create_booking(
    username: str,
    slug: str,
    payload: BookingInput,
    service: SchedulerService = Depends(get_service),
):
    result = _response(service.create_booking(username, slug, payload))
    deliver_booking_notifications(service.session, result["booking"]["id"])
    return result


@app.post("/v1/bookings/{booking_id}/cancel", response_model=BookingView, tags=["public booking"])
def cancel_booking(
    booking_id: str,
    payload: BookingAction,
    service: SchedulerService = Depends(get_service),
):
    result = _response(service.cancel_booking(booking_id, payload.token))
    deliver_booking_notifications(service.session, result["id"])
    return result


@app.post("/v1/bookings/{booking_id}/reschedule", response_model=BookingActionResult, tags=["public booking"])
def reschedule_booking(
    booking_id: str,
    payload: BookingReschedule,
    service: SchedulerService = Depends(get_service),
):
    result = _response(
        service.reschedule_booking(
            booking_id,
            payload.token,
            RescheduleInput(start_at=payload.start_at, invitee_timezone=payload.invitee_timezone),
        )
    )
    deliver_booking_notifications(service.session, result["booking"]["id"])
    return result


from app.calendar_routes import router as calendar_router
from app.notification_routes import router as notification_router
from app.workos_auth import router as workos_auth_router
from app.workspace_routes import router as workspace_router

app.include_router(calendar_router)
app.include_router(workspace_router)
app.include_router(notification_router)
app.include_router(workos_auth_router)
