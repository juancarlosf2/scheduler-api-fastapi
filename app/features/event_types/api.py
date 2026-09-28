"""Event Types HTTP endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends, status
from app.core.dependencies import get_current_host
from app.core.http import get_service, _response
from app.features.hosts.models import Host
from app.features.event_types.schemas import (
    EventStatusUpdate,
    EventTypeInput,
    EventTypeView,
)
from app.features.bookings.service import SchedulerService

router = APIRouter()


@router.get(
    "/v1/hosts/me/event-types", response_model=list[EventTypeView], tags=["event types"]
)
def list_event_types(
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.list_event_types(host.id))


@router.post(
    "/v1/hosts/me/event-types",
    response_model=EventTypeView,
    status_code=status.HTTP_201_CREATED,
    tags=["event types"],
)
def create_event_type(
    payload: EventTypeInput,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.create_event_type(host.id, payload))


@router.post(
    "/v1/hosts/me/event-types/drafts",
    response_model=EventTypeView,
    status_code=status.HTTP_201_CREATED,
    tags=["event types"],
)
def create_event_type_draft(
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.create_draft(host.id))


@router.put(
    "/v1/hosts/me/event-types/{event_id}",
    response_model=EventTypeView,
    tags=["event types"],
)
def update_event_type(
    event_id: str,
    payload: EventTypeInput,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.update_event_type(host.id, event_id, payload))


@router.post(
    "/v1/hosts/me/event-types/{event_id}/publish",
    response_model=EventTypeView,
    tags=["event types"],
)
def publish_event_type(
    event_id: str,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.publish_event_type(host.id, event_id))


@router.patch(
    "/v1/hosts/me/event-types/{event_id}/status",
    response_model=EventTypeView,
    tags=["event types"],
)
def set_event_status(
    event_id: str,
    payload: EventStatusUpdate,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.set_event_status(host.id, event_id, payload.status))


@router.post(
    "/v1/hosts/me/event-types/{event_id}/duplicate",
    response_model=EventTypeView,
    tags=["event types"],
)
def duplicate_event_type(
    event_id: str,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.duplicate_event_type(host.id, event_id))


@router.delete(
    "/v1/hosts/me/event-types/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["event types"],
)
def delete_event_type(
    event_id: str,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    service.delete_event_type(host.id, event_id)
