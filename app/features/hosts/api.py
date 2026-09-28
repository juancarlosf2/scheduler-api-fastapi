"""Hosts HTTP endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, status
from app.core.auth import auth_mode, create_api_key, hash_api_key
from app.core.dependencies import get_current_host
from app.core.http import get_service, _response
from app.features.hosts.models import Host
from app.features.hosts.schemas import (
    HostCreate,
    HostRegistration,
    HostView,
    HostRegistrationResult,
)
from app.features.bookings.service import SchedulerService

router = APIRouter()


@router.post(
    "/v1/hosts",
    response_model=HostRegistrationResult,
    status_code=status.HTTP_201_CREATED,
    tags=["hosts"],
)
def register_host(
    payload: HostRegistration, service: SchedulerService = Depends(get_service)
):
    try:
        mode = auth_mode()
    except RuntimeError:
        raise HTTPException(
            status_code=503, detail="Authentication is not configured"
        ) from None
    if mode != "local":
        raise HTTPException(
            status_code=403, detail="Host registration is managed by WorkOS"
        )
    api_key = create_api_key()
    host = service.create_host(
        HostCreate(**payload.model_dump(), api_key_hash=hash_api_key(api_key))
    )
    return {"host": _response(host), "api_key": api_key}


@router.get("/v1/hosts/me", response_model=HostView, tags=["hosts"])
def get_my_host(host: Host = Depends(get_current_host)):
    return _response(host)
