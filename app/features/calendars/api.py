"""Calendars HTTP endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends
from app.core.dependencies import get_current_host
from app.core.http import get_service, _response
from app.features.hosts.models import Host
from app.features.bookings.schemas import BookingView
from app.features.calendars.schemas import CalendarCreateReconciliation
from app.features.bookings.service import SchedulerService

router = APIRouter()


@router.post(
    "/v1/hosts/me/meetings/{booking_id}/retry-calendar-sync",
    response_model=BookingView,
    tags=["meetings"],
)
def retry_calendar_sync(
    booking_id: str,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    return _response(service.retry_calendar_sync(host.id, booking_id))


@router.post(
    "/v1/hosts/me/meetings/{booking_id}/reconcile-calendar-create",
    response_model=BookingView,
    tags=["meetings"],
)
def reconcile_calendar_create(
    booking_id: str,
    payload: CalendarCreateReconciliation,
    host: Host = Depends(get_current_host),
    service: SchedulerService = Depends(get_service),
):
    """Resolve an ambiguous create only after checking the provider calendar."""
    return _response(
        service.reconcile_calendar_create(
            host.id,
            booking_id,
            confirmed_absent=payload.confirmed_absent,
            reconciled_event_id=payload.reconciled_event_id,
        )
    )
