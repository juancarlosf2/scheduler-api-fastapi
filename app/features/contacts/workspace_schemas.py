"""Contacts host workspace HTTP schemas."""

from __future__ import annotations
from pydantic import BaseModel, Field


class ContactNotesInput(BaseModel):
    notes: str | None = Field(max_length=2000)


class ContactView(BaseModel):
    id: str
    host_id: str
    name: str
    email: str
    notes: str | None
