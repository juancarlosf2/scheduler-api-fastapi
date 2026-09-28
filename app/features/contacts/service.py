"""Contacts scheduling operations."""

from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.features.contacts.models import Contact
from app.core.errors import DomainError


class ContactMixin:
    def list_contacts(self, host_id: str) -> list[Contact]:
        self.get_host(host_id)
        return list(
            self.session.scalars(
                select(Contact).where(Contact.host_id == host_id).order_by(Contact.name)
            )
        )


def update_contact_notes(
    session: Session, host_id: str, contact_id: str, notes: str | None
) -> Contact:
    contact = session.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.host_id == host_id)
    )
    if contact is None:
        raise DomainError("Contact not found", 404)
    contact.notes = (notes or "").strip() or None
    session.commit()
    return contact
