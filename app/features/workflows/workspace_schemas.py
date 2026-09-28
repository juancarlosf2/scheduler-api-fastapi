"""Workflows host workspace HTTP schemas."""

from __future__ import annotations
from datetime import datetime
from typing import Literal
from pydantic import BaseModel


class WorkflowView(BaseModel):
    id: str
    event_type_id: str
    trigger: Literal["before_event_start", "after_event_end"]
    offset_minutes: int
    channel: str
    subject: str
    body: str
    status: Literal["active", "inactive"]
    created_at: datetime
    updated_at: datetime
