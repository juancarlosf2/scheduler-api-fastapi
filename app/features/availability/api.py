"""Availability HTTP endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends
from app.core.dependencies import get_current_host
from app.core.http import get_service, _response
from app.features.hosts.models import Host
from app.features.availability.schemas import AvailabilityInput, AvailabilityView
from app.features.bookings.service import SchedulerService

router = APIRouter()


@router.get(
    "/v1/hosts/me/availability", response_model=AvailabilityView, tags=["availability"]
)
def get_availability(
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.get_availability(host.id))


@router.put(
    "/v1/hosts/me/availability", response_model=AvailabilityView, tags=["availability"]
)
def update_availability(
    payload: AvailabilityInput,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.update_availability(host.id, payload))
