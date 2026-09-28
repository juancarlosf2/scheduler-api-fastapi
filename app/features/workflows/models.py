"""Workflows persistence."""

from __future__ import annotations
from datetime import datetime
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.model import Base
from app.core.model import new_id
from app.core.model import utc_now


class Workflow(Base):
    """Stored workflow definitions; this starter does not send notifications."""

    __tablename__ = "workflows"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('before_event_start', 'after_event_end')",
            name="workflow_trigger",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="workflow_status"),
        CheckConstraint("offset_minutes >= 0", name="workflow_offset"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    host_id: Mapped[str] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"), nullable=False
    )
    event_type_id: Mapped[str] = mapped_column(
        ForeignKey("event_types.id", ondelete="CASCADE"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)
    offset_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    channel: Mapped[str] = mapped_column(String(20), nullable=False, default="email")
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
