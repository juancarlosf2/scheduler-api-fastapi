"""Contacts host workspace endpoints."""

from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.dependencies import get_current_host, get_session
from app.features.hosts.models import Host
from app.features.contacts.service import update_contact_notes
from app.features.bookings.service import SchedulerService
from app.features.contacts.workspace_schemas import ContactNotesInput, ContactView

router = APIRouter(prefix="/v1/hosts/me", tags=["host workspace"])


@router.put("/contacts/{contact_id}/notes", response_model=ContactView)
def put_contact_notes(
    contact_id: UUID,
    payload: ContactNotesInput,
    host: Host = Depends(get_current_host),
    session: Session = Depends(get_session),
):
    return update_contact_notes(session, host.id, str(contact_id), payload.notes)


@router.get("/contacts", response_model=list[ContactView])
def list_contacts(
    host: Host = Depends(get_current_host), session: Session = Depends(get_session)
):
    return SchedulerService(session).list_contacts(host.id)
