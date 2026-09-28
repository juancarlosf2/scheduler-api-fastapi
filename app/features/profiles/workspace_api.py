"""Profiles host workspace endpoints."""

from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_host, get_session
from app.features.hosts.models import Host
from app.features.profiles.service import (
    profile_settings,
    update_profile,
    workspace_for,
)
from app.features.profiles.workspace_schemas import (
    ProfileSettingsInput,
    ProfileSettingsView,
)

router = APIRouter(prefix="/v1/hosts/me", tags=["host workspace"])


@router.get("/profile-settings", response_model=ProfileSettingsView)
def get_profile(
    host: Host = Depends(get_current_host), session: Session = Depends(get_session)
):
    return profile_settings(host, workspace_for(session, host.id))


@router.put("/profile-settings", response_model=ProfileSettingsView)
def put_profile(
    payload: ProfileSettingsInput,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    return update_profile(session, host, payload.model_dump())
