"""Onboarding host workspace endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_host, get_session
from app.core.errors import DomainError
from app.features.hosts.models import Host
from app.features.profiles.service import workspace_for
from app.features.bookings.service import SchedulerService
from app.features.onboarding.service import (
    TASKS,
    DISMISSALS,
    complete_setup,
    guide_status,
    mark_guide_item,
    setup_status,
)
from app.features.onboarding.workspace_schemas import (
    StepInput,
    SkipStepInput,
    RoleInput,
    LocationInput,
    GuideItemInput,
    SetupView,
    GuideView,
)

router = APIRouter(prefix="/v1/hosts/me", tags=["host workspace"])


@router.get("/onboarding", response_model=SetupView)
def get_setup(
    host: Host = Depends(get_current_host), session: Session = Depends(get_session)
):
    return setup_status(session, host)


@router.put("/onboarding/step", response_model=SetupView)
def put_step(
    payload: StepInput,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    workspace_for(session, host.id).current_step_id = payload.current_step_id
    session.commit()
    return setup_status(session, host)


@router.post("/onboarding/skip", response_model=SetupView)
def skip_step(
    payload: SkipStepInput,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    if payload.step_id != "calendar-usage":
        raise DomainError("Only Calendar Usage can be skipped")
    workspace_for(session, host.id).calendar_connection_skipped = True
    session.commit()
    return setup_status(session, host)


@router.put("/onboarding/role", response_model=SetupView)
def put_role(
    payload: RoleInput,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    workspace_for(session, host.id).role = payload.role
    session.commit()
    return setup_status(session, host)


@router.put("/onboarding/location", response_model=SetupView)
def put_location(
    payload: LocationInput,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    workspace = workspace_for(session, host.id)
    if payload.preferred_location_type == "phone_host":
        phone = (payload.phone_number or "").strip()
        if len([digit for digit in phone if digit.isdigit()]) < 7:
            raise DomainError("Enter a valid phone number")
        workspace.phone_number = phone
        workspace.preferred_location_value = None
    elif payload.preferred_location_type == "in_person":
        location = (payload.preferred_location_value or "").strip()
        if not location:
            raise DomainError("Add meeting location details")
        workspace.preferred_location_value = location
    else:
        workspace.preferred_location_value = "Google Meet"
    workspace.preferred_location_type = payload.preferred_location_type
    workspace.time_format = payload.time_format
    session.commit()
    return setup_status(session, host)


@router.post("/onboarding/complete", response_model=SetupView)
def finish_setup(
    host: Host = Depends(get_current_host), session: Session = Depends(get_session)
):
    return complete_setup(session, host, SchedulerService(session))


@router.get("/onboarding/guide", response_model=GuideView)
def get_guide(
    host: Host = Depends(get_current_host), session: Session = Depends(get_session)
):
    return guide_status(session, host)


@router.post("/onboarding/guide/tasks", response_model=GuideView)
def complete_guide_task(
    payload: GuideItemInput,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    if payload.item_id not in TASKS:
        raise DomainError("Invalid guide task")
    return mark_guide_item(session, host, payload.item_id, "completed_at")


@router.post("/onboarding/guide/dismissals", response_model=GuideView)
def dismiss_guide_item(
    payload: GuideItemInput,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    if payload.item_id not in DISMISSALS:
        raise DomainError("Invalid guide item")
    return mark_guide_item(session, host, payload.item_id, "dismissed_at")
