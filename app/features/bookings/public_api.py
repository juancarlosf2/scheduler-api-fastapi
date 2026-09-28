"""Bookings HTTP endpoints."""

from __future__ import annotations
from datetime import date
from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.core.http import get_service, _response
from app.features.notifications.lifecycle import deliver_booking_notifications
from app.features.availability.schemas import Slot
from app.features.bookings.schemas import (
    BookingAction,
    BookingReschedule,
    PublicProfileView,
    PublicEventView,
    BookingActionResult,
    BookingInput,
    BookingView,
    RescheduleInput,
)
from app.features.bookings.service import SchedulerService

router = APIRouter()


@router.get(
    "/v1/public/{username}", response_model=PublicProfileView, tags=["public booking"]
)
def public_profile(username: str, service: SchedulerService = Depends(get_service)):
    return _response(service.get_public_profile(username))


@router.get(
    "/v1/public/{username}/events/{slug}",
    response_model=PublicEventView,
    tags=["public booking"],
)
def public_event(
    username: str, slug: str, service: SchedulerService = Depends(get_service)
):
    return _response(service.get_public_event(username, slug))


@router.get(
    "/v1/public/{username}/events/{slug}/slots",
    response_model=list[Slot],
    tags=["public booking"],
)
def public_slots(
    username: str,
    slug: str,
    range_start_date: date = Query(),
    range_end_date: date = Query(),
    service: SchedulerService = Depends(get_service),
):
    if range_start_date > range_end_date:
        raise HTTPException(
            status_code=422, detail="Date range end must be on or after start"
        )
    return _response(
        service.get_slots(username, slug, range_start_date, range_end_date)
    )


@router.post(
    "/v1/public/{username}/events/{slug}/bookings",
    response_model=BookingActionResult,
    status_code=status.HTTP_201_CREATED,
    tags=["public booking"],
)
def create_booking(
    username: str,
    slug: str,
    payload: BookingInput,
    service: SchedulerService = Depends(get_service),
):
    result = _response(service.create_booking(username, slug, payload))
    deliver_booking_notifications(service.session, result["booking"]["id"])
    return result


@router.post(
    "/v1/bookings/{booking_id}/cancel",
    response_model=BookingView,
    tags=["public booking"],
)
def cancel_booking(
    booking_id: str,
    payload: BookingAction,
    service: SchedulerService = Depends(get_service),
):
    result = _response(service.cancel_booking(booking_id, payload.token))
    deliver_booking_notifications(service.session, result["id"])
    return result


@router.post(
    "/v1/bookings/{booking_id}/reschedule",
    response_model=BookingActionResult,
    tags=["public booking"],
)
def reschedule_booking(
    booking_id: str,
    payload: BookingReschedule,
    service: SchedulerService = Depends(get_service),
):
    result = _response(
        service.reschedule_booking(
            booking_id,
            payload.token,
            RescheduleInput(
                start_at=payload.start_at, invitee_timezone=payload.invitee_timezone
            ),
        )
    )
    deliver_booking_notifications(service.session, result["booking"]["id"])
    return result
