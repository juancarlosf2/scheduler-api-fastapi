"""Contacts HTTP schemas."""

from __future__ import annotations
from pydantic import BaseModel


class ContactView(BaseModel):
    id: str
    host_id: str
    name: str
    email: str
    notes: str | None
